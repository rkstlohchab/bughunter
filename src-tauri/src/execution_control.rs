//! Desktop-wide stop latch, independent of slow backend or model requests.
use std::sync::atomic::{AtomicU64, Ordering};
// Low bit is stopped; remaining bits invalidate stale backend status replies.
static STATE: AtomicU64 = AtomicU64::new(0);
pub fn revision() -> u64 { STATE.load(Ordering::SeqCst) }
pub fn stop() { let _ = STATE.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| Some((n.wrapping_add(2)) | 1)); }
pub fn resume() { let _ = STATE.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| Some((n.wrapping_add(2)) & !1)); }
pub fn sync_stopped(expected: u64) { let _ = STATE.compare_exchange(expected, expected | 1, Ordering::SeqCst, Ordering::SeqCst); }
pub fn require_running() -> Result<(), String> {
    if revision() & 1 != 0 { Err("Tasks are stopped. Use Resume tasks in the top bar.".into()) }
    else { Ok(()) }
}

#[cfg(test)]
mod tests {
    #[test]
    fn stop_requires_explicit_resume_and_ignores_stale_status() {
        super::stop(); assert!(super::require_running().is_err());
        let old = super::revision();
        super::resume();
        super::sync_stopped(old);
        assert!(super::require_running().is_ok());
    }
}
