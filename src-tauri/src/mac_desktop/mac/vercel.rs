//! Optional Vercel agent-desktop adapter. Only acts within a session selected in the Tauri UI.
//! It is a UI backend, not an unrestricted CLI/shell exposed to an LLM.
use serde_json::{json, Value};
use std::{io::Read, path::PathBuf, process::{Command, Stdio}, sync::atomic::{AtomicBool, Ordering}, time::{Duration, Instant}};

fn executable() -> Option<PathBuf> {
    let mut candidates=Vec::new();
    if let Some(paths)=std::env::var_os("PATH") {
        candidates.extend(std::env::split_paths(&paths).map(|p|p.join("agent-desktop")));
    }
    candidates.extend(["/opt/homebrew/bin/agent-desktop","/usr/local/bin/agent-desktop"]
        .iter().map(PathBuf::from));
    if let Some(home)=std::env::var_os("HOME") {
        candidates.push(PathBuf::from(home).join(".npm-global/bin/agent-desktop"));
    }
    candidates.into_iter().find(|p|p.is_file())
}

/// All CLI arguments are assembled here, never read from model text.
fn command(args:&[&str], cancelled:&AtomicBool) -> Result<Value,String> {
    let exe=executable().ok_or("Vercel agent-desktop isn't installed. Run setup-mac.sh or npm install -g agent-desktop.")?;
    let mut child=Command::new(exe).args(args)
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn()
        .map_err(|e|format!("Could not run agent-desktop: {e}"))?;
    let mut stdout=child.stdout.take().ok_or("Could not read agent-desktop response")?;
    // Drain concurrently so a large accessibility tree cannot fill the stdout pipe.
    let reader=std::thread::spawn(move || {
        let mut buf=Vec::new();
        let result=stdout.by_ref().take(1_048_576).read_to_end(&mut buf);
        (result,buf)
    });
    let deadline=Instant::now()+Duration::from_secs(20);
    loop {
        if cancelled.load(Ordering::SeqCst) || Instant::now()>=deadline {
            let _=child.kill();let _=child.wait();
            let _=reader.join();
            return Err("agent-desktop stopped or timed out; inspect the target before retrying an action.".into());
        }
        match child.try_wait() {
            Ok(Some(_))=>break,
            Ok(None)=>std::thread::sleep(Duration::from_millis(55)),
            Err(e)=>{let _=child.kill();let _=child.wait();let _=reader.join();return Err(e.to_string())}
        }
    }
    let (read_result,bytes)=reader.join().map_err(|_|"agent-desktop response reader failed")?;
    read_result.map_err(|e|e.to_string())?;
    let parsed:Value=serde_json::from_slice(&bytes)
        .map_err(|_|"agent-desktop did not return a supported JSON envelope".to_string())?;
    if parsed.get("ok").and_then(Value::as_bool)!=Some(true) {
        let err=&parsed["error"];
        let code=err["code"].as_str().unwrap_or("UNKNOWN");
        let message=err["message"].as_str().unwrap_or("Command failed");
        return Err(format!("agent-desktop {code}: {message}. Reobserve the app; do not automatically replay an uncertain action."));
    }
    Ok(parsed)
}

fn allowed_role(role:&str)->Option<&'static str> {
    match role {
        "button" | "menubutton" => Some("Button: "),
        "tab" | "radiobutton" => Some("Tab: "),
        "menuitem" => Some("Menu item: "),
        "treeitem" | "cell" | "listbox" | "option" => Some("Item: "),
        "textfield" | "textarea" | "combobox" => Some("Text field label: "),
        "statictext" | "text" => Some("Text: "),
        _ => None,
    }
}
fn traverse(node:&Value, out:&mut Vec<(String,String,String)>, depth:usize) {
    if depth>20 || out.len()>240 {return;}
    if let (Some(role),Some(name))=(node["role"].as_str(),node["name"].as_str()) {
        if let Some(prefix)=allowed_role(role) {
            let label=name.trim();
            if !label.is_empty() && label.len()<=170 && !label.contains('\n') {
                let reference=node["ref_id"].as_str().unwrap_or("");
                out.push((prefix.to_string(),label.to_string(),reference.to_string()));
            }
        }
    }
    if let Some(children)=node["children"].as_array() {
        for child in children.iter().take(200) {traverse(child,out,depth+1);}
    }
}
fn elements(snapshot:&Value)->Vec<(String,String,String)> {
    let mut out=Vec::new();traverse(&snapshot["data"]["tree"],&mut out,0);out
}
fn snapshot(name:&str,cancelled:&AtomicBool)->Result<Value,String> {
    command(&["snapshot","--app",name,"-i","--compact"],cancelled)
}
fn consequential(label:&str)->bool {
    const WORDS:&[&str]=&["delete","remove","erase","pay","buy","transfer","send","submit","install","uninstall","grant","authorize","approve","login","password","save","export","upload","publish","share","wipe","reset","quit","close","security","privacy"];
    label.split(|c:char|!c.is_ascii_alphanumeric())
        .any(|word| WORDS.iter().any(|w|word.eq_ignore_ascii_case(w)))
}
fn ref_format(reference:&str)->bool {
    reference.len()>=7 && reference.len()<=80 && reference.starts_with('@') && reference.contains(":e") &&
    reference.bytes().all(|b|b.is_ascii_alphanumeric() || matches!(b,b'@'|b':'|b'_'))
}

pub fn step(name:&str,action:&str,value:&str,allow_inputs:bool,cancelled:&AtomicBool)->Result<Value,String> {
    let snap=snapshot(name,cancelled)?;
    if action=="read_ui" {
        let mut output=Vec::new();
        if let Some(title)=snap["data"]["window"]["title"].as_str() {
            output.push(format!("Window: {}",title.replace('\n'," " ).replace('\r'," " ).chars().take(150).collect::<String>()));
        }
        let entries=elements(&snap);
        for (prefix,label,_) in entries.iter().take(120) {output.push(format!("{prefix}{label}"));}
        return Ok(json!({"action":"read_ui","adapter":"vercel-agent-desktop", "observation":output.join("\n"),
            "controls_count":entries.len(),"complete":snap["data"]["complete"],"verified":true}));
    }
    if !allow_inputs {return Err("This session is read-only.".into());}
    let (prefix,operation)=match action {
        "click_button"=>("Button: ","click"),
        "click_tab"=>("Tab: ","click"),
        "select_item"=>("Item: ","click"),
        "focus_field"=>("Text field label: ","focus"),
        _=>return Err("Use the built-in Mac helper for this action.".into()),
    };
    if value.is_empty() || value.len()>170 || consequential(value) {return Err("This control requires manual operation or a shorter exact name.".into());}
    let matches:Vec<String>=elements(&snap).into_iter()
        .filter(|(p,label,r)|p==prefix && label==value && ref_format(r))
        .map(|(_,_,r)|r).collect();
    if matches.len()!=1 {return Err(format!("Found {} matching controls; choose a unique visible label or use the built-in helper.",matches.len()));}
    let result=command(&[operation,&matches[0]],cancelled)?;
    let disposition=&result["data"]["disposition"];
    Ok(json!({"action":action,"adapter":"vercel-agent-desktop","receipt":format!("{operation} delivered to observed control"),
        "delivery":disposition["delivery"],"verified":"Action delivery reported; task outcome needs a fresh observation"}))
}

pub fn status()->Value {
    let present=executable();
    let cancelled=AtomicBool::new(false);
    if present.is_none() {return json!({"installed":false,"working":false,"detail":"Install agent-desktop with setup-mac.sh or npm install -g agent-desktop"});}
    match command(&["version"],&cancelled) {
        Ok(v)=>json!({"installed":true,"working":true,"version":v["data"],"detail":"CLI responds. macOS Accessibility still needs permission."}),
        Err(e)=>json!({"installed":true,"working":false,"detail":e}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extracts_only_known_accessibility_roles() {
        let snapshot=json!({"data":{"tree":{"role":"window","name":"Example","children":[
            {"role":"button","name":"Continue","ref_id":"@s1234:e1"},
            {"role":"textfield","name":"Search","ref_id":"@s1234:e2"},
            {"role":"group","name":"Not clickable","ref_id":"@s1234:e3"}
        ]}}});
        let entries=elements(&snapshot);
        assert_eq!(entries.len(),2);
        assert_eq!(entries[0].0,"Button: ");
        assert_eq!(entries[1].0,"Text field label: ");
    }
    #[test]
    fn rejects_bad_refs_and_consequential_actions() {
        assert!(ref_format("@s1234:e9"));
        assert!(!ref_format("@e9"));
        assert!(!ref_format("@s1234:e9; rm -rf /"));
        assert!(consequential("Delete"));
        assert!(consequential("Authorize Access"));
        assert!(!consequential("Documents"));
    }
}
