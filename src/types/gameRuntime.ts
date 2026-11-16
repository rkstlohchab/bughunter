// 64-bit numeric values are strings. JavaScript Number cannot safely hold them.
// Shared wire contracts for the game runtime; mirrors
// backend/bughunter/game_runtime/contracts.py and the JSON Schema files in
// contracts/game_runtime/. Field names are snake_case to match the wire.

export const CONTRACT_VERSION = 1;

export type Platform = 'macos' | 'windows' | 'android';

export type PointerWidth = 4 | 8;

export type NativeOpState =
  | 'queued'
  | 'claimed'
  | 'dispatched'
  | 'succeeded'
  | 'failed'
  | 'outcome_unknown'
  | 'cancelled_before_dispatch'
  | 'cancellation_requested';

export type ErrorCode =
  | 'invalid_input'
  | 'busy'
  | 'outcome_unknown'
  | 'stale_recipe'
  | 'target_unavailable'
  | 'executor_epoch_mismatch'
  | 'revision_conflict'
  | 'precondition_failed'
  | 'cleanup_timeout'
  | 'helper_exited'
  | 'unsupported_build'
  | 'permission_denied';

export type ImplStatus =
  | 'not_started'
  | 'working'
  | 'ready_for_review'
  | 'verified'
  | 'failed'
  | 'blocked';

export type TargetStatus =
  | 'not_run'
  | 'static_only'
  | 'runtime_access_blocked'
  | 'runtime_ready'
  | 'feature_verified'
  | 'stale';

export type CheckResult = 'pass' | 'fail' | 'blocked' | 'not_run';

export type SessionState =
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'failed'
  | 'interrupted';

export type VerificationStatus =
  | 'candidate'
  | 'observed'
  | 'restart_verified'
  | 'stale';

export type ValueType =
  | 'u8' | 'u16' | 'u32' | 'u64'
  | 'i8' | 'i16' | 'i32' | 'i64'
  | 'f32' | 'f64' | 'bytes';

export type NativeOpKind =
  | 'read'
  | 'write'
  | 'scan'
  | 'pointer_search'
  | 'tool_control';

export type EffectClass = 'read_only' | 'read_write' | 'mutating';

export type FeatureState =
  | 'requested'
  | 'starting'
  | 'running'
  | 'verifying'
  | 'monitoring'
  | 'stopping'
  | 'stopped'
  | 'failed'
  | 'needs_input'
  | 'blocked';

export type SubStatus = 'pending' | 'pass' | 'fail' | 'not_applicable';

export type EvidenceKind =
  | 'screenshot'
  | 'video'
  | 'log'
  | 'memory_capture'
  | 'model_note'
  | 'report'
  | 'other';

export type PointerStepOp = 'add' | 'deref';

/** Normalised address string:
 *   mod:<module>+0x<hex>  module-relative
 *   abs:0x<hex>           absolute (process-bound)
 *   null                  unresolved/missing
 *   placeholder           deliberate placeholder
 *   +0x0                  legitimate zero offset
 */
export type AddressRepr = string;

export type BuildFingerprint = {
  sha256_main: string;
  sha256_metadata?: string | null;
  extras?: Record<string, unknown>;
};

export type Module = {
  name: string;
  base_rva: AddressRepr;
  size: string; // hex string; 64-bit size values are strings
  sha256: string;
};

export type PointerStep = {
  op: PointerStepOp;
  offset: string; // signed hex string, e.g. "+0x18"
  width: PointerWidth;
};

export type Evidence = {
  id: string;
  kind: EvidenceKind;
  content_type: string;
  path?: string | null;
  sha256?: string | null;
  notes?: string | null;
};

export type GameTarget = {
  id: string;
  game_id: string;
  platform: Platform;
  runtime: string;
  architecture: string;
  pointer_width: PointerWidth;
  executable_or_package: string;
  version: string;
  build_fingerprint: BuildFingerprint;
  modules: Module[];
  capabilities: string[];
};

export type GameSession = {
  id: string;
  target_id: string;
  harness_session_id: string;
  session_generation: number;
  stop_generation: number;
  process_instance_id: string;
  knowledge_collection_ids: string[];
  state: SessionState;
  last_error: string | null;
};

export type MappingRecipe = {
  id: string;
  schema_version: number;
  revision: number;
  target_fingerprint: string;
  name: string;
  value_type: ValueType;
  root: AddressRepr;
  steps: PointerStep[];
  preconditions: string[];
  evidence_ids: string[];
  verification_status: VerificationStatus;
};

export type NativeOperation = {
  id: string;
  request_hash: string;
  session_id: string;
  session_generation: number;
  stop_generation: number;
  process_instance_id: string;
  target_fingerprint: string;
  kind: NativeOpKind;
  effect_class: EffectClass;
  arguments: Record<string, unknown>;
  preconditions: string[];
  status: NativeOpState;
  revision: number;
  executor_id: string | null;
  executor_epoch: number | null;
  lease_expires_at: string | null;
  attempt_id: string | null;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  evidence_ids: string[];
};

export type FeatureRun = {
  id: string;
  session_id: string;
  feature_id: string;
  recipe_revisions: number[];
  configuration: Record<string, unknown>;
  state: FeatureState;
  readback_status: SubStatus;
  behavior_status: SubStatus;
  cleanup_status: SubStatus;
  evidence_ids: string[];
};
