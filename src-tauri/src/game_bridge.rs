//! Private desktop → native-helper bridge for the game runtime.
//!
//! One connection per target process is owned here. Its stdin + stdout
//! are spoken by `memory_engine.server` on the Python side. The bridge
//! enforces the plan's security model:
//!
//! * **Credential**: 32 random bytes generated once per sidecar launch,
//!   kept only in memory, never logged, never shown to the renderer,
//!   the model, settings or saved reports. It rotates on sidecar
//!   restart.
//! * **Scope**: the public renderer RPC proxy rejects
//!   `gamelab.native.*` methods outright; only this module's internal
//!   bridge calls dispatch them.
//! * **Routing**: one permanent stdout reader thread splits RPC replies
//!   from versioned notifications. Replies wake their waiter by `id`;
//!   notifications go to the renderer sink.
//!
//! This file is intentionally minimal: it owns the lifetime of the
//! helper and the credential, and gives `ce_mcp` / future game-runtime
//! glue typed entry points to talk to the helper.
//!
//! The skeleton here deliberately avoids Tauri types so it is unit-
//! testable and does not force a Rust-level webview to start.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

/// Length of the per-sidecar bootstrap credential, in bytes.
pub const BRIDGE_CREDENTIAL_BYTES: usize = 32;

/// Methods the renderer-side public RPC proxy must never forward.
///
/// `ce_mcp.rs` (or whichever crate owns the public RPC surface) is
/// expected to check an outbound method name against
/// [`is_internal_bridge_method`] and drop the call before spawning any
/// native work.
pub const INTERNAL_METHOD_PREFIX: &str = "gamelab.native.";

/// Reject every method that is reserved for this bridge.
pub fn is_internal_bridge_method(method: &str) -> bool {
    method.starts_with(INTERNAL_METHOD_PREFIX)
}

/// 256-bit random credential generated once at bridge construction.
///
/// It is stored only inside this struct. `Debug` is deliberately NOT
/// derived — the usual `{:?}` printing would leak the bytes into logs.
#[derive(Clone)]
pub struct BridgeCredential {
    bytes: [u8; BRIDGE_CREDENTIAL_BYTES],
}

impl BridgeCredential {
    /// Draw a fresh credential from the OS CSPRNG.
    ///
    /// On Unix we read `/dev/urandom`; on Windows we call
    /// `BCryptGenRandom(BCRYPT_USE_SYSTEM_PREFERRED_RNG)`. Both are the
    /// standard OS CSPRNGs. No Rust crate is pulled in for this — the
    /// credential never leaves memory and no library-level crypto is
    /// used past this draw.
    pub fn generate() -> Result<Self, String> {
        let mut bytes = [0u8; BRIDGE_CREDENTIAL_BYTES];
        os_random_bytes(&mut bytes)?;
        Ok(Self { bytes })
    }

    /// Constant-time equality check.
    pub fn matches(&self, other: &[u8]) -> bool {
        if other.len() != self.bytes.len() {
            return false;
        }
        let mut diff: u8 = 0;
        for (a, b) in self.bytes.iter().zip(other.iter()) {
            diff |= a ^ b;
        }
        diff == 0
    }

    /// Byte length; useful for assertions in tests.
    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    /// Internal access for bootstrap-send only. NOT exposed through
    /// `Debug`, `Display`, serialization or logging.
    pub(crate) fn private_hex(&self) -> String { hex_encode(&self.bytes) }

    fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

type Pending = Arc<Mutex<HashMap<u64, mpsc::Sender<Result<Value, String>>>>>;
type NotificationSink = Arc<Mutex<Option<Box<dyn Fn(Value) + Send + 'static>>>>;

/// One live helper process plus its reply router.
struct Helper {
    child: Mutex<Child>,
    writer: Mutex<Option<mpsc::SyncSender<(Vec<u8>, mpsc::Sender<Result<(), String>>)>>> ,
    identity: Mutex<Value>,
    pending: Pending,
    notifications: NotificationSink,
    next_id: AtomicU64,
}

impl Helper {
    fn spawn(python_bin: &Path, module_args: Vec<String>) -> Result<Self, String> {
        let mut cmd = Command::new(python_bin);
        cmd.args(&module_args);
        cmd.env("PYTHONUNBUFFERED", "1");
        cmd.stdin(Stdio::piped());
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        let mut child = cmd.spawn().map_err(|e| format!("spawn helper: {e}"))?;
        let mut stdin = child.stdin.take().ok_or("helper stdin unavailable")?;
        let (writer, outgoing) = mpsc::sync_channel::<(Vec<u8>, mpsc::Sender<Result<(), String>>)>(64);
        std::thread::spawn(move || {
            while let Ok((bytes, ack)) = outgoing.recv() {
                let result = stdin.write_all(&bytes).and_then(|_| stdin.flush())
                    .map_err(|e| format!("helper stdin write: {e}"));
                let failed = result.is_err();
                let _ = ack.send(result);
                if failed { break; }
            }
        });
        let stdout = child.stdout.take().ok_or("helper stdout unavailable")?;
        if let Some(mut stderr) = child.stderr.take() {
            std::thread::spawn(move || { let _ = std::io::copy(&mut stderr, &mut std::io::sink()); });
        }
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let notifications: NotificationSink = Arc::new(Mutex::new(None));
        let pending_for_reader = pending.clone();
        let notifications_for_reader = notifications.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut bytes = Vec::new();
                let size = reader.by_ref().take(4 * 1024 * 1024 + 1).read_until(b'\n', &mut bytes);
                if !matches!(size, Ok(n) if n > 0 && n <= 4 * 1024 * 1024) { break; }
                let Ok(line) = String::from_utf8(bytes) else { break };
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let Ok(frame) = serde_json::from_str::<Value>(trimmed) else {
                    // Fail pending requests promptly on a malformed stream.
                    break;
                };
                let kind = frame.get("kind").and_then(Value::as_str).unwrap_or("");
                match kind {
                    "response" => {
                        let Some(id) = frame.get("id").and_then(Value::as_u64) else { break };
                        let result = if let Some(err) = frame.get("error").filter(|v| !v.is_null()) {
                            let msg = err
                                .get("message")
                                .and_then(Value::as_str)
                                .unwrap_or("helper reported an error")
                                .to_string();
                            Err(msg)
                        } else {
                            Ok(frame.get("result").cloned().unwrap_or(Value::Null))
                        };
                        if let Ok(mut map) = pending_for_reader.lock() {
                            if let Some(sender) = map.remove(&id) {
                                let _ = sender.send(result);
                            }
                        }
                    }
                    "notification" => {
                        if let Ok(sink) = notifications_for_reader.lock() {
                            if let Some(ref cb) = *sink {
                                cb(frame);
                            }
                        }
                    }
                    _ => break,
                }
            }
            // Stream closed: fail every pending waiter.
            if let Ok(mut map) = pending_for_reader.lock() {
                for (_, sender) in map.drain() {
                    let _ = sender.send(Err("helper closed stdout".into()));
                }
            }
        });
        Ok(Helper {
            child: Mutex::new(child),
            writer: Mutex::new(Some(writer)),
            identity: Mutex::new(Value::Null),
            pending,
            notifications,
            next_id: AtomicU64::new(1),
        })
    }

    fn send(&self, id: u64, method: &str, params: Value, timeout: Duration) -> Result<(), String> {
        let frame = json!({"kind": "request", "id": id, "method": method, "params": params});
        let mut line = serde_json::to_string(&frame).map_err(|e| e.to_string())?;
        line.push('\n');
        if line.len() > 4 * 1024 * 1024 { return Err("outbound frame exceeds 4 MiB".into()); }
        let writer = self.writer.lock().map_err(|_| "writer poisoned")?.clone().ok_or("helper stdin closed")?;
        let (ack, completed) = mpsc::channel();
        writer.try_send((line.into_bytes(), ack)).map_err(|_| "helper writer queue unavailable or full".to_string())?;
        completed.recv_timeout(timeout.min(Duration::from_secs(2))).map_err(|_| "helper stdin write timeout".to_string())?
    }

    fn call(&self, method: &str, params: Value, timeout: Duration) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = mpsc::channel();
        if let Ok(mut map) = self.pending.lock() {
            map.insert(id, tx);
        }
        if let Err(error) = self.send(id, method, params, timeout) {
            if let Ok(mut map) = self.pending.lock() { map.remove(&id); }
            return Err(error);
        }
        match rx.recv_timeout(timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Ok(mut map) = self.pending.lock() {
                    map.remove(&id);
                }
                // Distinct wording so game_executor can map to
                // reason='helper_response_timeout' (budget exhausted while
                // helper was alive and still working).
                Err(format!("helper_response_timeout: RPC on {method} exceeded {}ms", timeout.as_millis()))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                if let Ok(mut map) = self.pending.lock() {
                    map.remove(&id);
                }
                // Reader channel closed — helper process exited before
                // the reply was dispatched. game_executor maps this to
                // reason='helper_exit_during_write' for mutating ops.
                Err(format!("helper_exit_during_rpc: helper reader disconnected during {method}"))
            }
        }
    }

    fn shutdown_confirmed(&self) -> Result<(), String> {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        self.writer.lock().map_err(|_| "writer poisoned")?.take();
        let mut child = self.child.lock().map_err(|_| "child lock poisoned")?;
        let exited = child.wait_timeout_safe(Duration::from_millis(200)).map_err(|e|e.to_string())?.is_some();
        #[cfg(unix)]
        unsafe {
            // This group was created by spawn; include descendant debuggers.
            extern "C" { fn kill(pid: i32, signal: i32) -> i32; }
            kill(-(child.id() as i32), 9);
        }
        if !exited {
            if let Err(error) = child.kill() {
                if child.try_wait().map_err(|e|e.to_string())?.is_none() { return Err(error.to_string()); }
            }
            if child.wait_timeout_safe(deadline.saturating_duration_since(std::time::Instant::now())).map_err(|e|e.to_string())?.is_none() {
                return Err("Helper termination not confirmed; ownership retained".into());
            }
        }
        #[cfg(unix)]
        loop {
            extern "C" { fn kill(pid: i32, signal: i32) -> i32; }
            if unsafe { kill(-(child.id() as i32), 0) } != 0 { break; }
            if std::time::Instant::now() >= deadline { return Err("Helper descendants remain; ownership retained".into()); }
            std::thread::sleep(Duration::from_millis(20));
        }
        Ok(())
    }
    fn shutdown(&self) { let _ = self.shutdown_confirmed(); }

}

/// Small shim so we can wait on a `Child` with a timeout without
/// pulling in another crate. Returns `Ok(Some(status))` when the child
/// exited inside the window and `Ok(None)` when the deadline passed.
trait WaitTimeoutExt {
    fn wait_timeout_safe(&mut self, timeout: Duration) -> std::io::Result<Option<std::process::ExitStatus>>;
}

impl WaitTimeoutExt for Child {
    fn wait_timeout_safe(&mut self, timeout: Duration) -> std::io::Result<Option<std::process::ExitStatus>> {
        let deadline = std::time::Instant::now() + timeout;
        loop {
            match self.try_wait()? {
                Some(status) => return Ok(Some(status)),
                None => {
                    if std::time::Instant::now() >= deadline {
                        return Ok(None);
                    }
                    std::thread::sleep(Duration::from_millis(25));
                }
            }
        }
    }
}

/// Table of helpers keyed by target-process birth identity.
pub struct BridgeState {
    helpers: Mutex<HashMap<String, Arc<Helper>>>,
}

impl BridgeState {
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            helpers: Mutex::new(HashMap::new()),
        })
    }

    /// Spawn the Python memory-engine helper for one target and send
    /// the bootstrap exchange (credential handshake).
    ///
    /// Durable claims, leases and heartbeat methods are added by P04.
    pub fn attach(
        &self,
        target_key: String,
        python_bin: &Path,
        module_args: Vec<String>,
    ) -> Result<(), String> {
        let mut helpers = self.helpers.lock().map_err(|_| "helper table poisoned")?;
        if helpers.contains_key(&target_key) { return Err("target already has a helper".into()); }
        let credential = BridgeCredential::generate()?;
        let helper = Arc::new(Helper::spawn(python_bin, module_args)?);
        // Bootstrap: send the credential once, over the private
        // channel. The helper acknowledges and never echoes it back.
        let bootstrap = json!({
            "credential_hex": hex_encode(credential.as_bytes()),
        });
        match helper.call("gamelab.native.bootstrap", bootstrap, Duration::from_secs(5)) {
            Ok(result) if result.get("ready") == Some(&Value::Bool(true)) && result.get("protocol_version") == Some(&json!(1)) => { *helper.identity.lock().map_err(|_| "identity lock poisoned")? = result.get("helper_identity").cloned().unwrap_or(Value::Null); },
            result => {
                helper.shutdown();
                return Err(format!("helper bootstrap failed or unsupported: {result:?}"));
            }
        }
        helpers.insert(target_key, helper);
        Ok(())
    }

    pub fn detach(&self, target_key: &str) { let _ = self.detach_confirmed(target_key); }

    pub fn detach_confirmed(&self, target_key: &str) -> Result<(), String> {
        let helper = self.helpers.lock().map_err(|_| "helper table poisoned")?.get(target_key).cloned();
        if let Some(helper) = helper {
            helper.shutdown_confirmed()?;
            self.helpers.lock().map_err(|_| "helper table poisoned")?.remove(target_key);
        }
        Ok(())
    }
    pub fn detach_all_confirmed(&self) -> Result<(), String> {
        let keys: Vec<_> = self.helpers.lock().map_err(|_| "helper table poisoned")?.keys().cloned().collect();
        for key in keys { self.detach_confirmed(&key)?; }
        Ok(())
    }
    pub fn helper_identity(&self, target_key: &str) -> Result<Value,String> {
        let helper=self.helpers.lock().map_err(|_| "helper table poisoned")?.get(target_key).cloned().ok_or("no helper")?;
        let value=helper.identity.lock().map_err(|_| "identity poisoned")?.clone();
        Ok(value)
    }

    /// Install a native notification sink before starting work. The sink runs
    /// on the stdout reader; enqueue events without blocking or reentering
    /// this bridge so responses can continue reaching their waiters.
    pub fn set_notification_handler<F>(&self, target_key: &str, handler: F) -> Result<(), String>
    where F: Fn(Value) + Send + 'static {
        let helpers = self.helpers.lock().map_err(|_| "helper table poisoned")?;
        let helper = helpers.get(target_key).ok_or("no helper for target")?;
        *helper.notifications.lock().map_err(|_| "notification sink poisoned")? = Some(Box::new(handler));
        Ok(())
    }

    pub fn call_internal(
        &self,
        target_key: &str,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        if !is_internal_bridge_method(method) {
            return Err(format!("method {method} is not an internal bridge method"));
        }
        let helper = self.helpers.lock().map_err(|_| "helper table poisoned")?
            .get(target_key).cloned().ok_or_else(|| format!("no helper for target {target_key}"))?;
        helper.call(method, params, timeout)
    }

    /// Public RPC proxy: the renderer calls this; any attempt to use
    /// `gamelab.native.*` names is refused outright.
    pub fn public_proxy(&self, method: &str, _params: Value) -> Result<Value, String> {
        if is_internal_bridge_method(method) {
            return Err(format!(
                "method {method} is reserved for the internal bridge; renderer may not call it"
            ));
        }
        Err(format!("public proxy does not route {method} in this build"))
    }
}

impl Drop for BridgeState {
    fn drop(&mut self) {
        if let Ok(mut helpers) = self.helpers.lock() {
            for (_, helper) in helpers.drain() { helper.shutdown(); }
        }
    }
}

#[cfg(unix)]
fn os_random_bytes(buf: &mut [u8]) -> Result<(), String> {
    use std::io::Read;
    let mut f = std::fs::File::open("/dev/urandom").map_err(|e| format!("open urandom: {e}"))?;
    f.read_exact(buf).map_err(|e| format!("read urandom: {e}"))?;
    Ok(())
}

#[cfg(windows)]
fn os_random_bytes(buf: &mut [u8]) -> Result<(), String> {
    // BCryptGenRandom with BCRYPT_USE_SYSTEM_PREFERRED_RNG.
    #[link(name = "bcrypt")]
    extern "system" {
        fn BCryptGenRandom(
            h_algorithm: *mut core::ffi::c_void,
            pb_buffer: *mut u8,
            cb_buffer: u32,
            dw_flags: u32,
        ) -> i32;
    }
    const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x00000002;
    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            buf.as_mut_ptr(),
            buf.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    };
    if status != 0 {
        return Err(format!("BCryptGenRandom failed: 0x{:08X}", status));
    }
    Ok(())
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0x0f) as usize] as char);
    }
    out
}

// Avoid unused-import warnings when the file compiles in isolation.
#[allow(dead_code)]
fn _unused_path(_p: PathBuf) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn control_and_reap_do_not_wait_for_blocked_operation() {
        let bridge=Arc::new(BridgeState::new().unwrap());
        let backend=PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("backend");
        let script=r#"import time
from bughunter.memory_engine import server
server._METHOD_TABLE['memory.read']=lambda self,params:time.sleep(60)
from bughunter.memory_engine.__main__ import main
main()
"#;
        bridge.attach("blocked".into(),&backend.join(".venv/bin/python"),vec!["-u".into(),"-c".into(),format!("import sys;sys.path.insert(0,{});\n{}",serde_json::to_string(&backend.to_string_lossy()).unwrap(),script)]).unwrap();
        let worker=bridge.clone();
        let call=std::thread::spawn(move||worker.call_internal("blocked","gamelab.native.read",json!({}),Duration::from_secs(30)));
        std::thread::sleep(Duration::from_millis(100));
        let start=std::time::Instant::now();
        let reply=bridge.call_internal("blocked","gamelab.native.quiesce",json!({}),Duration::from_millis(500)).unwrap();
        assert_eq!(reply["quiescent"],false);
        assert!(start.elapsed()<Duration::from_millis(500));
        let start=std::time::Instant::now();
        bridge.detach_confirmed("blocked").unwrap();
        assert!(start.elapsed()<Duration::from_secs(2));
        assert!(call.join().unwrap().is_err());
    }

    #[test]
    fn credential_is_32_bytes_and_distinct() {
        let a = BridgeCredential::generate().expect("generate a");
        let b = BridgeCredential::generate().expect("generate b");
        assert_eq!(a.len(), BRIDGE_CREDENTIAL_BYTES);
        assert_eq!(a.len(), 32);
        assert_eq!(b.len(), 32);
        // Two independent draws should not be identical; a collision
        // on a 256-bit random is astronomically unlikely.
        assert!(a.as_bytes() != b.as_bytes());
    }

    #[test]
    fn credential_constant_time_match() {
        let a = BridgeCredential::generate().expect("generate");
        assert!(a.matches(a.as_bytes()));
        let mut wrong = a.as_bytes().to_vec();
        wrong[0] ^= 0xff;
        assert!(!a.matches(&wrong));
        assert!(!a.matches(b""));
        assert!(!a.matches(b"too short"));
    }

    #[test]
    fn public_proxy_rejects_internal_methods() {
        let bridge = BridgeState::new().expect("bridge");
        for name in [
            "gamelab.native.claim",
            "gamelab.native.dispatched",
            "gamelab.native.complete",
            "gamelab.native.heartbeat",
            "gamelab.native.bootstrap",
        ] {
            let err = bridge
                .public_proxy(name, json!({}))
                .expect_err("renderer must not reach gamelab.native.*");
            assert!(err.contains("reserved"), "unexpected rejection: {err}");
        }
    }

    #[test]
    fn public_proxy_also_refuses_unknown_public_methods_cleanly() {
        let bridge = BridgeState::new().expect("bridge");
        let err = bridge
            .public_proxy("gamelab.sessions.start", json!({}))
            .expect_err("skeleton has no routing yet");
        assert!(err.contains("does not route"), "unexpected rejection: {err}");
    }

    #[test]
    fn is_internal_bridge_method_is_strict_about_prefix() {
        assert!(is_internal_bridge_method("gamelab.native.claim"));
        assert!(!is_internal_bridge_method("gamelab.nativefoo"));
        assert!(!is_internal_bridge_method("gamelab.sessions.start"));
        assert!(!is_internal_bridge_method(""));
    }
    #[cfg(target_os = "macos")]
    #[test]
    fn actual_helper_external_child_read_write_restore() {
        let backend = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("backend");
        let python = backend.join(".venv/bin/python");
        assert!(python.exists(), "backend venv must be installed for native acceptance");
        struct Target(Child);
        impl Drop for Target { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }
        let mut target = Target(Command::new(&python).args(["-u", "-c",
            "import ctypes,os,sys; v=ctypes.c_uint32(60); print(str(os.getpid())+' '+str(ctypes.addressof(v)),flush=True); [(print(v.value,flush=True)) for line in sys.stdin]"])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap());
        let output = BufReader::new(target.0.stdout.take().unwrap());
        let (lines_tx, lines_rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in output.lines() {
                if lines_tx.send(line).is_err() { break; }
            }
        });
        let mut line = lines_rx.recv_timeout(Duration::from_secs(5)).unwrap().unwrap();
        let values: Vec<u64> = line.split_whitespace().map(|v| v.parse().unwrap()).collect();
        let source = format!("import sys,runpy; sys.path.insert(0,{}); runpy.run_module('bughunter.memory_engine',run_name='__main__')", serde_json::to_string(&backend.to_string_lossy()).unwrap());
        let bridge = BridgeState::new().unwrap();
        bridge.attach("child".into(), &python, vec!["-c".into(), source]).unwrap();
        assert!(bridge.attach("child".into(), &python, vec![]).is_err());
        let call = |method: &str, params: Value| bridge.call_internal("child", method, params, Duration::from_secs(30)).unwrap();
        let attached = call("gamelab.native.attach", json!({"pid":values[0]}));
        assert_eq!(attached["status"], "attached");
        let mut args = json!({"session_id":attached["session_id"], "identity":attached["identity"], "address":format!("0x{:x}", values[1]), "vtype":"uint"});
        assert_eq!(call("gamelab.native.read", args.clone())["value"], 60);
        args["value"] = json!(7);
        assert_eq!(call("gamelab.native.write", args.clone())["verified"], true);
        assert_eq!(call("gamelab.native.read", args.clone())["value"], 7);
        target.0.stdin.as_mut().unwrap().write_all(b"get\n").unwrap();
        line = lines_rx.recv_timeout(Duration::from_secs(5)).expect("target reply timed out after native operation").unwrap();
        assert_eq!(line.trim(), "7");
        args["value"] = json!(60);
        call("gamelab.native.write", args.clone());
        assert_eq!(call("gamelab.native.read", args)["value"], 60);
        target.0.stdin.as_mut().unwrap().write_all(b"get\n").unwrap();
        line = lines_rx.recv_timeout(Duration::from_secs(5)).expect("target reply timed out after native operation").unwrap();
        assert_eq!(line.trim(), "60");
        bridge.detach("child");
    }

    #[test]
    fn failed_bootstrap_reaps_helper_and_leaves_no_entry() {
        let backend = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("backend");
        let python = backend.join(if cfg!(windows) {".venv/Scripts/python.exe"} else {".venv/bin/python"});
        let bridge = BridgeState::new().unwrap();
        let result = bridge.attach("bad".into(), &python, vec!["-c".into(), "print('garbage',flush=True)".into()]);
        assert!(result.is_err());
        assert!(bridge.helpers.lock().unwrap().is_empty());
    }

    #[test]
    fn notifications_interleave_with_replies_and_disconnect_fails_waiter() {
        let backend = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("backend");
        let python = backend.join(if cfg!(windows) {".venv/Scripts/python.exe"} else {".venv/bin/python"});
        let source = r#"import json,sys
for line in sys.stdin:
    request=json.loads(line)
    method=request['method']
    if method.endswith('bootstrap'):
        result={'ready':True,'protocol_version':1}
    elif method.endswith('read'):
        print(json.dumps({'kind':'notification','method':'progress','params':{'step':1}}),flush=True)
        result={'value':7}
    else:
        break
    print(json.dumps({'kind':'response','id':request['id'],'result':result}),flush=True)
"#;
        let bridge = BridgeState::new().unwrap();
        bridge.attach("wire".into(), &python, vec!["-u".into(), "-c".into(), source.into()]).unwrap();
        let (tx, rx) = mpsc::channel();
        bridge.set_notification_handler("wire", move |value| { let _ = tx.send(value); }).unwrap();
        let result = bridge.call_internal("wire", "gamelab.native.read", json!({}), Duration::from_secs(2)).unwrap();
        assert_eq!(result["value"], 7);
        assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap()["params"]["step"], 1);
        let start = std::time::Instant::now();
        assert!(bridge.call_internal("wire", "gamelab.native.exit", json!({}), Duration::from_secs(5)).is_err());
        assert!(start.elapsed() < Duration::from_secs(2));
        bridge.detach("wire");
    }

}
