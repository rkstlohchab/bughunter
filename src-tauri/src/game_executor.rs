//! One app-wide durable executor. Only the Rust backend connection implements
//! this private interface; no command or credential is exposed to the webview.
use crate::game_bridge::{BridgeCredential, BridgeState};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Weak};
use std::time::{Duration, Instant};

pub(crate) trait NativeRpc: Send + Sync {
    fn native_request(&self, method: &str, params: Value) -> Result<Value, String>;
}

pub(crate) struct ExecutorRuntime {
    client: Weak<dyn NativeRpc>,
    stopping: Arc<AtomicBool>,
    bridge: Arc<BridgeState>,
    thread: Option<std::thread::JoinHandle<()>>,
}

struct Worker {
    stopping: Arc<AtomicBool>,
    done: mpsc::Receiver<()>,
}

impl ExecutorRuntime {
    pub(crate) fn start(client: Weak<dyn NativeRpc>) -> Result<Self, String> {
        let bridge = Arc::new(BridgeState::new()?);
        let stopping = Arc::new(AtomicBool::new(false));
        let stop = stopping.clone();
        let helpers = bridge.clone();
        let shutdown_client=client.clone();
        let thread = std::thread::spawn(move || {
            let mut workers: HashMap<String, Worker> = HashMap::new();
            while !stop.load(Ordering::Acquire) {
                workers.retain(|_, worker| matches!(worker.done.try_recv(),Err(mpsc::TryRecvError::Empty)));
                let Some(rpc) = client.upgrade() else { break };
                if let Ok(poll) = rpc.native_request("gamelab.native.poll", json!({})) {
                    if let Some(sessions) = poll["sessions"].as_array() {
                        for session in sessions {
                            let Some(key) = session["target_key"].as_str() else { continue };
                            if session["stop_requested"].as_bool().unwrap_or(false) {
                                if let Some(worker) = workers.get(key) { worker.stopping.store(true, Ordering::Release); }
                                continue;
                            }
                            if workers.contains_key(key) || !matches!(session["state"].as_str(), Some("active" | "running" | "starting")) { continue; }
                            let local_stop = Arc::new(AtomicBool::new(false));
                            let (done_tx, done) = mpsc::channel();
                            workers.insert(key.to_owned(), Worker { stopping: local_stop.clone(), done });
                            let rpc = rpc.clone();
                            let bridge = helpers.clone();
                            let session = session.clone();
                            let app_stop = stop.clone();
                            std::thread::spawn(move || {
                                // Failures remain durable on the backend. A helper is always
                                // reaped before this worker releases its connection ownership.
                                if let Err(error)=run_target(rpc.as_ref(), &bridge, session.clone(), &local_stop, &app_stop) {
                                    let _=rpc.native_request("gamelab.native.failed",json!({"process_instance_id":session["process_instance_id"],"error":error}));
                                }
                                let _ = done_tx.send(());
                            });
                        }
                    }
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            for worker in workers.values() { worker.stopping.store(true, Ordering::Release); }
            // Workers perform bounded quiescence/cleanup/reap while the backend
            // is still alive, so its final stopped receipt can be persisted.
            let deadline = Instant::now() + Duration::from_secs(8);
            while !workers.is_empty() && Instant::now() < deadline {
                workers.retain(|_, worker| matches!(worker.done.try_recv(),Err(mpsc::TryRecvError::Empty)));
                std::thread::sleep(Duration::from_millis(20));
            }
            let _ = helpers.detach_all_confirmed();
        });
        Ok(Self { client: shutdown_client, stopping, bridge, thread: Some(thread) })
    }

    pub(crate) fn shutdown(&mut self) {
        // Commit Stop before waking workers into cleanup. Otherwise a worker
        // can quiesce and exit before its declared recipe becomes eligible.
        if self.thread.is_some() {
            if let Some(rpc)=self.client.upgrade() {
                let _=rpc.native_request("gamelab.native.stop_all",json!({}));
            }
        }
        self.stopping.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
        let _ = self.bridge.detach_all_confirmed();
    }
}

impl Drop for ExecutorRuntime { fn drop(&mut self) { self.shutdown(); } }

fn helper_command() -> Result<(PathBuf, Vec<String>), String> {
    if cfg!(debug_assertions) {
        let backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().ok_or("missing workspace root")?.join("backend");
        let python = backend.join(if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" });
        if !python.exists() { return Err("native Python environment is unavailable".into()); }
        let path = serde_json::to_string(&backend.to_string_lossy()).map_err(|e| e.to_string())?;
        return Ok((python, vec!["-u".into(), "-c".into(), format!("import sys,runpy; sys.path.insert(0,{path}); runpy.run_module('bughunter.memory_engine',run_name='__main__')")]));
    }
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let helper = executable.parent().ok_or("missing application directory")?.join(if cfg!(windows) { "bughunter-backend.exe" } else { "bughunter-backend" });
    Ok((helper, vec!["--memory-engine".into()]))
}

fn binding(operation: &Value, executor: &Value) -> Value {
    json!({"operation_id":operation["id"], "revision":operation["revision"], "attempt_id":operation["attempt_id"],
           "executor_id":executor["executor_id"], "executor_epoch":executor["executor_epoch"]})
}

fn auth(executor: &Value) -> Value {
    json!({"executor_id":executor["executor_id"], "executor_epoch":executor["executor_epoch"]})
}

fn run_target(rpc: &dyn NativeRpc, bridge: &Arc<BridgeState>, session: Value, stop: &AtomicBool, app_stop: &AtomicBool) -> Result<(), String> {
    let key = session["target_key"].as_str().ok_or("target key missing")?;
    let process = session["process_instance_id"].as_str().ok_or("process identity missing")?;
    let (_, birth) = process.split_once(':').ok_or("process identity must be pid:birth")?;
    let birth: u64 = birth.parse().map_err(|_| "invalid process birth")?;
    let identity = json!({"pid":session["pid"], "start_time_ns":birth, "target_fingerprint":session["target_fingerprint"]});
    let executor_id = format!("desktop_{}", BridgeCredential::generate()?.private_hex());
    let (command, args) = helper_command()?;
    bridge.attach(key.to_owned(), &command, args)?;
    let initialized = (|| {
        let attached = bridge.call_internal(key, "gamelab.native.attach", json!({"pid":session["pid"], "identity":identity, "session_id":executor_id, "mutating":true}), Duration::from_secs(30))?;
        if attached["status"] != "attached" { return Err("native target is owned by another executor".into()); }
        let helper = bridge.helper_identity(key)?;
        rpc.native_request("gamelab.native.register", json!({"executor_id":executor_id, "helper_pid":helper["pid"],
            "helper_start_time_ns":helper["start_time_ns"], "process_instance_id":session["process_instance_id"], "target_fingerprint":session["target_fingerprint"]}))
    })();
    let executor = match initialized {
        Ok(value) => value,
        Err(error) => { let _ = bridge.detach_confirmed(key); return Err(error); }
    };
    let mut pending: Option<PendingOperation> = None;
    let mut last_cleanup = json!({"status":"unavailable","reason":"executor_interrupted"});
    let outcome = (|| {
        loop {
            let heartbeat = rpc.native_request("gamelab.native.heartbeat", auth(&executor))?;
            if stop.load(Ordering::Acquire) || app_stop.load(Ordering::Acquire) || heartbeat["stop_requested"] == true {
                last_cleanup = quiesce_and_cleanup(rpc, bridge, key, &identity, &executor_id, &executor, &mut pending);
                if last_cleanup["quiescent"] == true && last_cleanup["status"] != "outcome_unknown" {
                    let finished = rpc.native_request("gamelab.native.finish_stop", auth(&executor))?;
                    bridge.call_internal(key, "gamelab.native.release_tasks", json!({"session_id":executor_id,"task_session_ids":finished["stopped_task_session_ids"]}), Duration::from_secs(1))?;
                    if finished["resume"] == true && !app_stop.load(Ordering::Acquire) {
                        bridge.call_internal(key, "gamelab.native.resume", json!({}), Duration::from_secs(1))?;
                        stop.store(false, Ordering::Release);
                        continue;
                    }
                }
                break;
            }
            if let Some(work) = pending.as_ref() {
                match work.reply.recv_timeout(Duration::from_millis(100)) {
                    Ok(result) => {
                        record_reply(rpc, &executor, &work.operation, result)?;
                        pending = None;
                    }
                    Err(mpsc::RecvTimeoutError::Disconnected) => return Err("native operation waiter disconnected".into()),
                    Err(mpsc::RecvTimeoutError::Timeout) => {},
                }
                continue;
            }
            let claim = rpc.native_request("gamelab.native.claim", auth(&executor))?;
            let operation = claim["operation"].clone();
            if operation.is_null() { std::thread::sleep(Duration::from_millis(100)); continue; }
            let dispatched = match rpc.native_request("gamelab.native.dispatched", binding(&operation, &executor)) {
                Ok(value) => value,
                Err(error) => {
                    // A Stop between claim and dispatch remains undispatched.
                    if rpc.native_request("gamelab.native.heartbeat", auth(&executor))?["stop_requested"] == true { continue; }
                    return Err(error);
                }
            };
            let operation = dispatched["operation"].clone();
            let method = match operation["kind"].as_str() {
                Some("read") => "gamelab.native.read",
                Some("write") => "gamelab.native.write",
                Some("scan") => "gamelab.native.scan",
                Some("pointer_search") => "gamelab.native.pointer_search",
                _ => {
                    let mut receipt = binding(&operation, &executor);
                    receipt["status"] = json!("failed");
                    receipt["error"] = json!({"code":"unsupported_native_operation","message":"Operation has no qualified native adapter"});
                    rpc.native_request("gamelab.native.complete", receipt)?;
                    continue;
                }
            };
            let mut params = operation["arguments"].clone();
            params["session_id"] = json!(executor_id);
            params["identity"] = identity.clone();
            params["task_session_id"] = operation["session_id"].clone();
            let helper = bridge.clone(); let target_key = key.to_owned();
            let (tx, reply) = mpsc::channel();
            std::thread::spawn(move || {
                let _ = tx.send(helper.call_internal(&target_key, method, params, Duration::from_secs(30)));
            });
            pending = Some(PendingOperation { operation, reply });
        }
        Ok(())
    })();
    if outcome.is_err() {
        last_cleanup = quiesce_and_cleanup(rpc, bridge, key, &identity, &executor_id, &executor, &mut pending);
    }
    let death = bridge.detach_confirmed(key);
    let mut stopped = auth(&executor);
    stopped["helper_death_confirmed"] = json!(death.is_ok());
    stopped["cleanup_result"] = last_cleanup;
    if let Err(error) = &outcome { stopped["error"] = json!({"code":"executor_interrupted","message":error}); }
    let receipt = rpc.native_request("gamelab.native.stopped", stopped);
    death?;
    receipt?;
    outcome
}

struct PendingOperation {
    operation: Value,
    reply: mpsc::Receiver<Result<Value, String>>,
}

/// Map a helper-side error string into a stable reason code so callers
/// can distinguish WHY an operation failed without parsing prose.
///
/// Codes (stable, snake_case):
///  * ``helper_response_timeout`` — helper alive but did not reply inside
///    the budget (e.g. compare_write + readback slower than cleanup budget).
///  * ``helper_exit_during_write`` — helper process exited mid-op (reader
///    channel disconnected); mutating ops end as ``outcome_unknown`` and
///    must never be replayed.
///  * ``precondition_failed`` — the operation's declared precondition did
///    not hold (e.g. value changed since the read receipt that authored
///    ``expected_value``).
///  * ``target_identity_changed`` — the process id or build fingerprint
///    moved while the op was in flight; see also quiesce_and_cleanup.
///  * ``helper_reply_unavailable`` — generic fallback when the helper
///    surfaced an error that does not match a more specific category.
fn classify_helper_error(error: &str, kind: &str) -> &'static str {
    // ``kind`` may be ``read``/``write``/etc; only write-class ops care about
    // the exit-vs-timeout split (writes end as outcome_unknown either way).
    let _ = kind;
    if error.starts_with("helper_response_timeout") { return "helper_response_timeout"; }
    if error.starts_with("helper_exit_during_rpc") { return "helper_exit_during_write"; }
    if error.contains("precondition_failed") { return "precondition_failed"; }
    if error.contains("target_identity_changed") || error.contains("process_instance_id") {
        return "target_identity_changed";
    }
    "helper_reply_unavailable"
}

fn record_reply(rpc: &dyn NativeRpc, executor: &Value, operation: &Value, result: Result<Value,String>) -> Result<(),String> {
    let mut complete = binding(operation, executor);
    let heartbeat = rpc.native_request("gamelab.native.heartbeat", auth(executor))?;
    if let Some(requests) = heartbeat["stop_requests"].as_array() {
        for current in requests { if current["id"] == operation["id"] { complete["revision"] = current["revision"].clone(); } }
    }
    match result {
        Ok(value) if value.get("error").filter(|v| !v.is_null()).is_none() => {
            complete["status"] = json!("succeeded"); complete["result"] = value;
        },
        Ok(value) => {
            // Helper surfaced a structured error. If it names a precondition
            // failure, promote the code so the top-level reason is actionable.
            let err = value["error"].clone();
            let code = err.get("code").and_then(|c| c.as_str()).unwrap_or("");
            let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("");
            let reason = if code.is_empty() {
                classify_helper_error(msg, operation["kind"].as_str().unwrap_or(""))
            } else if code == "precondition_failed" || msg.contains("precondition_failed") {
                "precondition_failed"
            } else {
                code
            };
            // A structured error may arrive after WriteMemory changed bytes.
            // Only a failed compare proves that no write was attempted.
            complete["status"] = json!(if operation["kind"] == "write" && reason != "precondition_failed" {
                "outcome_unknown"
            } else { "failed" });
            complete["error"] = json!({"code": reason, "message": msg, "raw": err});
        },
        Err(error) => {
            // Helper errors can follow a partial mutation; never retry writes.
            let kind = operation["kind"].as_str().unwrap_or("");
            let reason = classify_helper_error(&error, kind);
            complete["status"] = json!(if kind == "write" { "outcome_unknown" } else { "failed" });
            complete["error"] = json!({"code": reason, "message": error});
        },
    }
    rpc.native_request("gamelab.native.complete", complete)?;
    Ok(())
}

fn quiesce_and_cleanup(rpc: &dyn NativeRpc, bridge: &BridgeState, key: &str, identity: &Value, native_session: &str, executor: &Value, pending: &mut Option<PendingOperation>) -> Value {
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut quiescent = false;
    while Instant::now() < deadline {
        let reply = bridge.call_internal(key, "gamelab.native.quiesce", json!({}), Duration::from_millis(150));
        if let Some(work) = pending.as_ref() {
            // The helper sends the operation reply before declaring quiescence.
            match work.reply.recv_timeout(Duration::from_millis(20)) {
                Ok(result) => {
                    if let Err(error) = record_reply(rpc, executor, &work.operation, result) {
                        return json!({"status":"outcome_unknown","reason":error});
                    }
                    *pending = None;
                },
                Err(mpsc::RecvTimeoutError::Disconnected) => { *pending = None; },
                Err(mpsc::RecvTimeoutError::Timeout) => {},
            }
        }
        if reply.as_ref().is_ok_and(|v| v["quiescent"] == true) { quiescent = true; break; }
        std::thread::sleep(Duration::from_millis(20));
    }
    if !quiescent { return json!({"status":"unavailable","reason":"quiescence_timeout"}); }
    let cleanup_deadline = Instant::now() + Duration::from_secs(2);
    let mut results = Vec::new();
    loop {
        let available = match rpc.native_request("gamelab.native.quiescent", auth(executor)) {
            Ok(value) => value, Err(error) => return json!({"status":"unavailable","reason":error}),
        };
        let Some(cleanup) = available.get("cleanup_operation").filter(|v| v.is_object()) else {
            return json!({"status":"completed","quiescent":true,"operations":results});
        };
        if cleanup["kind"] != "write" || cleanup["process_instance_id"] != executor["process_instance_id"] || cleanup["target_fingerprint"] != executor["target_fingerprint"] {
            return json!({"status":"unavailable","reason":"target_identity_changed"});
        }
        let mut arguments = cleanup["arguments"].clone();
        arguments["session_id"] = json!(native_session); arguments["identity"] = identity.clone(); arguments["cleanup"] = json!(true);
        arguments["task_session_id"] = cleanup["session_id"].clone();
        let remaining = cleanup_deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() { return json!({"status":"outcome_unknown","reason":"cleanup_budget_exhausted"}); }
        let result = bridge.call_internal(key, "gamelab.native.write", arguments, remaining);
        let err_reason = result.as_ref().err().map(|e| classify_helper_error(e, "write"));
        if let Err(error) = record_reply(rpc, executor, cleanup, result) {
            return json!({"status":"outcome_unknown","reason":error});
        }
        results.push(cleanup["id"].clone());
        if let Some(reason) = err_reason {
            return json!({"status":"outcome_unknown","reason":reason,"operations":results});
        }
    }
}

#[cfg(all(test,target_os="macos"))]
mod tests {
    use super::*;
    use crate::{Backend,Process};
    use std::process::Command;
    use std::sync::Mutex;

    #[test]
    fn structured_write_error_preserves_unknown_outcome() {
        struct Receipts(Mutex<Vec<Value>>);
        impl NativeRpc for Receipts {
            fn native_request(&self, method: &str, params: Value) -> Result<Value, String> {
                if method == "gamelab.native.complete" { self.0.lock().unwrap().push(params); }
                Ok(json!({"stop_requests":[]}))
            }
        }
        let rpc = Receipts(Mutex::new(Vec::new()));
        let operation = json!({"id":"write-1","kind":"write","revision":1});
        record_reply(&rpc, &json!({}), &operation,
            Ok(json!({"error":{"code":"operation_failed","message":"readback mismatch after write"}}))).unwrap();
        record_reply(&rpc, &json!({}), &operation,
            Ok(json!({"error":{"code":"operation_failed","message":"precondition_failed: changed"}}))).unwrap();
        let receipts = rpc.0.lock().unwrap();
        assert_eq!(receipts[0]["status"], "outcome_unknown");
        assert_eq!(receipts[1]["status"], "failed");
    }

    #[test]
    fn durable_backend_executor_fixture_read_write_cleanup() {
        let backend_dir=PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("backend");
        let python=backend_dir.join(".venv/bin/python");
        let temp=std::env::temp_dir().join(format!("bughunter-p04-{}",BridgeCredential::generate().unwrap().private_hex()));
        std::fs::create_dir_all(&temp).unwrap();
        // Only model decisions are scripted. Harness, typed tools, SQLite,
        // Rust executor, helper transport and process memory are production.
        let entry=r#"import time
from bughunter import provider_api
def turn(**kw):
    execute=kw['execute']
    message=next(m['content'] for m in reversed(kw['transcript']) if m['role']=='user')
    if 'shared reader' in message:
        return {'message':{'role':'assistant','content':'Ready to observe.'},'tool_calls':[]}
    assert 'Cited course plan' in str(kw['transcript'])
    assert 'course_chunk' in str(kw['transcript'])
    assert 'observe the baseline value before writing' in str(kw['transcript'])
    assert kw['provider_cfg']['model'] == 'fixture-runtime-provider'
    fixture=execute('game_fixture', {})
    assert 'error' not in fixture, fixture
    sid=fixture['session']['id']
    def wait(op):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            state=execute('game_status', {})
            current=next(o for s in state['sessions'] for o in s['operations'] if o['id']==op['id'])
            if current['status'] in {'succeeded','failed','outcome_unknown'}:
                assert current['status']=='succeeded', current
                return current
            time.sleep(.25)
        raise AssertionError('Native operation timed out')
    field=fixture['target']['fields']['countdown']
    read=execute('game_read', {'session_id':sid,**field})
    assert wait(read)['result']['value']==60
    scan=execute('game_scan',{'session_id':sid,'value_type':'u32','mode':'exact','needle':60})
    scanned=wait({'id':scan['operation_id']})
    assert scanned['status']=='succeeded', scanned
    assert any(item['address']==field['address'] for item in scanned['result']['results']), scanned
    saved=execute('game_mapping_save',{'session_id':sid,'name':'runtime countdown','root':'mod:region_0+0x0','steps':[{'op':'add','offset':'0x20','width':4}],'value_type':'u32'})
    resolve=execute('game_mapping_revalidate',{'session_id':sid,'recipe_id':saved['id']})
    resolved=wait({'id':resolve['operation_id']})
    assert resolved['result']['value']==60 and resolved['result']['address']==field['address'], resolved
    observed=execute('game_mapping_observe',{'session_id':sid,'recipe_id':saved['id'],'operation_id':resolve['operation_id']})
    assert observed['verification_status']=='observed', observed
    feature=execute('game_feature_start',{'session_id':sid,'recipe_id':saved['id'],'feature_id':'fixed_countdown','apply_value':9,'disable_value':60})
    feature_result=wait({'id':feature['operation_id']})
    assert feature_result['status']=='succeeded' and feature_result['result']['value']==9, feature_result
    monitor=execute('game_feature_monitor',{'session_id':sid,'recipe_id':saved['id'],'feature_id':'fixed_countdown'})
    assert wait({'id':monitor['operation_id']})['result']['value']==9
    execute('game_feature_stop',{'session_id':sid,'feature_id':'fixed_countdown'})
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        status=execute('game_status',{})
        current=next(s for s in status['sessions'] if s['id']==sid)
        if any(f['feature_id']=='fixed_countdown' and f['state']=='stopped' and f['cleanup_status']=='pass' for f in current['features']):break
        time.sleep(.1)
    else:raise AssertionError('feature cleanup did not complete: '+str(status))
    fresh=execute('game_read',{'session_id':sid,**field})
    assert wait(fresh)['result']['value']==60
    read=fresh
    write=execute('game_write', {'session_id':sid,'read_operation_id':read['id'],'value':7,'restore_on_stop':True})
    assert wait(write)['result']['verified'] is True
    assert wait(execute('game_read', {'session_id':sid,**field}))['result']['value']==7
    return {'message':{'role':'assistant','content':'Observed test value 60, applied 7, independently read 7. Fixture only.'},'tool_calls':[]}
provider_api.single_tool_turn=turn
from bughunter.__main__ import main
main()
"#;
        let seed=r#"import sys,json
from bughunter.store import Store
from bughunter.knowledge import KnowledgeService
s=Store(sys.argv[1]);knowledge=KnowledgeService(s,start_worker=False)
s.put('source',{'title':'Runtime fixture course','status':'ready','project_id':None,'platform_scope':'shared','chunk_count':1},id='course_runtime')
text='Test game runtime using a disposable fixture: observe the baseline value before writing, then independently read it back. Restore the static fixture value when stopping.'
with s.connection(immediate=True) as db:
    db.execute('INSERT INTO knowledge_chunks(id,source_id,ordinal,text,original_text,locator,modality,metadata,corrected) VALUES(?,?,?,?,?,?,?,?,0)',('course_chunk','course_runtime',0,text,text,'paragraph-1','text',json.dumps({'platform_scope':'shared'})))
    db.execute('INSERT INTO knowledge_fts(id,source_id,text,title,locator) VALUES(?,?,?,?,?)',('course_chunk','course_runtime',text,'Runtime fixture course','paragraph-1'))
knowledge.shutdown()
"#;
        assert!(Command::new(&python).current_dir(&backend_dir).args(["-c",seed,temp.to_str().unwrap()]).status().unwrap().success());
        let credential=BridgeCredential::generate().unwrap().private_hex();
        let mut command=Command::new(&python);
        command.current_dir(&backend_dir).args(["-u","-c",entry,"--stdio","--data-dir",temp.to_str().unwrap()]).env("BUGHUNTER_NATIVE_CREDENTIAL",&credential);
        let mut process=Process::from_command(command).unwrap();process.native_credential=Some(credential);
        let backend=Arc::new(Backend {process:Mutex::new(Some(process)),..Backend::default()});
        assert_eq!(backend.native_request("gamelab.native.bootstrap",json!({})).unwrap()["ready"],true);
        assert!(backend.request("gamelab.native.complete".into(),json!({})).is_err());
        let client:Arc<dyn NativeRpc>=backend.clone();
        let mut runtime=ExecutorRuntime::start(Arc::downgrade(&client)).unwrap();
        let call=|method:&str,params:Value|backend.request(method.into(),params).unwrap();
        let start=call("harness.start",json!({"message":"Test game runtime using a disposable fixture and my course", "grants":["app"], "mode":"game", "web_search_mode":"off", "settings_override":{"provider":"ollama","model":"fixture-runtime-provider"}}));
        let harness=&start["id"];
        let deadline=Instant::now()+Duration::from_secs(45);
        let chat=loop {
            let state=call("harness.get",json!({"id":harness}));
            if state["status"]=="awaiting_user" { break state; }
            assert_eq!(state["status"],"running","Chat failed: {state}");
            assert!(Instant::now()<deadline,"Chat timed out: {state}");
            std::thread::sleep(Duration::from_millis(200));
        };
        assert!(chat["session"]["messages"].to_string().contains("independently read 7"));
        let state=call("gamelab.runtime.status",json!({"harness_id":harness}));
        let session=state["sessions"][0]["id"].as_str().unwrap();
        let target=state["sessions"][0]["target_id"].clone();
        let address=state["sessions"][0]["descriptor"]["fields"]["countdown"]["address"].clone();
        assert!(state["sessions"][0]["knowledge_plan_id"].is_string());
        let original_executor=backend.native_request("gamelab.native.poll",json!({})).unwrap()["executors"][0]["executor_id"].clone();
        let reader_task=call("harness.start",json!({"message":"Test game shared reader", "grants":["app"],"mode":"game","web_search_mode":"off"}));
        let reader=call("gamelab.sessions.start",json!({"harness_id":reader_task["id"],"target_id":target}));
        call("gamelab.features.stop",json!({"session_id":session,"feature_id":"manual_value"}));
        let deadline=Instant::now()+Duration::from_secs(12);
        loop {
            let state=call("gamelab.sessions.get",json!({"session_id":session}));
            let features=state["sessions"][0]["features"].as_array().unwrap();
            if features.iter().any(|f|f["feature_id"]=="manual_value"&&f["state"]=="stopped") {
                assert_eq!(state["sessions"][0]["state"],"running");
                assert!(state["sessions"][0]["operations"].as_array().unwrap().iter().any(|o|o["arguments"]["value"]==60&&o["status"]=="succeeded"),"cleanup missing: {state}");
                break;
            }
            assert!(Instant::now()<deadline,"feature Stop not complete: {state}");
            std::thread::sleep(Duration::from_millis(100));
        }
        call("gamelab.sessions.stop",json!({"session_id":session}));
        let read=call("gamelab.runtime.read",json!({"session_id":reader["id"],"address":address,"vtype":"uint"}));
        let deadline=Instant::now()+Duration::from_secs(12);
        loop {
            let state=call("gamelab.sessions.get",json!({"session_id":reader["id"]}));
            assert_eq!(state["sessions"][0]["state"],"running","reader interrupted by session Stop: {state}");
            let current=state["sessions"][0]["operations"].as_array().unwrap().iter().find(|o|o["id"]==read["id"]).unwrap();
            if current["status"]=="succeeded" { assert_eq!(current["result"]["value"],60);break; }
            assert!(Instant::now()<deadline,"shared read failed: {state}");std::thread::sleep(Duration::from_millis(100));
        }
        let poll=backend.native_request("gamelab.native.poll",json!({})).unwrap();
        assert_eq!(poll["executors"].as_array().unwrap().len(),1);
        assert_eq!(poll["executors"][0]["executor_id"],original_executor);
        call("gamelab.sessions.stop",json!({"session_id":reader["id"]}));
        let deadline=Instant::now()+Duration::from_secs(12);
        loop {
            let poll=backend.native_request("gamelab.native.poll",json!({})).unwrap();
            if poll["executors"][0]["state"]=="dead" { break; }
            assert!(Instant::now()<deadline,"helper not reaped: {poll}");std::thread::sleep(Duration::from_millis(100));
        }
        let closing=call("harness.start",json!({"message":"Test game runtime using a disposable fixture and my course on app close", "grants":["app"], "mode":"game", "web_search_mode":"off", "settings_override":{"provider":"ollama","model":"fixture-runtime-provider"}}));
        let deadline=Instant::now()+Duration::from_secs(45);
        loop {
            let state=call("harness.get",json!({"id":closing["id"]}));
            if state["status"]=="awaiting_user" {break;}
            assert_eq!(state["status"],"running","shutdown fixture failed: {state}");
            assert!(Instant::now()<deadline);std::thread::sleep(Duration::from_millis(200));
        }
        runtime.shutdown();
        let state=call("gamelab.runtime.status",json!({"harness_id":closing["id"]}));
        assert_eq!(state["sessions"][0]["state"],"stopped","app close did not stop: {state}");
        assert!(state["sessions"][0]["operations"].as_array().unwrap().iter().any(|o|o["arguments"]["value"]==60&&o["status"]=="succeeded"),"app close did not restore: {state}");
        if let Some(folder)=std::env::var_os("BUGHUNTER_P04_EVIDENCE_DIR") {
            let folder=PathBuf::from(folder);std::fs::create_dir_all(&folder).unwrap();
            let evidence=json!({"provider":"scripted test provider; no paid model call","runtime":"production harness, broker, Rust executor and native helper", "chat":chat,
                "final_status":call("gamelab.runtime.status",json!({})),"final_executors":backend.native_request("gamelab.native.poll",json!({})).unwrap()["executors"],
                "observed_sequence":[60,7,60],"human_review":"not_run"});
            std::fs::write(folder.join("chat-native-receipts.json"),serde_json::to_vec_pretty(&evidence).unwrap()).unwrap();
        }
        backend.shutdown();std::fs::remove_dir_all(temp).unwrap();
    }
}
