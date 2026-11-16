//! Shared wire contracts for the game runtime.
//!
//! Mirrors `backend/bughunter/game_runtime/contracts.py` and the JSON Schema
//! files in `contracts/game_runtime/`. 64-bit numeric values (addresses,
//! RVAs, offsets, memory sizes) travel as strings so JavaScript `Number`
//! cannot truncate them. Field names are `snake_case` through every binding.
//!
//! Parity with the JSON Schemas is enforced by three mechanisms:
//!
//! 1. Every struct carries `#[serde(deny_unknown_fields)]`, so extra fields
//!    are rejected the same way `additionalProperties: false` rejects them
//!    in the schemas.
//! 2. Fields that the schema marks as required do **not** carry
//!    `#[serde(default)]`. Serde surfaces a missing-field error that matches
//!    the schema's `required` rejection.
//! 3. `validate_record` layers the wire-level bounds the integer types
//!    cannot enforce on their own: `schema_version >= 1`, `revision >= 1`
//!    for mapping recipes, non-empty `request_hash`, sha256 hex format and
//!    the `recipe_revisions` lower bound.
//!
//! Address strings are validated against the same pattern Python and the
//! JSON Schemas use; sha256 fields must be 64 lowercase hex characters.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub fn contract_version() -> u32 {
    1
}

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Platform {
    Macos,
    Windows,
    Android,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NativeOpState {
    Queued,
    Claimed,
    Dispatched,
    Succeeded,
    Failed,
    OutcomeUnknown,
    CancelledBeforeDispatch,
    CancellationRequested,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    InvalidInput,
    Busy,
    OutcomeUnknown,
    StaleRecipe,
    TargetUnavailable,
    ExecutorEpochMismatch,
    RevisionConflict,
    PreconditionFailed,
    CleanupTimeout,
    HelperExited,
    UnsupportedBuild,
    PermissionDenied,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ImplStatus {
    NotStarted,
    Working,
    ReadyForReview,
    Verified,
    Failed,
    Blocked,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TargetStatus {
    NotRun,
    StaticOnly,
    RuntimeAccessBlocked,
    RuntimeReady,
    FeatureVerified,
    Stale,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CheckResult {
    Pass,
    Fail,
    Blocked,
    NotRun,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SessionState {
    Idle,
    Starting,
    Running,
    Stopping,
    Stopped,
    Failed,
    Interrupted,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum VerificationStatus {
    Candidate,
    Observed,
    RestartVerified,
    Stale,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ValueType {
    U8,
    U16,
    U32,
    U64,
    I8,
    I16,
    I32,
    I64,
    F32,
    F64,
    Bytes,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NativeOpKind {
    Read,
    Write,
    Scan,
    PointerSearch,
    ToolControl,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EffectClass {
    ReadOnly,
    ReadWrite,
    Mutating,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FeatureState {
    Requested,
    Starting,
    Running,
    Verifying,
    Monitoring,
    Stopping,
    Stopped,
    Failed,
    NeedsInput,
    Blocked,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SubStatus {
    Pending,
    Pass,
    Fail,
    NotApplicable,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceKind {
    Screenshot,
    Video,
    Log,
    MemoryCapture,
    ModelNote,
    Report,
    Other,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PointerStepOp {
    Add,
    Deref,
}

/// Pointer width in bytes. Only 32- and 64-bit targets are supported.
///
/// `try_from = "u8"` means strict integer parsing: `"4"` (a JSON string) is
/// rejected, keeping parity with the schema's `enum: [4, 8]` and Python's
/// strict `Literal[4, 8]`.
#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(try_from = "u8", into = "u8")]
pub enum PointerWidth {
    Four,
    Eight,
}

impl From<PointerWidth> for u8 {
    fn from(value: PointerWidth) -> u8 {
        match value {
            PointerWidth::Four => 4,
            PointerWidth::Eight => 8,
        }
    }
}

impl TryFrom<u8> for PointerWidth {
    type Error = String;
    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            4 => Ok(PointerWidth::Four),
            8 => Ok(PointerWidth::Eight),
            other => Err(format!("pointer_width must be 4 or 8, got {other}")),
        }
    }
}

// ---------------------------------------------------------------------------
// Sub-records
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct BuildFingerprint {
    pub sha256_main: String,
    /// Optional per schema; keeps a default to stay in parity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256_metadata: Option<String>,
    /// Optional per schema; defaults to empty object.
    #[serde(default)]
    pub extras: HashMap<String, Value>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct Module {
    pub name: String,
    pub base_rva: String,
    pub size: String,
    pub sha256: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct PointerStep {
    pub op: PointerStepOp,
    pub offset: String,
    pub width: PointerWidth,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub id: String,
    pub kind: EvidenceKind,
    pub content_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

// ---------------------------------------------------------------------------
// Top-level records.
//
// Every field the JSON Schema lists as required is a required field here
// too: no `#[serde(default)]` on anything that is schema-required, so a
// missing field is a parse error. Nullable fields use `Option<...>` with
// no default, matching schema `type: ["string", "null"]` with `required`.
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct GameTarget {
    pub id: String,
    pub game_id: String,
    pub platform: Platform,
    pub runtime: String,
    pub architecture: String,
    pub pointer_width: PointerWidth,
    pub executable_or_package: String,
    pub version: String,
    pub build_fingerprint: BuildFingerprint,
    /// Required array; schema rejects a `GameTarget` without `modules`.
    pub modules: Vec<Module>,
    /// Required array; schema rejects a `GameTarget` without `capabilities`.
    pub capabilities: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct GameSession {
    pub id: String,
    pub target_id: String,
    pub harness_session_id: String,
    pub session_generation: u64,
    pub stop_generation: u64,
    pub process_instance_id: String,
    pub knowledge_collection_ids: Vec<String>,
    pub state: SessionState,
    /// Required; may be `null` but the field itself must be present.
    pub last_error: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct MappingRecipe {
    pub id: String,
    /// Wire minimum 1; `validate_record` enforces the bound the integer
    /// type cannot (`u32` happily accepts 0).
    pub schema_version: u32,
    /// Wire minimum 1; `validate_record` enforces the bound.
    pub revision: u32,
    pub target_fingerprint: String,
    pub name: String,
    pub value_type: ValueType,
    pub root: String,
    pub steps: Vec<PointerStep>,
    pub preconditions: Vec<String>,
    pub evidence_ids: Vec<String>,
    pub verification_status: VerificationStatus,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct NativeOperation {
    pub id: String,
    pub request_hash: String,
    pub session_id: String,
    pub session_generation: u64,
    pub stop_generation: u64,
    pub process_instance_id: String,
    pub target_fingerprint: String,
    pub kind: NativeOpKind,
    pub effect_class: EffectClass,
    pub arguments: HashMap<String, Value>,
    pub preconditions: Vec<String>,
    pub status: NativeOpState,
    /// Wire minimum 0; `u32` already refuses negatives.
    pub revision: u32,
    pub executor_id: Option<String>,
    pub executor_epoch: Option<u64>,
    pub lease_expires_at: Option<String>,
    pub attempt_id: Option<String>,
    pub result: Option<Value>,
    pub error: Option<Value>,
    pub evidence_ids: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct FeatureRun {
    pub id: String,
    pub session_id: String,
    pub feature_id: String,
    pub recipe_revisions: Vec<u32>,
    pub configuration: HashMap<String, Value>,
    pub state: FeatureState,
    pub readback_status: SubStatus,
    pub behavior_status: SubStatus,
    pub cleanup_status: SubStatus,
    pub evidence_ids: Vec<String>,
}

// ---------------------------------------------------------------------------
// Cross-language parity validator
// ---------------------------------------------------------------------------

const ADDRESS_PATTERN_HINT: &str =
    "address must match 'mod:<name>+0x<hex>', 'abs:0x<hex>', 'null', 'placeholder' or '+0x0'";

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn is_signed_hex_offset(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    let mut i = 0;
    if bytes[0] == b'+' || bytes[0] == b'-' {
        i += 1;
    }
    if bytes.len() < i + 2 || bytes[i] != b'0' || bytes[i + 1] != b'x' {
        return false;
    }
    let hex = &bytes[i + 2..];
    !hex.is_empty()
        && hex
            .iter()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F'))
}

fn is_unsigned_hex(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3
        && &bytes[..2] == b"0x"
        && bytes[2..]
            .iter()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F'))
}

fn is_address_string(value: &str) -> bool {
    if value == "null" || value == "placeholder" || value == "+0x0" {
        return true;
    }
    if let Some(rest) = value.strip_prefix("mod:") {
        if let Some((name, suffix)) = rest.split_once("+0x") {
            if !name.is_empty()
                && name
                    .bytes()
                    .all(|b| matches!(b, b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'.' | b'_' | b'-'))
                && !suffix.is_empty()
                && suffix
                    .bytes()
                    .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F'))
            {
                return true;
            }
        }
        return false;
    }
    if let Some(suffix) = value.strip_prefix("abs:0x") {
        return !suffix.is_empty()
            && suffix
                .bytes()
                .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F'));
    }
    false
}

fn push_address(errors: &mut Vec<String>, field: &str, value: &str) {
    if !is_address_string(value) {
        errors.push(format!("{field}: {ADDRESS_PATTERN_HINT}; got {value:?}"));
    }
}

fn push_sha256(errors: &mut Vec<String>, field: &str, value: &str) {
    if !is_sha256(value) {
        errors.push(format!(
            "{field}: sha256 must be 64 lowercase hex characters; got {value:?}"
        ));
    }
}

fn validate_build_fingerprint(errors: &mut Vec<String>, prefix: &str, fp: &BuildFingerprint) {
    push_sha256(errors, &format!("{prefix}.sha256_main"), &fp.sha256_main);
    if let Some(meta) = &fp.sha256_metadata {
        push_sha256(errors, &format!("{prefix}.sha256_metadata"), meta);
    }
}

fn validate_module(errors: &mut Vec<String>, prefix: &str, m: &Module) {
    push_address(errors, &format!("{prefix}.base_rva"), &m.base_rva);
    if !is_unsigned_hex(&m.size) {
        errors.push(format!(
            "{prefix}.size: must be an unsigned hex string like '0x1234'; got {:?}",
            m.size
        ));
    }
    push_sha256(errors, &format!("{prefix}.sha256"), &m.sha256);
}

fn validate_game_target(errors: &mut Vec<String>, t: &GameTarget) {
    validate_build_fingerprint(errors, "build_fingerprint", &t.build_fingerprint);
    for (i, m) in t.modules.iter().enumerate() {
        validate_module(errors, &format!("modules[{i}]"), m);
    }
}

fn validate_mapping_recipe(errors: &mut Vec<String>, r: &MappingRecipe) {
    if r.schema_version < 1 {
        errors.push(
            "schema_version: must be a positive integer (>= 1); 0 is not a valid wire value".to_string(),
        );
    }
    if r.revision < 1 {
        errors.push(
            "revision: must be a positive integer (>= 1); 0 is not a valid wire value".to_string(),
        );
    }
    push_address(errors, "root", &r.root);
    for (i, step) in r.steps.iter().enumerate() {
        if !is_signed_hex_offset(&step.offset) {
            errors.push(format!(
                "steps[{i}].offset: must be a signed hex string like '+0x18'; got {:?}",
                step.offset
            ));
        }
    }
}

fn validate_native_operation(errors: &mut Vec<String>, op: &NativeOperation) {
    if op.request_hash.trim().is_empty() {
        errors.push(
            "request_hash: must be a non-empty string; completion receipts cannot fabricate one"
                .to_string(),
        );
    }
}

fn validate_feature_run(errors: &mut Vec<String>, f: &FeatureRun) {
    for (i, rev) in f.recipe_revisions.iter().enumerate() {
        if *rev < 1 {
            errors.push(format!(
                "recipe_revisions[{i}]: must be a positive integer (>= 1); {rev} is not valid"
            ));
        }
    }
}

/// Parse `json_text` as `kind` (snake_case record name) and run every
/// wire-level bound the schema enforces. Returns `Ok(())` when the record
/// is valid, `Err(Vec<String>)` otherwise — the first entry is always a
/// parse-level error when deserialization itself failed. The errors are
/// human-readable and anchored by field name so parity tests across
/// Python/TypeScript/Rust agree on the offending field.
pub fn validate_record(kind: &str, json_text: &str) -> Result<(), Vec<String>> {
    let mut errors: Vec<String> = Vec::new();
    match kind {
        "game_target" => match serde_json::from_str::<GameTarget>(json_text) {
            Ok(v) => validate_game_target(&mut errors, &v),
            Err(e) => errors.push(format!("game_target: {e}")),
        },
        "game_session" => match serde_json::from_str::<GameSession>(json_text) {
            Ok(_) => {}
            Err(e) => errors.push(format!("game_session: {e}")),
        },
        "mapping_recipe" => match serde_json::from_str::<MappingRecipe>(json_text) {
            Ok(v) => validate_mapping_recipe(&mut errors, &v),
            Err(e) => errors.push(format!("mapping_recipe: {e}")),
        },
        "native_operation" => match serde_json::from_str::<NativeOperation>(json_text) {
            Ok(v) => validate_native_operation(&mut errors, &v),
            Err(e) => errors.push(format!("native_operation: {e}")),
        },
        "feature_run" => match serde_json::from_str::<FeatureRun>(json_text) {
            Ok(v) => validate_feature_run(&mut errors, &v),
            Err(e) => errors.push(format!("feature_run: {e}")),
        },
        other => errors.push(format!("unknown record kind: {other}")),
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contract_version_is_one() {
        assert_eq!(contract_version(), 1);
    }

    #[test]
    fn pointer_width_roundtrips() {
        let json = serde_json::to_string(&PointerWidth::Eight).unwrap();
        assert_eq!(json, "8");
        let back: PointerWidth = serde_json::from_str("4").unwrap();
        assert_eq!(back, PointerWidth::Four);
        assert!(serde_json::from_str::<PointerWidth>("3").is_err());
    }
}

#[cfg(test)]
mod parity {
    //! Walks the shared parity fixture bank under
    //! `contracts/game_runtime/fixtures/parity/{valid,invalid}` and asserts
    //! `validate_record` accepts every valid fixture and rejects every
    //! invalid one, matching Python and the JSON Schemas record for record.
    use super::validate_record;
    use std::fs;
    use std::path::{Path, PathBuf};

    fn fixtures_root() -> PathBuf {
        // src-tauri/src/game_runtime_types.rs -> repo root is three levels up.
        let here = Path::new(env!("CARGO_MANIFEST_DIR"));
        here.parent()
            .expect("manifest parent")
            .join("contracts/game_runtime/fixtures/parity")
    }

    fn kind_from_stem(stem: &str) -> String {
        match stem.split_once("__") {
            Some((kind, _)) => kind.to_string(),
            None => stem.to_string(),
        }
    }

    #[test]
    fn walks_valid_fixtures() {
        let dir = fixtures_root().join("valid");
        let mut count = 0usize;
        for entry in fs::read_dir(&dir).expect("read valid dir") {
            let entry = entry.expect("entry");
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let stem = path.file_stem().unwrap().to_str().unwrap().to_string();
            let kind = kind_from_stem(&stem);
            let text = fs::read_to_string(&path).expect("read json");
            let result = validate_record(&kind, &text);
            assert!(
                result.is_ok(),
                "valid fixture {} ({}) was rejected: {:?}",
                path.display(),
                kind,
                result
            );
            count += 1;
        }
        assert!(count >= 5, "expected at least the five canonical valid fixtures, saw {count}");
    }

    #[test]
    fn walks_invalid_fixtures() {
        let dir = fixtures_root().join("invalid");
        let mut count = 0usize;
        for entry in fs::read_dir(&dir).expect("read invalid dir") {
            let entry = entry.expect("entry");
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let stem = path.file_stem().unwrap().to_str().unwrap().to_string();
            let kind = kind_from_stem(&stem);
            let text = fs::read_to_string(&path).expect("read json");
            let result = validate_record(&kind, &text);
            assert!(
                result.is_err(),
                "invalid fixture {} ({}) was wrongly accepted",
                path.display(),
                kind
            );
            count += 1;
        }
        assert!(count >= 12, "expected the full invalid fixture bank, saw {count}");
    }

    #[test]
    fn rejects_mapping_recipe_revision_zero() {
        let text = r#"{
          "id": "x", "schema_version": 1, "revision": 0,
          "target_fingerprint": "a", "name": "n", "value_type": "u32",
          "root": "mod:X+0x1", "steps": [], "preconditions": [],
          "evidence_ids": [], "verification_status": "candidate"
        }"#;
        let err = validate_record("mapping_recipe", text).unwrap_err();
        assert!(err.iter().any(|m| m.contains("revision")), "{err:?}");
    }

    #[test]
    fn rejects_game_target_unknown_field() {
        let text = r#"{
          "id":"t","game_id":"g","platform":"macos","runtime":"r",
          "architecture":"arm64","pointer_width":8,
          "executable_or_package":"/x","version":"1",
          "build_fingerprint":{"sha256_main":"3c1a7f2b4e5d6c7a8b9c0d1e2f3a4b5c6d7e8f9012a3b4c5d6e7f80112233445"},
          "modules":[],"capabilities":[],"nope":"x"
        }"#;
        let err = validate_record("game_target", text).unwrap_err();
        assert!(err.iter().any(|m| m.contains("unknown field")), "{err:?}");
    }
}
