/** Native Mac tool handoff, driven only by explicit Settings button clicks.
 * Checking whether an app is open never claims a Burp/CE API connection.
 * Nothing in this component is reachable from Chat's model tool protocol.
 */
import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Button } from './components';
import { rpc } from './api';

type NativeStatus = {
  name: string;
  installed: boolean;
  runtime: string;
  running: boolean | null;
  process_check: 'checked' | 'unavailable';
  connected: boolean;
  detail: string;
};
type ProxyListener = {port:number; reachable:boolean; detail:string};
type ToolInfo = { name: string; location?: string | null; installed?: boolean; runtime?: string | null };

export default function MacToolControl({tool,refreshKey,proxyUrl}: {tool:ToolInfo;refreshKey:number|null;proxyUrl?:string}) {
  const desktop = !!(window as any).__TAURI_INTERNALS__;
  const location = typeof tool.location === 'string' ? tool.location : '';
  const nativeApp = tool.installed && tool.runtime === 'macOS application' && location.toLowerCase().endsWith('.app');
  const [live,setLive] = useState<NativeStatus|null>(null);
  const [busy,setBusy] = useState(false);
  const [proxyCheck,setProxyCheck] = useState<ProxyListener|null>(null);
  const [message,setMessage] = useState('');
  const [burpBridge,setBurpBridge] = useState<{connected:boolean;service:string}|null>(null);
  const recheck = useCallback(async () => {
    setMessage('');
    if (!desktop || !nativeApp) {setLive(null);return;}
    try {
      const state = await invoke<NativeStatus>('mac_tool_live_status',{name:tool.name,path:location});
      setLive(state);
    } catch(e) {
      setLive(null);
      setMessage(`Native app check unavailable: ${String(e)}`);
    }
  },[desktop,nativeApp,location,tool.name]);

  useEffect(()=>{void recheck();},[recheck,refreshKey]);
  async function checkLocalProxy() {
    setBusy(true);setProxyCheck(null);setMessage('');
    try {
      const parsed = new URL(proxyUrl || 'http://127.0.0.1:8080');
      if (!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname) || !['http:','https:'].includes(parsed.protocol)) {
        throw new Error('Only a proxy running on this Mac can be checked. Configure a loopback proxy URL.');
      }
      const port = parsed.port ? Number(parsed.port) : parsed.protocol==='https:' ? 443 : 80;
      if (!Number.isInteger(port) || port<1 || port>65535) throw new Error('Invalid proxy port.');
      setProxyCheck(await invoke<ProxyListener>('mac_local_proxy_listener',{port}));
    } catch(e) {setMessage(`Proxy check unavailable: ${String(e)}`);}
    finally {setBusy(false);}
  }
  async function checkBurpBridge(){
    setMessage('Checking authenticated Burp extension…');
    try{const status=await rpc<{connected:boolean;service:string}>('burp.health');setBurpBridge(status);setMessage(status.connected?'Burp Montoya history extension confirmed connected.':'Burp extension did not confirm a connection.');}
    catch(e){setBurpBridge(null);setMessage(`Burp extension not connected: ${String(e)}`);}
  }
  async function launch() {
    if (!nativeApp || !desktop || busy) return;
    setBusy(true);setMessage('');
    try {
      await invoke('mac_tool_open',{name:tool.name,path:location});
      setMessage('macOS accepted the request to open the app. This does not connect to its API or to game memory.');
      await recheck();
    } catch(e) { setMessage(`Could not open app: ${String(e)}`); }
    finally {setBusy(false);}
  }

  return <div className="mac-tool-control">
    {nativeApp ? <>
      <p className="muted">Live app state: <strong>{live?.running===true?'Running':live?.running===false?'Not running (or not visible)':'Not verified'}</strong> · Tool connection: <strong>Not verified</strong></p>
      <div className="button-group wrap">
        <Button disabled={!desktop||busy} onClick={()=>void launch()}>{busy?'Opening…':'Open app'}</Button>
        <Button disabled={!desktop||busy} onClick={()=>void recheck()}>Check running app</Button>
      </div>
    </> : tool.installed && tool.runtime?.includes('Windows') ?
      <p className="muted">Windows Cheat Engine detected. This .exe cannot be opened as a native Mac app. Use a supported Windows environment; the separate Mac memory bridge is not Cheat Engine.</p> :
      <p className="muted">{tool.installed?'The installation is detected, but native app launching is not available for this format.':'Select an installed app above to enable its controls.'}</p>}
    {tool.name === 'Burp Suite' && desktop && <>
      <div className="button-group wrap"><Button disabled={busy} onClick={()=>void checkLocalProxy()}>Check local proxy port</Button><Button disabled={busy} onClick={()=>void checkBurpBridge()}>Check actual Burp connection</Button></div>
      {burpBridge && <p role="status">Burp integration: <strong>{burpBridge.connected?'Connected':'Unavailable'}</strong> ({burpBridge.service})</p>}
      <p className="muted">To read real Burp HTTP history through Chat, build the included Java extension using <code>bash scripts/build_burp_bridge.sh</code> and add the generated JAR under Burp Extensions. Only requests inside Burp scope and your approved BugHunter website project are returned.</p>
      {proxyCheck && <p className="muted" role="status">Local port {proxyCheck.port}: <strong>{proxyCheck.reachable?'Responding':'No listener'}</strong>. {proxyCheck.detail}</p>}
    </>}
    {message && <p role="status" className="muted">{message}</p>}
  </div>;
}
