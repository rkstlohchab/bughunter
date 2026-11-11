//! Approval policy for the Cheat Engine (CE) bridge agent loop.
//!
//! Pure logic, no I/O. The agent loop in `ce_core.rs` asks this module what to do with every
//! model-proposed tool call *before* anything reaches the MCP server:
//!
//! * `Blocked`       - never executed, even if the operator would approve (shell/file/network escapes).
//! * `NeedsApproval` - parked until the operator approves or denies it in the UI.
//! * `Auto`          - read-only tools, only when `auto_approve_readonly` is on.
//!
//! The denylist is a tripwire, not a sandbox: Lua can obfuscate. The real control is that the
//! operator sees the exact code before it runs.

use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Auto,
    NeedsApproval(String),
    Blocked(String),
}

/// Substrings (checked after normalisation) that are refused outright inside any tool argument.
/// They cover shell execution, file I/O, networking and native-library loading from CE Lua.
pub const K_BLOCKED_PATTERNS: &[&str] = &[
    "os.execute",
    "os.remove",
    "os.rename",
    "os.exit",
    "io.popen",
    "io.open",
    "io.output",
    "package.loadlib",
    "dofile",
    "loadfile",
    "shellexecute",
    "runcommand",
    "getinternet",
    "createprocess",
];

/// Tool-name prefixes treated as read-only when `auto_approve_readonly` is on.
const K_READ_ONLY_PREFIXES: &[&str] = &[
    "read_", "get_", "verify_", "list_", "enum_", "find_", "search_", "dissect_", "disassemble", "analyze_",
    "describe_", "check_", "is_", "resolve_", "lookup_", "ping", "scan_", "next_scan", "watch_",
];

/// Substrings that always force approval, even for a read-only-looking prefix.
const K_ALWAYS_APPROVE: &[&str] = &[
    "lua", "assemble", "write", "patch", "inject", "execute", "exec_", "kill", "terminate", "thread",
    "set_", "change_", "modify", "freeze", "breakpoint", "debug", "load", "create", "alloc", "attach", "open_process",
    "call_function", "script", "memory_record", "save", "detach", "clear_scan",
];

fn collect_strings(v: &Value, out: &mut Vec<String>) {
    match v {
        Value::String(s) => out.push(s.clone()),
        Value::Array(a) => a.iter().for_each(|x| collect_strings(x, out)),
        Value::Object(o) => o.iter().for_each(|(k, x)| {
            out.push(k.clone());
            collect_strings(x, out);
        }),
        _ => {}
    }
}

/// Lowercase, rewrite `x["y"]` / `x['y']` indexing to `x.y`, drop whitespace.
fn normalise(s: &str) -> String {
    s.to_lowercase()
        .replace("[\"", ".")
        .replace("['", ".")
        .replace("\"]", "")
        .replace("']", "")
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect()
}

pub fn blocked_pattern(args: &Value) -> Option<&'static str> {
    let mut strings = Vec::new();
    collect_strings(args, &mut strings);
    strings.iter().find_map(|s| {
        let n = normalise(s);
        K_BLOCKED_PATTERNS.iter().copied().find(|p| n.contains(p))
    })
}

pub fn classify(name: &str, args: &Value, auto_approve_readonly: bool) -> Verdict {
    if let Some(p) = blocked_pattern(args) {
        return Verdict::Blocked(format!(
            "argument contains `{p}`; shell, file, network and native-library access is not allowed from the agent loop. Run it yourself from the Lua tab if you really need it."
        ));
    }
    let lname = name.to_lowercase();
    if let Some(hit) = K_ALWAYS_APPROVE.iter().find(|s| lname.contains(*s)) {
        return Verdict::NeedsApproval(format!("`{name}` can change CE or target state (matched `{hit}`)"));
    }
    let read_only = K_READ_ONLY_PREFIXES.iter().any(|p| lname.starts_with(p));
    if read_only && auto_approve_readonly {
        return Verdict::Auto;
    }
    if read_only {
        Verdict::NeedsApproval(format!("`{name}` is read-only but auto-approve is off"))
    } else {
        Verdict::NeedsApproval(format!("`{name}` is not on the read-only list"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mapping_reads_obey_the_existing_readonly_switch() {
        for name in ["get_mapping_context", "list_mapping_processes", "verify_mapping"] {
            assert_eq!(classify(name, &serde_json::json!({}), true), Verdict::Auto);
            assert!(matches!(classify(name, &serde_json::json!({}), false), Verdict::NeedsApproval(_)));
        }
    }
    use serde_json::json;

    #[test]
    fn lua_always_needs_approval() {
        let v = classify("evaluate_lua", &json!({"code": "return 1"}), true);
        assert!(matches!(v, Verdict::NeedsApproval(_)));
    }

    #[test]
    fn read_tools_auto_only_when_enabled() {
        assert_eq!(classify("read_memory", &json!({"address": "0x10"}), true), Verdict::Auto);
        assert!(matches!(classify("read_memory", &json!({}), false), Verdict::NeedsApproval(_)));
    }

    #[test]
    fn verified_scan_change_always_requires_approval() {
        let decision=classify("change_unique_scan_result", &json!({"expected_value":100,"new_value":500}),true);
        assert!(matches!(decision,Verdict::NeedsApproval(_)));
    }

    #[test]
    fn write_beats_read_prefix() {
        assert!(matches!(classify("get_and_write_memory", &json!({}), true), Verdict::NeedsApproval(_)));
    }

    #[test]
    fn unknown_tools_need_approval() {
        assert!(matches!(classify("frobnicate", &json!({}), true), Verdict::NeedsApproval(_)));
    }

    #[test]
    fn shell_escape_is_blocked_even_when_obfuscated() {
        for code in [
            "os.execute('calc')",
            "OS . Execute ('calc')",
            "os[\"execute\"]('calc')",
            "local f = io.popen('dir')",
            "shellExecute('cmd')",
            "getInternet().getURL('http://x')",
        ] {
            let v = classify("evaluate_lua", &json!({ "code": code }), true);
            assert!(matches!(v, Verdict::Blocked(_)), "not blocked: {code}");
        }
    }

    #[test]
    fn blocked_scans_nested_values_and_keys() {
        let v = classify("read_memory", &json!({"a": [{"b": "io.open('x')"}]}), true);
        assert!(matches!(v, Verdict::Blocked(_)));
    }

    #[test]
    fn mac_native_tool_classes() {
        let a = json!({});
        for t in ["ping", "check_access", "list_processes", "get_process_info", "list_memory_regions", "read_memory",
                  "read_pointer_chain", "scan_value", "next_scan", "get_scan_results", "list_frozen"] {
            assert_eq!(classify(t, &a, true), Verdict::Auto, "{t}");
        }
        for t in ["attach_process", "attach_mapping_process", "write_memory", "freeze_value", "unfreeze_value", "detach_process", "clear_scan"] {
            assert!(matches!(classify(t, &a, true), Verdict::NeedsApproval(_)), "{t}");
        }
    }

    #[test]
    fn plain_lua_is_not_blocked() {
        assert!(blocked_pattern(&json!({"code": "return readInteger(0x1000)"})).is_none());
    }
}
