import { invoke } from "@tauri-apps/api/core";

export type CeBackend = "cheat_engine" | "mac_native";

export interface CeAppConfig {
  backend: CeBackend;
  mac_script_path: string;
  python_bin: string;
  mcp_script_path: string;
  ce_host: string;
  ce_port: string;
  ce_auth_token: string;
  llm_base_url: string;
  llm_api_key: string;
  llm_model: string;
  system_prompt: string;
  auto_approve_readonly: boolean;
}

export interface CeMcpTool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
}

export type CeStepKind =
  | "user"
  | "assistant"
  | "tool_call"
  | "tool_result"
  | "approval_required"
  | "approval_granted"
  | "approval_denied"
  | "blocked"
  | "notice";

export interface CeStepLog {
  kind: CeStepKind;
  name: string;
  payload: string;
}

export interface CeApproval {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  reason: string;
}

export interface CeAgentOutcome {
  status: "done" | "awaiting_approval" | "awaiting_retry" | "invalid_tool_calls" | "step_limit";
  trace: CeStepLog[];
  approval: CeApproval | null;
}

export interface CePreflightItem {
  id: string;
  label: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  fix: string | null;
}

export const isDesktop = () => typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

export interface CeConnectionHealth {
  connected: boolean;
  backend: string;
  backend_label?: string;
  tool_count?: number;
  game_attached?: boolean | null;
  attached_pid?: number;
  heartbeat?: string;
  heartbeat_error?: string;
  access_report?: string;
  capabilities?: Record<string,boolean>;
}

export type MappingProgram = {pid:number;program:string;process_instance_id:string;build_sha256?:string};
export type MappingCheckReply = {check_id:string;saved?:boolean;approval_required?:boolean;request?:Record<string,unknown>;reason?:string};

export const ceMcpApi = {
  getConfig: () => invoke<CeAppConfig>("ce_get_config"),
  saveConfig: (newCfg: CeAppConfig) => invoke<string>("ce_save_config", { newCfg }),
  syncAi: (connection: {base_url:string;model:string;api_key:string;system_prompt:string}) => invoke<string>("ce_sync_ai", {
    baseUrl:connection.base_url, model:connection.model, apiKey:connection.api_key, systemPrompt:connection.system_prompt
  }),
  connectMcp: () => invoke<CeMcpTool[]>("ce_connect_mcp"),
  disconnectMcp: () => invoke<string>("ce_disconnect_mcp"),
  callTool: (name: string, argumentsPayload: Record<string, unknown> = {}) =>
    invoke<string>("ce_call_mcp_tool", { name, arguments: argumentsPayload }),
  evalLua: (code: string) => invoke<string>("ce_eval_lua", { code }),
  runAgentPrompt: (prompt: string) => invoke<CeAgentOutcome>("ce_run_agent_prompt", { prompt }),
  resolveApproval: (approve: boolean) => invoke<CeAgentOutcome>("ce_resolve_approval", { approve }),
  retryAgent: () => invoke<CeAgentOutcome>("ce_retry_agent"),
  cancelAgent: () => invoke<string>("ce_cancel_agent"),
  preflight: () => invoke<CePreflightItem[]>("ce_preflight"),
  connectionHealth: () => invoke<CeConnectionHealth>("ce_connection_health"),
  checkMapping: (checkId: string) => invoke<MappingCheckReply>("ce_check_mapping", { checkId }),
  checkMappingAutomatic: (checkId: string) => invoke<MappingCheckReply>("ce_check_mapping", { checkId, automatic:true }),
  prepareMappingChat: () => invoke<{processes:MappingProgram[];context:MappingProgram|null;truncated?:boolean}>("ce_prepare_mapping_chat"),
  selectMappingProgram: (program:MappingProgram) => invoke<MappingProgram>("ce_select_mapping_program", {pid:program.pid,processInstanceId:program.process_instance_id}),
};
