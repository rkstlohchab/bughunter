import { useEffect, useState } from 'react';
import { ShieldCheck, RefreshCw } from 'lucide-react';
import { Button } from './components';
import { invoke } from '@tauri-apps/api/core';

type PermissionStatus = {platform:string;screen_recording:boolean;accessibility:boolean;note:string};

export default function MacAccessPanel() {
  const [status,setStatus] = useState<PermissionStatus|null>(null);
  const [busy,setBusy] = useState(false);
  const [agentDesktop,setAgentDesktop] = useState<{installed:boolean;working:boolean;detail?:string}|null>(null);
  const [message,setMessage] = useState('');
  const desktop = !!(window as any).__TAURI_INTERNALS__;
  async function refresh() {
    if (!desktop) {setMessage('Available in the installed Mac desktop app, not the browser preview.');return;}
    try {
      const [mac,engine]=await Promise.all([
        invoke<PermissionStatus>('mac_permission_status'),
        invoke<{installed:boolean;working:boolean;detail?:string}>('agent_desktop_status').catch(()=>null)
      ]);
      setStatus(mac);setAgentDesktop(engine);setMessage('Checked Mac access and desktop control engine.');
    }
    catch(e) {setMessage(`Could not read permission state: ${String(e)}`);}
  }
  useEffect(()=>{if(desktop) void refresh();},[]);
  async function action(kind:string) {
    setBusy(true);setMessage('');
    try {
      setStatus(await invoke<PermissionStatus>('mac_permission_action',{kind}));
      setMessage(kind==='screen_recording'?'macOS processed the recording permission request; a restart may be required.':'Opened the macOS privacy settings. Enable this app explicitly, then recheck.');
    } catch(e) {setMessage(String(e));}
    finally {setBusy(false);}
  }
  return <section className="surface settings-card" aria-label="Mac access">
    <div className="section-title"><div><h2>Mac access</h2><p>Only request access when a task needs it. Detecting installed apps does not require a Mac permission dialog.</p></div><ShieldCheck size={20}/></div>
    <div className="settings-card-body">
      {status?.platform==='macOS' ? <div className="tool-access-list">
        <div className="tool-access-row"><div><strong>Screen recording</strong><p className="muted">{status.screen_recording?'Allowed for this app':'Not granted to this app'} · Needed to inspect other apps’ windows, not to locate installed programs.</p></div><Button disabled={busy} onClick={()=>action(status.screen_recording?'screen_settings':'screen_recording')}>{status.screen_recording?'Open settings':'Request access'}</Button></div>
        <div className="tool-access-row"><div><strong>Accessibility</strong><p className="muted">{status.accessibility?'Allowed for this app':'Not granted to this app'} · Needed for UI control of other apps, not for discovering them.</p></div><Button disabled={busy} onClick={()=>action('accessibility')}>Open settings</Button></div>
      </div> : <p className="muted">{desktop?'Checking macOS permissions…':'Run in the desktop application to check Mac privacy access.'}</p>}
      <div className="tool-access-row"><div><strong>Vercel agent-desktop</strong><p className="muted">{agentDesktop?.working?'Installed and responding — select it in Chat → Control a Mac app.':agentDesktop?.installed?'Installed but not responding. Check installation in Terminal.':'Optional improved computer control. Install with setup-mac.sh.'} The built-in engine remains available.</p></div></div>
      <p className="muted">Process-memory access is controlled separately by macOS, target-process protections and the selected bridge. A Windows Cheat Engine executable does not work as a native macOS bridge. Permissions for child tools may need to be granted to those tools separately.</p>
      <Button disabled={busy} icon={RefreshCw} onClick={()=>void refresh()}>Recheck Mac access</Button>
      {message && <p role="status" className="muted">{message}</p>}
    </div>
  </section>;
}
