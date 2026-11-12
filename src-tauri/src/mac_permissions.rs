//! Native macOS privacy checks. Installing/listing apps never requires TCC.
//! Only a user-click may trigger screen-capture consent or open System Settings.
use serde_json::{json, Value};

#[cfg(target_os = "macos")]
fn full_disk_status() -> Value {
    // Open a protected file solely to test access. Never read its contents.
    let Some(home) = std::env::var_os("HOME") else { return Value::Null; };
    let paths = [std::path::PathBuf::from(home).join("Library/Application Support/com.apple.TCC/TCC.db"),
        std::path::PathBuf::from("/Library/Application Support/com.apple.TCC/TCC.db")];
    for path in paths {
        match std::fs::File::open(path) {
            Ok(_) => return json!(true),
            Err(_) => {},
        }
    }
    // There is no public FDA preflight API. A probe failure may reflect a
    // different app identity, ordinary file permissions, or a missing database.
    // It does not establish the state of the user's Full Disk Access toggle.
    Value::Null
}

#[cfg(target_os = "macos")]
#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn AXIsProcessTrusted() -> bool;
    fn CGPreflightScreenCaptureAccess() -> bool;
    fn CGRequestScreenCaptureAccess() -> bool;
}

#[tauri::command]
pub fn mac_permission_status() -> Value {
    #[cfg(target_os = "macos")]
    unsafe {
        return json!({
            "platform": "macOS", "screen_recording": CGPreflightScreenCaptureAccess(),
            "accessibility": AXIsProcessTrusted(),
            "full_disk": full_disk_status(),
            "process": std::env::current_exe().ok().map(|p|p.display().to_string()),
            "note": "These permissions apply to this desktop process, not automatically to Python, Wine or other helper apps. App discovery does not require either permission."
        });
    }
    #[cfg(not(target_os = "macos"))]
    json!({"platform":"unsupported", "screen_recording": false, "accessibility": false,
           "note":"This panel is available only on macOS."})
}

#[tauri::command]
pub fn mac_permission_action(kind: String) -> Result<Value, String> {
    #[cfg(target_os = "macos")]
    {
        if kind == "screen_recording" {
            // Calls the system-controlled prompt only after explicit user click.
            unsafe { CGRequestScreenCaptureAccess(); }
            return Ok(mac_permission_status());
        }
        let target = match kind.as_str() {
            "full_disk" => "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
            "accessibility" => "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "screen_settings" => "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            _ => return Err("Unknown macOS permission setting.".into())
        };
        std::process::Command::new("open").arg(target).spawn()
            .map_err(|e| format!("Could not open macOS System Settings: {e}"))?;
        Ok(mac_permission_status())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = kind;
        Err("Native permission controls require macOS.".into())
    }
}
