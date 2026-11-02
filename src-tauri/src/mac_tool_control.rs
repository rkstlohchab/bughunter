//! Operator-click-only macOS app handoff.
//!
//! This module must never be callable by the investigation agent or backend RPC.
//! It opens only independently validated, recognized native .app bundles, never
//! arbitrary executables, shell snippets, Wine apps or target processes. Opening
//! an application is not a connection to its API or to process memory.
use serde_json::{json, Value};

fn tool_is_supported(name: &str) -> bool {
    matches!(name, "Burp Suite" | "Cheat Engine")
}

#[cfg(target_os = "macos")]
mod macos {
    use super::*;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::Command;

    fn plist_string(plist: &Path, key: &str) -> Result<String, String> {
        let output = Command::new("/usr/bin/plutil")
            .args(["-extract", key, "raw", "-o", "-"])
            .arg(plist)
            .output()
            .map_err(|_| "Unable to read macOS application metadata.".to_string())?;
        if !output.status.success() {
            return Err(format!("The app is missing its {key} metadata."));
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    }

    fn validated_bundle(name: &str, selected_path: &str) -> Result<(PathBuf, PathBuf), String> {
        if !tool_is_supported(name) { return Err("This app is not on the allowed tool list.".into()); }
        if selected_path.is_empty() || selected_path.len() > 4096 || selected_path.contains('\0') {
            return Err("Select a recognized application in Settings.".into());
        }
        let path = fs::canonicalize(selected_path)
            .map_err(|_| "The selected application no longer exists. Recheck installed tools.".to_string())?;
        if !path.is_dir() || path.extension().and_then(|s| s.to_str()) != Some("app") {
            return Err("Only native macOS .app bundles can be opened here. Windows executables require a separate compatible environment.".into());
        }
        let plist = path.join("Contents/Info.plist");
        if !plist.is_file() { return Err("The selected folder is not a valid macOS app bundle.".into()); }
        let bundle_id = plist_string(&plist, "CFBundleIdentifier")?.to_lowercase();
        let app_name = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
        let recognized = match name {
            "Burp Suite" => (bundle_id.contains("portswigger") || bundle_id.contains("burpsuite")) &&
                (app_name.contains("burp") || app_name.contains("portswigger")),
            "Cheat Engine" => (bundle_id.contains("cheatengine") || bundle_id.contains("cheat.engine") || bundle_id.contains("cheat-engine")) &&
                (app_name.contains("cheat") && app_name.contains("engine")),
            _ => false,
        };
        if !recognized { return Err("The app's bundle identity does not match the selected tool. Use its official native app or choose its correct location.".into()); }
        let executable_name = plist_string(&plist, "CFBundleExecutable")?;
        if executable_name.contains('/') || executable_name.contains('\\') || executable_name == "." || executable_name == ".." || executable_name.is_empty() {
            return Err("Invalid app executable metadata.".into());
        }
        let executable = path.join("Contents/MacOS").join(executable_name);
        if !executable.is_file() { return Err("The application executable is missing.".into()); }
        Ok((path, executable))
    }

    fn running(executable: &Path) -> Result<bool, String> {
        // Exact executable-path match; a similarly named process is not proof.
        let output = Command::new("/bin/ps").args(["-axo", "comm="]).output()
            .map_err(|_| "Unable to check running applications.".to_string())?;
        if !output.status.success() { return Err("macOS did not allow a process-status check.".into()); }
        let expected = executable.to_string_lossy();
        Ok(String::from_utf8_lossy(&output.stdout).lines().any(|line| line.trim() == expected))
    }

    pub fn status(name: &str, selected_path: &str) -> Result<Value, String> {
        let (path, executable) = validated_bundle(name, selected_path)?;
        let current = running(&executable);
        let (running, process_check) = match current {
            Ok(value) => (Some(value), "checked"),
            Err(_) => (None, "unavailable"),
        };
        Ok(json!({"name":name,"installed":true,"runtime":"native macOS app",
            "location":path.to_string_lossy(), "running":running,
            "process_check":process_check, "connected":false,
            "detail":"Running is not the same as a connected API, Burp proxy or memory bridge."}))
    }

    pub fn open_app(name: &str, selected_path: &str) -> Result<Value, String> {
        let (path, _) = validated_bundle(name, selected_path)?;
        let result = Command::new("/usr/bin/open").arg("-a").arg(&path).output()
            .map_err(|e| format!("macOS could not open this application: {e}"))?;
        if !result.status.success() {
            return Err(format!("macOS could not open the selected app: {}", String::from_utf8_lossy(&result.stderr).trim()));
        }
        Ok(json!({"opened":true,"name":name,"detail":"macOS accepted the open request. Recheck to see whether the app started; a tool connection has NOT been established."}))
    }
}

#[tauri::command]
pub fn mac_tool_live_status(name: String, path: String) -> Result<Value, String> {
    if !tool_is_supported(&name) { return Err("Unsupported tool.".into()); }
    #[cfg(target_os = "macos")]
    { macos::status(&name, &path) }
    #[cfg(not(target_os = "macos"))]
    { let _ = path; Err("Native application checks require macOS.".into()) }
}

#[tauri::command]
pub fn mac_tool_open(name: String, path: String) -> Result<Value, String> {
    if !tool_is_supported(&name) { return Err("Unsupported tool.".into()); }
    #[cfg(target_os = "macos")]
    { macos::open_app(&name, &path) }
    #[cfg(not(target_os = "macos"))]
    { let _ = path; Err("Opening Mac apps requires the native macOS desktop.".into()) }
}

/// Only tests whether a TCP listener responds on THIS Mac. This is not
/// proof the listener belongs to Burp or has permission to test any website.
/// No HTTP request or payload is sent.
#[tauri::command]
pub fn mac_local_proxy_listener(port: u16) -> Result<Value, String> {
    if port == 0 {return Err("Enter a valid local proxy port.".into());}
    use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
    use std::time::Duration;
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let reachable = TcpStream::connect_timeout(&address, Duration::from_millis(650)).is_ok();
    Ok(json!({"port":port,"reachable":reachable,
        "detail":if reachable {"Something is listening on this Mac's loopback port. It has NOT been identified as Burp Suite."}
                 else {"No local TCP listener answered on this port. Start Burp's proxy listener and recheck."}}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_desktop_tools_are_supported() {
        assert!(tool_is_supported("Burp Suite"));
        assert!(tool_is_supported("Cheat Engine"));
        for invalid in ["", "Terminal", "bash", "/bin/sh", "SomeGame", "Ghidra", "Burp Suite; open something"] {
            assert!(!tool_is_supported(invalid));
        }
    }
    #[test]
    fn local_proxy_probe_rejects_invalid_port() {
        assert!(mac_local_proxy_listener(0).is_err());
    }
    #[test]
    fn no_native_application_control_on_other_platforms() {
        #[cfg(not(target_os = "macos"))]
        {
            assert!(mac_tool_live_status("Burp Suite".into(), "/Applications/Burp Suite.app".into()).is_err());
            assert!(mac_tool_open("Cheat Engine".into(), "/Applications/Cheat Engine.app".into()).is_err());
        }
    }
}
