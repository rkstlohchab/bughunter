//! Native macOS privacy checks. Installing/listing apps never requires TCC.
//! Only a user-click may trigger screen-capture consent or open System Settings.
use serde_json::{json, Value};

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
