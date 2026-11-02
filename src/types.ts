/** JSON records shared by desktop views and the Python RPC transport.
 * Python service validation remains authoritative at the execution boundary.
 */
export interface Entity { id: string; created_at?: string; updated_at?: string; [key: string]: any }
export interface Citation { source_id: string; chunk_id?: string; title?: string; locator: string; text?: string }
export interface Source extends Entity { title: string; status: 'queued' | 'processing' | 'ready' | 'partial' | 'error'; chunk_count?: number; semantic_index?: string }
export interface GraphAssertion extends Entity { source_id?: string; chunk_id?: string; kind?: string; text?: string; status: 'imported' | 'inferred' | 'corrected' | 'verified'; locator?: string }
export interface ProjectScope extends Entity { name: string; authorization: string; scope: string[]; exclusions: string[]; read_only_paths: (string | {path: string; methods: string[]})[]; allow_private: boolean; requests_per_second: number; max_concurrency: number; max_requests: number }
export interface Session { project_id: string; session_id: string; description?: string }
export interface HttpRequest { method: string; url: string; headers: Record<string, string>; body: string }
export interface HttpResponse { status: number; headers: Record<string, string>; body: string; truncated?: boolean }
export interface Approval { request_hash: string; session_id: string; expires_at: string; consumed: boolean }
export type ActionStatus = 'ready' | 'pending_approval' | 'approved' | 'executing' | 'completed' | 'failed' | 'blocked' | 'denied' | 'cancelled' | 'unknown';
export interface ActionRequest extends Entity, Session { request: HttpRequest; response?: HttpResponse; status: ActionStatus; effect: 'read_only' | 'state_changing' | 'unknown'; request_hash: string; approval?: Approval; evidence_ids: string[]; run_id?: string; test_id?: string }
export interface Observation extends Entity, Session { request: HttpRequest; response?: HttpResponse; trusted_observation: boolean; origin: string }
export interface TestPlan extends Entity, Session { title: string; category: string; traffic_id: string; field: string; proposed_change: string; proposed_request: HttpRequest; prerequisites: string[]; expected_secure_behavior: string; confirmation_criteria: string; citations: Citation[]; status: string }
export interface Finding extends Entity { project_id: string; title: string; status: 'hypothesis' | 'observed' | 'confirmed' | 'false_positive' | 'inconclusive'; summary: string; impact: string; evidence_ids: string[]; steps: string[]; confirmation?: string; controls?: string; citations?: Citation[] }
export interface ScriptDraft extends Entity { title: string; tool: string; purpose: string; script: string; notes?: string; source_ids: string[]; source_id?: string; source_title?: string; project_id?: string | null; run_id?: string; status: 'draft'; reviewed: boolean; reviewed_at?: string | null }
export interface InvestigationRun extends Entity { project_id: string | null; provider: string; model: string; status: string; prompt: string; summary: string; session_id?: string; error?: string; kind?: string; source_id?: string }

export interface RpcResults {
  'projects.list': ProjectScope[];
  'projects.save': ProjectScope;
  'projects.delete': {id: string; deleted: boolean; library_preserved: boolean; exported_files_preserved: boolean};
  'sources.list': Source[];
  'sources.import': Source;
  'sources.get': Source & { chunks: (Entity & Citation & {text: string})[] };
  'knowledge.search': (Citation & {text: string; score?: number})[];
  'knowledge.graph': {nodes: GraphAssertion[]; edges: Entity[]};
  'traffic.list': Observation[];
  'traffic.get': Observation;
  'tests.list': TestPlan[];
  'tests.propose': TestPlan[];
  'tests.queue': ActionRequest;
  'tests.assess': Finding;
  'actions.list': ActionRequest[];
  'actions.approve': ActionRequest;
  'actions.execute': ActionRequest;
  'findings.list': Finding[];
  'findings.save': Finding;
  'findings.learn': Source;
  'findings.export': {content: string; filename: string; path: string};
  'runs.list': InvestigationRun[];
  'runs.start': InvestigationRun;
  'runs.resume': InvestigationRun;
  'scripts.list': ScriptDraft[];
  'scripts.get': ScriptDraft;
  'scripts.review': ScriptDraft;
  'scripts.delete': {id: string; deleted: boolean};
}
