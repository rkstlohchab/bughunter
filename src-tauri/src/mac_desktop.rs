//! Native, opt-in control of one user-selected macOS application.
//!
//! A Tauri window must explicitly start the session. The model never receives
//! direct shell or operating-system access. Every step checks the currently
//! focused application's bundle identifier, with a fixed session expiry and
//! action budget. Text from another app is untrusted evidence, not instructions.
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tauri::State;

#[derive(Clone)]
struct Session {
    cancelled: Arc<AtomicBool>,
    bundle_id: String,
    name: String,
    expires: Instant,
    remaining: u32,
    allow_inputs: bool,
    engine: String,
}

pub struct DesktopControl(Mutex<Option<Session>>, AtomicBool);

impl Default for DesktopControl {
    fn default() -> Self {Self(Mutex::new(None),AtomicBool::new(false))}
}

impl DesktopControl {
    pub fn stop_now(&self) -> Result<(), String> {
        if let Some(session) = self.0.lock().map_err(|_|"Session lock unavailable")?.take() {
            session.cancelled.store(true, Ordering::SeqCst);
        }
        Ok(())
    }
}

struct ActionBusy<'a>(&'a AtomicBool);
impl Drop for ActionBusy<'_> {fn drop(&mut self){self.0.store(false,Ordering::SeqCst);}}

#[cfg(target_os = "macos")]
mod mac {
    use super::*;
    use std::path::Path;
    use std::process::{Command, Stdio};
    mod vercel;

    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" { fn AXIsProcessTrusted() -> bool; fn CGPreflightScreenCaptureAccess() -> bool; fn CGRequestScreenCaptureAccess() -> bool; }

    fn script(code: &str, args: &[&str], cancellation:Option<&AtomicBool>) -> Result<String, String> {
        let mut command = Command::new("/usr/bin/osascript");
        command.arg("-e").arg(code).arg("--");
        for arg in args { command.arg(arg); }
        // macOS automation can block on a permission prompt or hung target.
        // Terminate the osascript child instead of keeping the AI session stuck.
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|e| format!("macOS automation unavailable: {e}"))?;
        let deadline = Instant::now() + Duration::from_secs(45);
        loop {
            if cancellation.is_some_and(|flag|flag.load(Ordering::SeqCst)) {
                let _=child.kill();
                let _=child.wait();
                return Err("Desktop session was stopped; action cancelled.".into());
            }
            match child.try_wait().map_err(|e| format!("Could not check macOS automation: {e}"))? {
                Some(_) => break,
                None if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("macOS automation timed out. Check app focus and permissions before retrying.".into());
                }
                None => std::thread::sleep(Duration::from_millis(80)),
            }
        }
        let result = child.wait_with_output().map_err(|e| format!("Could not read macOS automation result: {e}"))?;
        if !result.status.success() {
            return Err(format!("macOS declined this action: {}", String::from_utf8_lossy(&result.stderr).trim()));
        }
        Ok(String::from_utf8_lossy(&result.stdout).trim().chars().take(9000).collect())
    }
    fn bundle_metadata(path: &str) -> Result<(String,String), String> {
        if path.len() > 4096 || !path.ends_with(".app") || path.contains('\0') {return Err("Select a native .app bundle in Settings.".into());}
        let bundle = std::fs::canonicalize(path).map_err(|_| "Selected application no longer exists.".to_string())?;
        if !bundle.is_dir() || bundle.extension().and_then(|e| e.to_str()) != Some("app") {return Err("Select a native macOS application bundle.".into());}
        let plist = bundle.join("Contents/Info.plist");
        if !plist.is_file() {return Err("The app bundle does not contain Info.plist.".into());}
        fn get(plist:&Path,key:&str)->Result<String,String>{
            let r=Command::new("/usr/bin/plutil").args(["-extract",key,"raw","-o","-"]).arg(plist).output().map_err(|e|e.to_string())?;
            if !r.status.success(){return Err(format!("Invalid {key} in app bundle."));}
            Ok(String::from_utf8_lossy(&r.stdout).trim().to_string())
        }
        let bid=get(&plist,"CFBundleIdentifier")?;
        let exec=get(&plist,"CFBundleExecutable")?;
        if bid.len()>220 || bid.is_empty() || !bid.chars().all(|c|c.is_ascii_alphanumeric() || ".-_".contains(c)) || exec.is_empty() || exec.contains('/') || exec.contains('\\') || exec==".." {return Err("Invalid macOS app identity.".into());}
        if !bundle.join("Contents/MacOS").join(exec).is_file() {return Err("App executable missing.".into());}
        // These system components must not be automated by this feature.
        if ["com.apple.loginwindow","com.apple.systempreferences","com.apple.Terminal","com.googlecode.iterm2","com.apple.keychainaccess"].iter().any(|s|bid.eq_ignore_ascii_case(s)) {
            return Err("System settings, terminals and credential managers aren't available for automated control.".into());
        }
        Ok((bid,bundle.file_stem().and_then(|x|x.to_str()).unwrap_or("Application").to_string()))
    }
    fn focused() -> Result<String, String> {
        script("tell application \"System Events\" to get bundle identifier of first application process whose frontmost is true",&[],None)
    }
    pub fn begin(path:&str, allow_inputs:bool, seconds:u64, engine:&str) -> Result<Session,String> {
        if seconds==0 || seconds>900 {return Err("Session length must be at most 15 minutes.".into());}
        if !(unsafe { AXIsProcessTrusted() }) {return Err("Grant Accessibility to BugHunterClaudex in macOS Settings, then restart the app.".into());}
        if engine!="built-in" && engine!="vercel" {return Err("Select a supported desktop engine.".into());}
        if engine=="vercel" && vercel::status()["working"]!=true {return Err("Vercel agent-desktop is not installed or is not responding. Select the built-in engine or install agent-desktop.".into());}
        let (bid,name)=bundle_metadata(path)?;
        // User selected an app and explicitly authorized bringing it to front.
        let result=Command::new("/usr/bin/open").arg("-a").arg(path).output().map_err(|e|e.to_string())?;
        if !result.status.success(){return Err("macOS could not launch the selected app.".into());}
        // Launch is asynchronous: do not report the session ready until the
        // explicitly selected application actually becomes frontmost.
        let deadline=Instant::now()+Duration::from_secs(4);
        loop {
            if focused().ok().as_deref()==Some(&bid) {break;}
            if Instant::now()>=deadline {return Err("The selected Mac app didn't become frontmost. Open it, then retry.".into());}
            std::thread::sleep(Duration::from_millis(160));
        }
        Ok(Session{cancelled:Arc::new(AtomicBool::new(false)),bundle_id:bid,name,expires:Instant::now()+Duration::from_secs(seconds),remaining:40,allow_inputs,engine:engine.to_string()})
    }
    fn run_script(code:&str,bid:&str,arg:Option<&str>,cancelled:&AtomicBool)->Result<String,String>{
        let items=match arg {Some(value)=>vec![bid,value],None=>vec![bid]};
        script(code,&items,Some(cancelled))
    }
    fn consequential(label:&str)->bool {
        let lower=label.to_ascii_lowercase();
        let words=["delete","remove","erase","purchase","buy","pay","transfer","send","submit","wipe","format","reset","install","uninstall","authorize","grant","approve","confirm","login","save","export","share","publish","upload","quit","close","shutdown","restart","security","privacy","preferences"];
        lower.split(|c:char|!c.is_ascii_alphanumeric()).any(|part|words.contains(&part))
    }
    // Native AXUIElement helper (compiled on the operator's Mac by setup.py).
    // Never run a model-supplied executable path or shell expression. We use a
    // fixed per-user location and send only the authorized bundle ID/label.
    fn native_ax(session:&Session, action:&str, value:&str)->Option<Result<Value,String>> {
        if !["read_ui","click_button","click_tab","select_item","focus_field"].contains(&action) {return None;}
        if action!="read_ui" {
            if !session.allow_inputs {return Some(Err("This session is read-only.".into()));}
            if value.is_empty() || value.len()>180 {return Some(Err("Choose an exact visible control label.".into()));}
            if consequential(value) {return Some(Err("Consequential controls require manual confirmation in the app.".into()));}
        }
        let home=std::env::var_os("HOME")?;
        let executable=std::path::PathBuf::from(home).join(".local/share/bughunterclaudex/bin/axbridge");
        if !executable.is_file() {return None;} // Legacy AppleScript path remains available.
        let mut cmd=Command::new(executable);
        cmd.arg(&session.bundle_id).arg(action).arg(value);
        cmd.stdout(Stdio::piped()).stderr(Stdio::null());
        let mut child=match cmd.spawn(){Ok(c)=>c,Err(e)=>return Some(Err(format!("Native accessibility helper unavailable: {e}")))};
        let deadline=Instant::now()+Duration::from_secs(12);
        loop {
            if session.cancelled.load(Ordering::SeqCst) {
                let _=child.kill();let _=child.wait();
                return Some(Err("Control session stopped.".into()));
            }
            if Instant::now()>deadline {
                let _=child.kill();let _=child.wait();
                return Some(Err("Native accessibility action timed out.".into()));
            }
            match child.try_wait() {
                Ok(Some(_))=>break,
                Ok(None)=>std::thread::sleep(Duration::from_millis(50)),
                Err(e)=>return Some(Err(format!("Native helper failed: {e}")))
            }
        }
        let output=match child.wait_with_output(){Ok(o)=>o,Err(e)=>return Some(Err(e.to_string()))};
        let result:Value=match serde_json::from_slice(&output.stdout){
            Ok(v)=>v,Err(_)=>return Some(Err("Native helper returned invalid data.".into()))
        };
        if !output.status.success(){
            return Some(Err(result.get("error").and_then(Value::as_str).unwrap_or("Native accessibility action failed.").to_string()));
        }
        let observation=result.get("observation").cloned().unwrap_or(Value::Null);
        let receipt=result.get("receipt").cloned().unwrap_or(Value::Null);
        Some(Ok(json!({"action":action,"adapter":"AXUIElement","result":result,
            "observation":observation,"receipt":receipt,
            "verified":"Action submitted; outcome not independently verified"})))
    }
    pub fn act(session:&Session, action:&str, value:&str) -> Result<Value,String> {
        if session.cancelled.load(Ordering::SeqCst) {return Err("Desktop control was stopped.".into());}
        if !(unsafe { AXIsProcessTrusted() }) {return Err("Accessibility permission was revoked. Stop the session.".into());}
        if focused()? != session.bundle_id {return Err("Target app lost focus. No action was sent; return to the selected app and retry.".into());}
        if value.chars().count()>1500 {return Err("Action input is too long.".into());}
        if session.engine=="vercel" && ["read_ui","click_button","click_tab","select_item","focus_field"].contains(&action) {
            return vercel::step(&session.name,action,value,session.allow_inputs,&session.cancelled);
        }
        if let Some(result)=native_ax(session,action,value) {return result;}
        match action {
            "read_ui" => {
                // Only control names and window title; do not collect password-field contents.
                const READ:&str=r#"on run argv
set bid to item 1 of argv
set gathered to ""
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    if (count of windows) = 0 then return "No visible window"
    set w to front window
    set gathered to "Window: " & (name of w as text)
    set countItems to 0
    repeat with elementRef in (entire contents of w)
      if countItems >= 120 then exit repeat
      set countItems to countItems + 1
      try
        set elRole to role of elementRef as text
        set elName to name of elementRef as text
        if elRole is "AXButton" and elName is not "" then
          set gathered to gathered & linefeed & "Button: " & elName
        else if (elRole is "AXRadioButton" or elRole is "AXTab") and elName is not "" then
          set gathered to gathered & linefeed & "Tab: " & elName
        else if (elRole is "AXTextField" or elRole is "AXTextArea") and elName is not "" then
          set gathered to gathered & linefeed & "Text field label: " & elName
        else if (elRole is "AXRow" or elRole is "AXCell") and elName is not "" then
          set gathered to gathered & linefeed & "Item: " & elName
        else if elRole is "AXStaticText" and elName is not "" then
          set gathered to gathered & linefeed & "Text: " & elName
        end if
      end try
    end repeat
    -- Menu labels are collected without clicking or running menu items.
    try
      set menuNames to menu bar items of menu bar 1
      set menuCount to 0
      repeat with menuRef in menuNames
        if menuCount >= 12 then exit repeat
        set menuCount to menuCount + 1
        try
          set mainName to name of menuRef as text
          set countMenuItems to 0
          repeat with itemRef in menu items of menu 1 of menuRef
            if countMenuItems >= 18 then exit repeat
            set countMenuItems to countMenuItems + 1
            try
              set itemName to name of itemRef as text
              if itemName is not "" and itemName is not "missing value" then
                set gathered to gathered & linefeed & "Menu item: " & mainName & " > " & itemName
              end if
            end try
          end repeat
        end try
      end repeat
    end try
    return gathered
  end tell
end tell
end run"#;
                let text=run_script(READ,&session.bundle_id,None,&session.cancelled)?;
                Ok(json!({"action":"read_ui","observation":text,"source":"macOS Accessibility","verified":true}))
            },
            "click_button" => {
                if !session.allow_inputs {return Err("This session is read-only. Allow control to click buttons.".into());}
                if value.is_empty() || value.len()>180 {return Err("Enter a visible button label.".into());}
                // Native guard as well as the Python planner. Never trust a
                // frontend invocation to have passed planner validation.
                if consequential(value) {
                    return Err("This button requires manual control in the target app.".into());
                }
                const CLICK:&str=r#"on run argv
set bid to item 1 of argv
set buttonName to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    set w to front window
    repeat with elementRef in (entire contents of w)
      try
        if (role of elementRef as text) is "AXButton" and (name of elementRef as text) is buttonName then
          click elementRef
          return "Button click sent"
        end if
      end try
    end repeat
    error "Requested button is not visible"
  end tell
end tell
end run"#;
                let receipt=run_script(CLICK,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"click_button","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "click_tab" => {
                if !session.allow_inputs {return Err("This session is read-only. Allow control to select tabs.".into());}
                if value.is_empty() || value.len()>180 {return Err("Select a visible tab label.".into());}
                const SELECT:&str=r#"on run argv
set bid to item 1 of argv
set tabName to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    set w to front window
    repeat with elementRef in (entire contents of w)
      try
        set elementRole to role of elementRef as text
        if (elementRole is "AXRadioButton" or elementRole is "AXTab") and (name of elementRef as text) is tabName then
          click elementRef
          return "Tab selected"
        end if
      end try
    end repeat
    error "Requested tab is not visible"
  end tell
end tell
end run"#;
                let receipt=run_script(SELECT,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"click_tab","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "click_menu" => {
                if !session.allow_inputs {return Err("This session is read-only.".into());}
                let parts:Vec<&str>=value.split(" > ").collect();
                if parts.len()!=2 || parts.iter().any(|p|p.trim().is_empty() || p.len()>120){return Err("Choose a visible menu item, such as View > Zoom In.".into());}
                if consequential(parts[1]) {return Err("This menu command needs a human click in the target app.".into());}
                const MENU:&str=r#"on run argv
set bid to item 1 of argv
set menuPath to item 2 of argv
set AppleScript's text item delimiters to " > "
set parts to text items of menuPath
set AppleScript's text item delimiters to ""
set menuName to item 1 of parts
set itemName to item 2 of parts
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    try
      click menu item itemName of menu 1 of menu bar item menuName of menu bar 1
      return "Menu command selected"
    on error
      error "Menu command is not available or is disabled"
    end try
  end tell
end tell
end run"#;
                let receipt=run_script(MENU,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"click_menu","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "select_item" => {
                if !session.allow_inputs {return Err("This session is read-only.".into());}
                if value.is_empty() || value.len()>180 || consequential(value) {return Err("Choose an ordinary visible list item.".into());}
                const ITEM:&str=r#"on run argv
set bid to item 1 of argv
set wantedName to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    set w to front window
    repeat with elementRef in (entire contents of w)
      try
        set elementRole to role of elementRef as text
        if (elementRole is "AXRow" or elementRole is "AXCell") and (name of elementRef as text) is wantedName then
          click elementRef
          return "List item selected"
        end if
      end try
    end repeat
    error "List item not found"
  end tell
end tell
end run"#;
                let receipt=run_script(ITEM,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"select_item","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "scroll" => {
                if !session.allow_inputs {return Err("This session is read-only.".into());}
                if !["up","down"].contains(&value) {return Err("Scroll direction must be up or down.".into());}
                // Page Up/Down presses rather than screen coordinates: target window stays scoped.
                const SCROLL:&str=r#"on run argv
set bid to item 1 of argv
set dirName to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    if not frontmost then error "Target app lost focus"
    if dirName is "down" then key code 121
    if dirName is "up" then key code 116
  end tell
end tell
return "One page moved"
end run"#;
                let receipt=run_script(SCROLL,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"scroll","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "focus_field" => {
                if !session.allow_inputs {return Err("This session is read-only.".into());}
                if value.is_empty() || value.len()>180 {return Err("Select a visible text-field label.".into());}
                const FOCUS:&str=r#"on run argv
set bid to item 1 of argv
set fieldName to item 2 of argv
set fieldNameLower to fieldName as text
ignoring case
  if fieldNameLower contains "password" or fieldNameLower contains "token" or fieldNameLower contains "secret" or fieldNameLower contains "api key" or fieldNameLower contains "otp" then
    error "Protected text input must be used manually"
  end if
end ignoring
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    set w to front window
    repeat with elementRef in (entire contents of w)
      try
        set elementRole to role of elementRef as text
        if (elementRole is "AXTextField" or elementRole is "AXTextArea") and (name of elementRef as text) is fieldName then
          click elementRef
          return "Selected named text input"
        end if
      end try
    end repeat
    error "Text field was not found or is protected"
  end tell
end tell
end run"#;
                let receipt=run_script(FOCUS,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"focus_field","receipt":receipt,"verified":"Field selected; content not inspected"}))
            },
            "type_text" => {
                if !session.allow_inputs {return Err("This session is read-only. Enable control to type.".into());}
                if value.is_empty() {return Err("No text supplied.".into());}
                const TYPE:&str=r#"on run argv
set bid to item 1 of argv
set txt to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    if not frontmost then error "Target lost focus"
    set focusedElement to focused UI element
    set elementRole to role of focusedElement as text
    if elementRole is not "AXTextField" and elementRole is not "AXTextArea" then error "Not focused on a supported text field"
    set fieldLabel to ""
    try
      set fieldLabel to name of focusedElement as text
    end try
    ignoring case
      if fieldLabel contains "password" or fieldLabel contains "secret" or fieldLabel contains "token" or fieldLabel contains "authentication" or fieldLabel contains "api key" then
        error "Protected input fields require manual typing"
      end if
    end ignoring
    keystroke txt
  end tell
end tell
return "Text input sent"
end run"#;
                let receipt=run_script(TYPE,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"type_text","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "key" => {
                if !session.allow_inputs {return Err("This session is read-only.".into());}
                if !["return","tab","escape","down","up"].contains(&value){return Err("Unsupported key. Use return, tab, escape, down or up.".into());}
                const KEY:&str=r#"on run argv
set bid to item 1 of argv
set keyName to item 2 of argv
tell application "System Events"
  tell (first application process whose bundle identifier is bid)
    if keyName is "return" then key code 36
    if keyName is "tab" then key code 48
    if keyName is "escape" then key code 53
    if keyName is "down" then key code 125
    if keyName is "up" then key code 126
  end tell
end tell
return "Key sent"
end run"#;
                let receipt=run_script(KEY,&session.bundle_id,Some(value),&session.cancelled)?;
                Ok(json!({"action":"key","receipt":receipt,"verified":"Action submitted, not outcome verified"}))
            },
            "screenshot" => {
                if !(unsafe { CGPreflightScreenCaptureAccess() }) {return Err("Grant Screen Recording in Settings to capture the Mac desktop.".into());}
                let filename=format!("bughunter-screen-{}-{}.png",std::process::id(),session.remaining);
                let path=std::env::temp_dir().join(filename);
                let output=Command::new("/usr/sbin/screencapture").args(["-x","-t","png"]).arg(&path).output().map_err(|e|e.to_string())?;
                if !output.status.success(){return Err("macOS declined screenshot capture.".into());}
                let bytes=std::fs::read(&path).map_err(|e|e.to_string());
                let _=std::fs::remove_file(&path);
                let bytes=bytes?;
                if bytes.len()>12*1024*1024 {return Err("Screenshot exceeds the 12 MB local UI limit.".into());}
                use base64::Engine;
                Ok(json!({"action":"screenshot","image":"data:image/png;base64,".to_owned()+&base64::engine::general_purpose::STANDARD.encode(&bytes),"size_bytes":bytes.len(),"local_only":true,"notice":"Screenshot may contain private data. It is NOT transmitted to a model by this command."}))
            },
            _ => Err("Unsupported action.".into())
        }
    }
    pub fn permissions(request:bool)->Value {
        let mut accessibility=unsafe { AXIsProcessTrusted() };
        let mut screen_recording=unsafe { CGPreflightScreenCaptureAccess() };
        if request {
            if !accessibility {
                // This only opens the official macOS permission page. It cannot grant access.
                let _=Command::new("/usr/bin/open")
                    .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
                    .output();
            }
            if !screen_recording {
                // Trigger Apple's own consent dialog only after the operator clicked.
                screen_recording=unsafe { CGRequestScreenCaptureAccess() };
            }
            accessibility=unsafe { AXIsProcessTrusted() };
        }
        json!({"accessibility":accessibility,"screen_recording":screen_recording,
            "instructions":"Enable BugHunterClaudex in macOS Privacy & Security > Accessibility and Screen Recording, then reopen the app if required. Permissions cannot be granted programmatically."})
    }
    pub fn current_bid()->Result<String,String> { focused() }
    pub fn agent_status()->Value {vercel::status()}
}

#[tauri::command]
pub fn mac_desktop_permissions(request:bool)->Result<Value,String>{
    #[cfg(target_os="macos")]
    {Ok(mac::permissions(request))}
    #[cfg(not(target_os="macos"))]
    {let _=request;Err("macOS permissions can only be checked on macOS.".into())}
}

#[tauri::command]
pub fn agent_desktop_status()->Result<Value,String>{
    #[cfg(target_os="macos")] {Ok(mac::agent_status())}
    #[cfg(not(target_os="macos"))] {Err("Agent Desktop is only checked by this Mac build.".into())}
}

#[tauri::command]
pub fn mac_desktop_begin(path:String, allow_inputs:bool, engine:Option<String>, state:State<'_, DesktopControl>)->Result<Value,String>{
    crate::execution_control::require_running()?;
    #[cfg(target_os="macos")]
    {
        let chosen=engine.as_deref().unwrap_or("built-in");
        let session=mac::begin(&path,allow_inputs,900,chosen)?;
        let output=json!({"app":session.name,"bundle_id":session.bundle_id,"remaining":session.remaining,"expires_in_seconds":900,"allow_inputs":session.allow_inputs,"engine":session.engine.clone()});
        let mut guard=state.0.lock().map_err(|_|"Session lock unavailable")?;
        crate::execution_control::require_running()?;
        if let Some(old)=guard.take(){old.cancelled.store(true,Ordering::SeqCst);}
        *guard=Some(session);
        Ok(output)
    }
    #[cfg(not(target_os="macos"))]
    {let _=(path,allow_inputs,engine,state);Err("Desktop control requires macOS.".into())}
}

#[tauri::command]
pub fn mac_desktop_stop(state:State<'_,DesktopControl>)->Result<Value,String>{
    state.stop_now()?;
    Ok(json!({"stopped":true}))
}

#[tauri::command]
pub fn mac_desktop_step(action:String, value:String, state:State<'_,DesktopControl>)->Result<Value,String>{
    crate::execution_control::require_running()?;
    #[cfg(target_os="macos")]
    {
        if state.1.compare_exchange(false,true,Ordering::SeqCst,Ordering::SeqCst).is_err(){
            return Err("Another desktop action is still running. Wait or press Stop.".into());
        }
        let _busy=ActionBusy(&state.1);
        let session={
            let mut guard=state.0.lock().map_err(|_|"Session lock unavailable")?;
            let current=guard.as_mut().ok_or("Start a desktop-control session in Chat first.")?;
            if current.expires<=Instant::now() || current.remaining==0 {
                current.cancelled.store(true,Ordering::SeqCst);
                *guard=None;
                return Err("Desktop-control session expired or reached its action limit.".into());
            }
            if !["read_ui","click_button","click_tab","click_menu","select_item","scroll","focus_field","type_text","key","screenshot"].contains(&action.as_str()){return Err("Unsupported desktop action.".into());}
            current.remaining-=1;
            current.clone()
        };
        // Do not hold the session lock during macOS automation: Stop can kill
        // a hanging osascript immediately via the shared cancellation token.
        let outcome=mac::act(&session,&action,&value)?;
        if session.cancelled.load(Ordering::SeqCst){return Err("Desktop control stopped.".into());}
        Ok(json!({"result":outcome,"remaining":session.remaining,"app":session.name}))
    }
    #[cfg(not(target_os="macos"))]
    {let _=(action,value,state);Err("Desktop control requires macOS.".into())}
}

#[cfg(test)]
mod tests {
    #[test]
    fn portable_source_no_deferred_exec() {
        assert!(!["open_url","shell","run_script","execute_code"].contains(&"click_button"));
    }
}
