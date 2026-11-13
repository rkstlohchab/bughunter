pub mod ce_core;
pub mod ce_mcp;
pub mod ce_policy;
mod mapping_receipt;
pub mod mac_permissions;
pub mod execution_control;
pub mod mac_tool_control;
pub mod mac_desktop;

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::Manager;

type Reply = Result<Value, String>;
type Pending = Arc<Mutex<HashMap<u64, mpsc::Sender<Reply>>>>;

fn backend_python_candidates(root: &std::path::Path, windows: bool) -> Vec<PathBuf> {
    let relative = if windows { ".venv/Scripts/python.exe" } else { ".venv/bin/python" };
    vec![root.join("backend").join(relative), root.join(relative)]
}

fn backend_search_path() -> Result<std::ffi::OsString, String> {
    let inherited = std::env::var_os("PATH").unwrap_or_default();
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"));
    let mut paths = Vec::new();
    if cfg!(target_os = "windows") {
        if let Some(appdata) = std::env::var_os("APPDATA") { paths.push(PathBuf::from(appdata).join("npm")); }
    } else {
        if let Some(home) = home { paths.push(PathBuf::from(home).join(".local/bin")); }
        if cfg!(target_os = "macos") { paths.push(PathBuf::from("/opt/homebrew/bin")); }
        paths.push(PathBuf::from("/usr/local/bin"));
    }
    paths.extend(std::env::split_paths(&inherited));
    std::env::join_paths(paths).map_err(|e| format!("Could not prepare the backend tool path: {e}"))
}

struct Process {
    child: Child,
    stdin: Option<ChildStdin>,
    pending: Pending,
    alive: Arc<AtomicBool>,
}

fn reject_pending(pending: &Pending, message: &str) {
    if let Ok(mut entries) = pending.lock() {
        for (_, sender) in entries.drain() {
            let _ = sender.send(Err(message.to_string()));
        }
    }
}

impl Process {
    fn from_command(mut command: Command) -> Result<Self, String> {
        command.env("PYTHONUNBUFFERED", "1");
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|e| format!("Could not start the local backend: {e}"))?;
        let stdin = child.stdin.take().ok_or("Backend input is unavailable")?;
        let stdout = child.stdout.take().ok_or("Backend output is unavailable")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let alive = Arc::new(AtomicBool::new(true));
        let reader_pending = pending.clone();
        let reader_alive = alive.clone();
        // Per-process maps prevent a dying reader from rejecting a replacement's calls.
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(response) = serde_json::from_str::<Value>(&line) else {
                    reject_pending(&reader_pending, "The backend returned an invalid response.");
                    break;
                };
                let Some(id) = response.get("id").and_then(Value::as_u64) else { continue };
                let result = if let Some(error) = response.get("error") {
                    Err(error.get("message").and_then(Value::as_str).unwrap_or("Backend operation failed").to_string())
                } else {
                    Ok(response.get("result").cloned().unwrap_or(Value::Null))
                };
                if let Ok(mut entries) = reader_pending.lock() {
                    if let Some(sender) = entries.remove(&id) { let _ = sender.send(result); }
                }
            }
            reader_alive.store(false, Ordering::Release);
            reject_pending(&reader_pending, "The local backend stopped. Inspect action history before retrying a write.");
        });
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    eprintln!("[workbench] {line}");
                }
            });
        }
        Ok(Self { child, stdin: Some(stdin), pending, alive })
    }

    fn spawn() -> Result<Self, String> {
        let mut command;
        if cfg!(debug_assertions) {
            let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().ok_or("Unable to locate the project root")?.to_path_buf();
            let backend = root.join("backend");
            let candidates = backend_python_candidates(&root, cfg!(target_os = "windows"));
            let python = candidates.iter().find(|p| p.exists()).ok_or("Python environment missing. Run the setup script to install backend dependencies.")?;
            command = Command::new(python);
            command.args(["-m", "bughunter", "--stdio"]).current_dir(backend);
        } else {
            let executable = std::env::current_exe().map_err(|e| e.to_string())?;
            let directory = executable.parent().ok_or("Unable to locate application directory")?;
            let sidecar = directory.join(if cfg!(target_os = "windows") { "bughunter-backend.exe" } else { "bughunter-backend" });
            if !sidecar.exists() { return Err("Packaged Python backend is missing. Rebuild the application bundle.".into()); }
            command = Command::new(sidecar);
            command.arg("--stdio");
        }
        command.env("PATH", backend_search_path()?);
        Self::from_command(command)
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        // EOF lets the backend cancel its runs and close managed subprocesses.
        self.stdin.take();
        reject_pending(&self.pending, "The desktop connection closed. Pending writes were not retried.");
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) { return; }
            std::thread::sleep(Duration::from_millis(30));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct Backend {
    process: Mutex<Option<Process>>,
    sequence: AtomicU64,
    stopping: AtomicBool,
}

impl Backend {
    fn request(&self, method: String, params: Value) -> Reply {
        if self.stopping.load(Ordering::Acquire) { return Err("The desktop app is closing.".into()); }
        let id = self.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let (sender, receiver) = mpsc::channel();
        let pending;
        {
            let mut guard = self.process.lock().map_err(|_| "Backend connection lock failed")?;
            let restart = match guard.as_mut() {
                Some(p) => !p.alive.load(Ordering::Acquire) || p.child.try_wait().map_err(|e| e.to_string())?.is_some(),
                None => true,
            };
            if restart { *guard = Some(Process::spawn()?); }
            let process = guard.as_mut().ok_or("Backend unavailable")?;
            pending = process.pending.clone();
            pending.lock().map_err(|_| "Backend pending-call lock failed")?.insert(id, sender);
            let request = json!({"id": id, "method": method, "params": params});
            let send_result = match process.stdin.as_mut() {
                Some(stdin) => writeln!(stdin, "{request}").and_then(|_| stdin.flush()),
                None => Err(std::io::Error::new(std::io::ErrorKind::BrokenPipe, "Backend closed")),
            };
            if let Err(error) = send_result {
                if let Ok(mut entries) = pending.lock() { entries.remove(&id); }
                *guard = None;
                return Err(format!("Backend input failed: {error}"));
            }
        }
        // Release the writer lock before waiting so stop/status can overtake slow calls.
        match receiver.recv_timeout(Duration::from_secs(180)) {
            Ok(result) => result,
            Err(error) => {
                if let Ok(mut entries) = pending.lock() { entries.remove(&id); }
                Err(format!("Backend response unavailable ({error}). Inspect action history before retrying a write."))
            }
        }
    }

    fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        if let Ok(mut guard) = self.process.lock() { *guard = None; }
    }
}

#[derive(Clone, Default)]
struct BackendState(Arc<Backend>);

#[tauri::command]
async fn rpc(method: String, params: Option<Value>, state: tauri::State<'_, BackendState>,
             memory: tauri::State<'_, ce_mcp::CeMcpState>, desktop: tauri::State<'_, mac_desktop::DesktopControl>) -> Reply {
    let stopping = method == "system.stop";
    let resuming = method == "system.resume";
    let status = method == "system.status";
    let revision = execution_control::revision();
    if stopping {
        execution_control::stop();
        let _ = desktop.stop_now(); // Other cancellation paths must still run if the session lock failed.
        ce_core::agent_cancel(&memory).await;
    }
    let backend = state.0.clone();
    let result = tauri::async_runtime::spawn_blocking(move || backend.request(method, params.unwrap_or_else(|| json!({}))))
        .await.map_err(|e| e.to_string())?;
    if resuming && result.is_ok() { execution_control::resume(); }
    if status && result.as_ref().ok().and_then(|v|v["emergency_stop"].as_bool()) == Some(true) { execution_control::sync_stopped(revision); }
    if stopping { ce_core::disconnect(&memory).await; }
    result
}

#[tauri::command]
async fn ce_check_mapping(check_id: String, state: tauri::State<'_, BackendState>,
                          memory: tauri::State<'_, ce_mcp::CeMcpState>,
                          automatic: Option<bool>, execution: tauri::State<'_, mapping_receipt::Execution>) -> Reply {
    let _serial = execution.0.lock().await;
    execution_control::require_running()?;
    mapping_receipt::validate_id(&check_id)?;
    let backend = state.0.clone();
    let id = check_id.clone();
    let pending = tauri::async_runtime::spawn_blocking(move ||
        backend.request("memory.mappings.pending".into(), json!({"check_id": id})))
        .await.map_err(|e| e.to_string())??;
    let request = pending.get("request").filter(|v| v.is_object()).ok_or("Missing mapping request")?.clone();
    let path = PathBuf::from(pending["receipt_path"].as_str().ok_or("Missing receipt path")?);
    if path.exists() { return Ok(json!({"check_id": check_id, "saved": true})); }
    let mut arguments = request.clone();
    arguments["check_id"] = json!(check_id);
    if automatic.unwrap_or(false) {
        let auto = memory.config.lock().await.auto_approve_readonly;
        match ce_policy::classify("verify_mapping", &arguments, auto) {
            ce_policy::Verdict::Auto => {},
            ce_policy::Verdict::NeedsApproval(reason) => return Ok(json!({"check_id":check_id,"approval_required":true,"request":request,"reason":reason})),
            ce_policy::Verdict::Blocked(reason) => return Err(reason),
        }
    }
    let outcome = ce_core::check_mapping(&memory, arguments).await;
    let receipt = match outcome {
        Ok(result) => json!({"schema":"bughunter.mapping-receipt/v1", "check_id":check_id, "request":request, "result":result}),
        Err(error) => json!({"schema":"bughunter.mapping-receipt/v1", "check_id":check_id, "request":request, "error":error}),
    };
    mapping_receipt::save(&path, &check_id, &receipt)?;
    Ok(json!({"check_id":check_id, "saved":true}))
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState::default())
        .manage(ce_mcp::CeMcpState::default())
        .manage(mapping_receipt::Execution::default())
        .manage(mac_desktop::DesktopControl::default())
        .invoke_handler(tauri::generate_handler![
            rpc,
            ce_check_mapping,
            mac_permissions::mac_permission_status,
            mac_permissions::mac_permission_action,
            mac_tool_control::mac_tool_live_status,
            mac_tool_control::mac_tool_open,
            mac_tool_control::mac_local_proxy_listener,
            mac_desktop::mac_desktop_permissions,
            mac_desktop::agent_desktop_status,
            mac_desktop::mac_desktop_begin,
            mac_desktop::mac_desktop_step,
            mac_desktop::mac_desktop_stop,
            ce_mcp::ce_get_config,
            ce_mcp::ce_save_config,
            ce_mcp::ce_sync_ai,
            ce_mcp::ce_connect_mcp,
            ce_mcp::ce_prepare_mapping_chat,
            ce_mcp::ce_select_mapping_program,
            ce_mcp::ce_disconnect_mcp,
            ce_mcp::ce_call_mcp_tool,
            ce_mcp::ce_eval_lua,
            ce_mcp::ce_run_agent_prompt,
            ce_mcp::ce_resolve_approval,
            ce_mcp::ce_retry_agent,
            ce_mcp::ce_cancel_agent,
            ce_mcp::ce_preflight,
            ce_mcp::ce_connection_health,
        ])
        .build(tauri::generate_context!())
        .expect("Unable to start BugHunterClaudex")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<BackendState>().0.shutdown();
                // Reap the CE bridge child; tokio's kill_on_drop is the backstop.
                let ce = app.state::<ce_mcp::CeMcpState>();
                tauri::async_runtime::block_on(crate::ce_core::disconnect(&ce));
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_venv_paths_cover_windows_and_unix() {
        let root = PathBuf::from("workspace");
        assert_eq!(backend_python_candidates(&root, true)[0], root.join("backend/.venv/Scripts/python.exe"));
        assert_eq!(backend_python_candidates(&root, false)[0], root.join("backend/.venv/bin/python"));
        let paths = backend_search_path().unwrap();
        let inherited: Vec<_> = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()).collect();
        let prepared: Vec<_> = std::env::split_paths(&paths).collect();
        assert!(inherited.iter().all(|path| prepared.contains(path)));
    }

    #[test]
    fn stop_response_overtakes_slow_request() {
        let mut command = Command::new("python3");
        command.args(["-u", "-c", "import sys,json,time,threading\nlock=threading.Lock()\ndef respond(r):\n if r['method']=='slow': time.sleep(0.7)\n with lock: print(json.dumps({'id':r['id'],'result':r['method']}),flush=True)\nfor line in sys.stdin:\n threading.Thread(target=respond,args=(json.loads(line),)).start()"]);
        let backend = Arc::new(Backend::default());
        *backend.process.lock().unwrap() = Some(Process::from_command(command).unwrap());
        let slow_backend = backend.clone();
        let slow = std::thread::spawn(move || slow_backend.request("slow".into(), json!({})));
        std::thread::sleep(Duration::from_millis(80));
        let start = Instant::now();
        assert_eq!(backend.request("system.stop".into(), json!({})).unwrap(), "system.stop");
        assert!(start.elapsed() < Duration::from_millis(450));
        assert_eq!(slow.join().unwrap().unwrap(), "slow");
        backend.shutdown();
    }

    #[test]
    fn backend_exit_rejects_pending_call_without_replay() {
        let mut command = Command::new("python3");
        command.args(["-u", "-c", "import sys\nsys.stdin.readline()\nsys.exit(0)"]);
        let backend = Backend::default();
        *backend.process.lock().unwrap() = Some(Process::from_command(command).unwrap());
        let result = backend.request("write_action".into(), json!({}));
        assert!(result.unwrap_err().contains("stopped"));
        backend.shutdown();
    }
}
