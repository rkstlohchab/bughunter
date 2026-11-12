//! Tauri command layer for the Cheat Engine MCP bridge. Logic lives in `ce_core.rs`
//! (client, agent loop) and `ce_policy.rs` (approval rules).

use crate::ce_core::{self, CeAgentOutcome, CeAppConfig, CeMcpTool, CePreflightItem};
use serde_json::{json, Value};
use std::path::PathBuf;
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, State};

pub use crate::ce_core::CeCoreState as CeMcpState;

fn config_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    // Per-user app config dir; the working directory is unreliable in a bundled app.
    Ok(app.path().app_config_dir().map_err(|e| e.to_string())?.join("ce_mcp_config.json"))
}

/// The shipped macOS MCP server: bundled resource in release, source tree in `tauri dev`.
fn bundled_mac_script(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(p) = app.path().resolve("resources/mac_memory_mcp.py", BaseDirectory::Resource) {
        if p.exists() {
            return Some(p);
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/mac_memory_mcp.py");
    dev.exists().then_some(dev)
}

#[tauri::command]
pub async fn ce_get_config(app: AppHandle, state: State<'_, CeMcpState>) -> Result<CeAppConfig, String> {
    // Do not overwrite the live AI connection with stale on-disk settings while connected.
    if state.mcp.lock().await.is_some() {
        let mut current = state.config.lock().await.clone();
        current.llm_api_key.clear(); // never reveal a live provider credential to the configuration screen
        return Ok(current);
    }
    if let Some(parsed) = ce_core::read_config_file(&config_file_path(&app)?) {
        *state.config.lock().await = parsed.clone();
        return Ok(parsed);
    }
    Ok(state.config.lock().await.clone())
}

#[tauri::command]
pub async fn ce_save_config(app: AppHandle, new_cfg: CeAppConfig, state: State<'_, CeMcpState>) -> Result<String, String> {
    if state.mcp.lock().await.is_some() {
        return Err("Disconnect the memory helper in Tasks before changing its settings.".into());
    }
    // Provider credentials are stored in the app keychain. A bridge configuration
    // file must not inherit any copied API key from the live Settings connection.
    let mut persisted = new_cfg.clone();
    persisted.llm_api_key.clear();
    ce_core::write_config_file(&config_file_path(&app)?, &persisted)?;
    *state.config.lock().await = new_cfg;
    Ok("saved".into())
}

/// Apply the selected app-wide provider to the memory agent for this session.
/// Do not write model credentials into the legacy bridge JSON config.
#[tauri::command]
pub async fn ce_sync_ai(base_url: String, model: String, api_key: String, system_prompt: String,
                        state: State<'_, CeMcpState>) -> Result<String, String> {
    let parsed = reqwest::Url::parse(&base_url).map_err(|_| "Invalid AI server address".to_owned())?;
    if !matches!(parsed.scheme(), "https" | "http") || parsed.host_str().is_none()
        || (parsed.scheme() == "http" && !matches!(parsed.host_str(),Some("localhost"|"127.0.0.1"|"::1"))) {
        return Err("Use HTTPS for a remote AI server, or HTTP on this Mac only".into());
    }
    if model.trim().is_empty() || model.len()>256 || api_key.len()>4096 || system_prompt.len()>12000 {
        return Err("Invalid AI connection settings".into());
    }
    let mut cfg = state.config.lock().await;
    cfg.llm_base_url = base_url;
    cfg.llm_model = model;
    cfg.llm_api_key = api_key;
    cfg.system_prompt = system_prompt;
    Ok("Using the AI selected in Settings for this session".into())
}

#[tauri::command]
pub async fn ce_connect_mcp(app: AppHandle, state: State<'_, CeMcpState>) -> Result<Vec<CeMcpTool>, String> {
    crate::execution_control::require_running()?;
    ce_core::connect(&state, bundled_mac_script(&app)).await
}

/// User-requested mapping workflow setup. It does not choose or attach a target.
#[tauri::command]
pub async fn ce_prepare_mapping_chat(app: AppHandle, state: State<'_, CeMcpState>) -> Result<Value,String> {
    crate::execution_control::require_running()?;
    if state.mcp.lock().await.is_none() {
        if let Some(config) = ce_core::read_config_file(&config_file_path(&app)?) {
            *state.config.lock().await = config;
        }
        if state.config.lock().await.backend != ce_core::BACKEND_MAC {
            return Err("Chat mapping checks currently require the native Mac helper selected in Settings.".into());
        }
        ce_core::connect(&state,bundled_mac_script(&app)).await?;
    }
    if state.config.lock().await.backend != ce_core::BACKEND_MAC {
        return Err("Chat mapping checks currently require the native Mac helper selected in Settings.".into());
    }
    let raw = ce_core::call_tool(&state,"list_mapping_processes",json!({})).await?;
    if raw.starts_with("error:") { return Err(raw); }
    let mut result:Value = serde_json::from_str(&raw).map_err(|_| "Could not read the running program list")?;
    let current = ce_core::call_tool(&state,"get_mapping_context",json!({})).await?;
    result["context"] = serde_json::from_str(&current).unwrap_or(Value::Null);
    Ok(result)
}

#[tauri::command]
pub async fn ce_select_mapping_program(pid:u32, process_instance_id:String, state:State<'_,CeMcpState>) -> Result<Value,String> {
    crate::execution_control::require_running()?;
    ce_core::select_mapping_process(&state,pid,process_instance_id).await
}

#[tauri::command]
pub async fn ce_disconnect_mcp(state: State<'_, CeMcpState>) -> Result<String, String> {
    ce_core::disconnect(&state).await;
    Ok("disconnected".into())
}

/// Operator-initiated single tool call (the Tools tab). Agent-proposed calls never come through here.
#[tauri::command]
pub async fn ce_call_mcp_tool(name: String, arguments: Value, state: State<'_, CeMcpState>) -> Result<String, String> {
    crate::execution_control::require_running()?;
    ce_core::call_tool(&state, &name, arguments).await
}

/// Operator-typed Lua (the Lua tab). The human pressing EXECUTE is the approval.
#[tauri::command]
pub async fn ce_eval_lua(code: String, state: State<'_, CeMcpState>) -> Result<String, String> {
    crate::execution_control::require_running()?;
    ce_core::call_tool(&state, "evaluate_lua", json!({ "code": code })).await
}

/// Starts an agent run. Returns when the run finishes or parks on a call that needs approval.
#[tauri::command]
pub async fn ce_run_agent_prompt(prompt: String, state: State<'_, CeMcpState>) -> Result<CeAgentOutcome, String> {
    crate::execution_control::require_running()?;
    ce_core::agent_start(&state, prompt).await
}

/// Approve or deny the single call a parked run is waiting on, then continue the run.
#[tauri::command]
pub async fn ce_resolve_approval(approve: bool, state: State<'_, CeMcpState>) -> Result<CeAgentOutcome, String> {
    crate::execution_control::require_running()?;
    ce_core::agent_resolve(&state, approve).await
}

#[tauri::command]
pub async fn ce_retry_agent(state: State<'_, CeMcpState>) -> Result<CeAgentOutcome, String> {
    crate::execution_control::require_running()?;
    ce_core::agent_retry(&state).await
}

#[tauri::command]
pub async fn ce_cancel_agent(state: State<'_, CeMcpState>) -> Result<String, String> {
    ce_core::agent_cancel(&state).await;
    Ok("cancelled".into())
}

/// Reports the actual backend, a live ping and its advertised capabilities.
#[tauri::command]
pub async fn ce_connection_health(state: State<'_, CeMcpState>) -> Result<Value,String> {
    Ok(ce_core::connection_health(&state).await)
}

/// Read-only environment report (privileges, Python, script, LLM endpoint). Changes nothing.
#[tauri::command]
pub async fn ce_preflight(app: AppHandle, state: State<'_, CeMcpState>) -> Result<Vec<CePreflightItem>, String> {
    Ok(ce_core::preflight(&state, bundled_mac_script(&app)).await)
}
