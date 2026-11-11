//! Receipts come from the fixed native command, not renderer-supplied results.
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

#[derive(Default)]
pub struct Execution(pub tokio::sync::Mutex<()>);

pub fn validate_id(id: &str) -> Result<(), String> {
    let suffix = id.strip_prefix("mapping_check_").ok_or("Invalid mapping check identifier")?;
    if suffix.len() != 32 || !suffix.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err("Invalid mapping check identifier".into());
    }
    Ok(())
}

pub fn save(path: &Path, id: &str, receipt: &Value) -> Result<(), String> {
    validate_id(id)?;
    let parent = path.parent().ok_or("Missing receipt directory")?;
    if path.file_name().and_then(|n| n.to_str()) != Some(&format!("{id}.json"))
        || parent.file_name().and_then(|n| n.to_str()) != Some("mapping-receipts")
        || fs::symlink_metadata(parent).map_err(|e| e.to_string())?.file_type().is_symlink() {
        return Err("Invalid mapping receipt location".into());
    }
    let bytes = serde_json::to_vec(receipt).map_err(|e| e.to_string())?;
    if bytes.len() > 100_000 { return Err("Native readback exceeds receipt size limit".into()); }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)] {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    // Exclusive creation prevents replay from overwriting an earlier observation.
    let mut file = options.open(path).map_err(|e| format!("Cannot save mapping receipt: {e}"))?;
    file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|e| format!("Cannot finish mapping receipt: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identifiers_cannot_escape_receipt_directory() {
        assert!(validate_id("mapping_check_0123456789abcdef0123456789abcdef").is_ok());
        for id in ["../escape", "mapping_check_a", "mapping_check_0123456789abcdef0123456789abcdeG"] {
            assert!(validate_id(id).is_err());
        }
    }
    #[test]
    fn receipts_are_exclusive_and_private() {
        let root = std::env::temp_dir().join(format!("mapping-receipt-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let folder = root.join("mapping-receipts");
        fs::create_dir_all(&folder).unwrap();
        let id = "mapping_check_0123456789abcdef0123456789abcdef";
        let path = folder.join(format!("{id}.json"));
        let receipt = serde_json::json!({"check_id":id});
        save(&path, id, &receipt).unwrap();
        assert!(save(&path, id, &receipt).is_err());
        assert_eq!(serde_json::from_slice::<Value>(&fs::read(&path).unwrap()).unwrap(), receipt);
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        }
        fs::remove_dir_all(root).unwrap();
    }
}
