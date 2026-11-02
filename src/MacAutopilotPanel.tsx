/** User-authorized, one-application desktop agent with a visible stop button.
 * The local model proposes ONLY one UI step at a time; native Rust checks
 * focus, permission and session limits before performing any step.
 */
import { useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { rpc } from './api';
import { Button, Modal } from './components';

type Step = {action:string;value:string;reason?:string;outcome:string};
type Result = {result:{observation?:string;receipt?:string;image?:string};remaining:number;app:string};
const desktop = () => !!(window as any).__TAURI_INTERNALS__;

export default function MacAutopilotPanel({initialTask,projectId,onClose}:{initialTask:string;projectId?:string;onClose:()=>void}) {
  const [path,setPath]=useState('');
  const [engine,setEngine]=useState<'built-in'|'vercel'>('built-in');
  const [vercelState,setVercelState]=useState<{installed:boolean;working:boolean;detail?:string}|null>(null);
  useEffect(()=>{
    if (!desktop()) return;
    void invoke<{installed:boolean;working:boolean;detail?:string}>('agent_desktop_status')
      .then(info=>{setVercelState(info);if(info.working)setEngine('vercel');})
      .catch(()=>setVercelState({installed:false,working:false,detail:'Desktop engine status unavailable.'}));
  },[]);
  const [objective,setObjective]=useState(initialTask.slice(0,2000));
  const [allowInputs,setAllowInputs]=useState(false);
  const [allowUiSharing,setAllowUiSharing]=useState(false);
  const [allowVisibleText,setAllowVisibleText]=useState(false);
  const [active,setActive]=useState(false);
  const [running,setRunning]=useState(false);
  const [awaitingReview,setAwaitingReview]=useState(false);
  const [status,setStatus]=useState('Select a native Mac app to automate.');
  const [permissions,setPermissions]=useState('');
  const [trace,setTrace]=useState<Step[]>([]);
  const [screenshot,setScreenshot]=useState('');
  const [ui,setUi]=useState('');
  const cancel=useRef(false);
  const ledger=useRef<string|null>(null);
  const live=useRef<Step[]>([]);
  const runningRef=useRef(false);

  async function checkPermissions(request:boolean) {
    try {
      const result=await invoke<{accessibility:boolean;screen_recording:boolean;instructions:string}>('mac_desktop_permissions',{request});
      setPermissions(`Accessibility: ${result.accessibility?'granted':'not granted'} · Screen Recording: ${result.screen_recording?'granted':'not granted'}. ${result.instructions}`);
    } catch(e){setPermissions(String(e));}
  }
  async function choose() {
    try {
      const selected=await open({directory:false,multiple:false,title:'Select the Mac app you want to control'});
      if (typeof selected==='string') setPath(selected);
    } catch(e){setStatus(String(e));}
  }
  async function uiHash(value:string) {
    // A repeatable receipt without storing private window text or field values.
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
  }
  async function audit(kind:string,name:string,status?:string) {
    if (!ledger.current)return;
    try{await rpc('tasks.external.event',{id:ledger.current,kind,name,...(status?{status}:{})});}catch{ /* task execution does not depend on audit storage */ }
  }
  async function stop() {
    cancel.current=true;
    setRunning(false);runningRef.current=false;
    try{await invoke('mac_desktop_stop');}catch{}
    setActive(false);setAwaitingReview(false);
    if (ledger.current) {
      await rpc('tasks.external.finish',{id:ledger.current,status:'interrupted',error:'Operator stopped the control session.'}).catch(()=>{});
      ledger.current=null;
    }
    setStatus('Mac control stopped.');
  }
  async function testAppAccess() {
    if (!desktop() || !path || runningRef.current || active) return;
    runningRef.current = true;
    setRunning(true);
    setStatus('Checking macOS Accessibility access and reading this app only…');
    try {
      const session = await invoke<{app:string}>('mac_desktop_begin',{path,allowInputs:false,engine});
      const observation = await invoke<Result>('mac_desktop_step',{action:'read_ui',value:''});
      const description = String(observation.result.observation || '');
      if (!description.trim() || description.includes('No visible window')) {
        throw new Error('The app was opened, but it did not expose a readable window.');
      }
      setUi(description);
      const fields = description.split('\n').filter(line => /^(Button|Tab|Menu item|Item|Text field label):/.test(line));
      setStatus(`macOS verified live Accessibility access to ${session.app}; read ${fields.length} named controls. No actions were sent and no data was shared with AI. This does not prove that a full autonomous task will succeed.`);
    } catch(e) {
      setStatus(`App access test failed: ${String(e)}. Check Settings → Mac permissions and ensure the selected app has an open window.`);
    } finally {
      await invoke('mac_desktop_stop').catch(()=>{});
      runningRef.current = false;
      setRunning(false);
    }
  }

  async function start() {
    if (!desktop()||!path||!objective.trim()||runningRef.current)return;
    cancel.current=false;
    setScreenshot('');setTrace([]);setAwaitingReview(false);live.current=[];
    try{
      const result=await invoke<{app:string;remaining:number}>('mac_desktop_begin',{path,allowInputs,engine});
      setActive(true);
      setStatus(`${result.app} selected. Reading the visible controls…`);
      const task=await rpc<{id:string}>('tasks.external.start',{kind:'desktop_control',title:objective.slice(0,110),project_id:projectId||undefined});
      ledger.current=task.id;
      await run();
    }catch(e){await stop();setStatus(`Could not start: ${String(e)}`);}
  }
  async function run() {
    if (runningRef.current)return;
    runningRef.current=true;setRunning(true);
    let last='';
    let repeat=0;
    let finished=false;
    try {
      for(let i=0;i<14&&!cancel.current;i++){
        const observation=await invoke<Result>('mac_desktop_step',{action:'read_ui',value:''});
        const visible=String(observation.result.observation||'');
        setUi(visible);
        setStatus(`Step ${i+1}/14: asking your configured AI what to do next…`);
        const planned=await rpc<{proposal:{action:string;value:string;reason:string};model:string}>('desktop.next',{
          objective,observation:visible,history:live.current.slice(-10),allow_inputs:allowInputs,allow_visible_text:allowVisibleText
        });
        if(cancel.current)break;
        const a=planned.proposal;
        if(a.action==='done'){
          setStatus(`AI proposes completion: ${a.value || 'No further action proposed.'}. Review the target app, then mark checked.`);
          finished=true;
          await audit('notice','AI stopped proposing actions');
          break;
        }
        if (a.action==='read_ui') {
          repeat++;
          if (repeat>1){setStatus('AI requested another inspection without a new action. Stopping to avoid a loop.');break;}
        }else{repeat=0;}
        const fingerprint=a.action+':'+a.value;
        if(last===fingerprint){setStatus('AI repeated the same step. Stopped to prevent a loop.');break;}
        last=fingerprint;
        const action=await invoke<Result>('mac_desktop_step',{action:a.action,value:a.value});
        const item:Step={action:a.action,value:a.action==='type_text'?'[private text]':a.value,reason:a.reason,outcome:String(action.result.receipt||action.result.observation||'Action accepted')};
        live.current=[...live.current,item].slice(-14);setTrace([...live.current]);
        // Store only the action type and a hash of the UI seen immediately
        // before it. Never save keystrokes, private window text or screenshots.
        if (ledger.current) {
          try {
            await rpc('tasks.external.step',{id:ledger.current,action:a.action,
              observation_hash:await uiHash(visible),native_confirmed:true});
          } catch (auditError) {
            throw new Error(`Could not save the desktop action receipt: ${String(auditError)}`);
          }
        }

      }
      if(!cancel.current){
        if(finished){
          setAwaitingReview(true);
          await audit('notice','AI reported done; human verification pending','awaiting_approval');
        }else{
          if(ledger.current){await rpc('tasks.external.finish',{id:ledger.current,status:'interrupted',error:'Reached the step limit or repeated actions; no completion verified.'}).catch(()=>{});ledger.current=null;}
          // The previous ledger was closed. A follow-up must start an entirely
          // new permissioned session; never continue executing unrecorded steps.
          await invoke('mac_desktop_stop').catch(()=>{});
          setActive(false);
          setStatus(previous=>previous.startsWith('Step ') ? 'Reached the 14-step limit. Review the result, then start a new session if needed.' : previous);
        }
      }
    }catch(e){setStatus(`Stopped safely: ${String(e)}`);await audit('notice','Desktop control error');
      if(ledger.current){await rpc('tasks.external.finish',{id:ledger.current,status:'failed',error:String(e).slice(0,500)}).catch(()=>{});ledger.current=null;}
      await invoke('mac_desktop_stop').catch(()=>{});
      setActive(false);
    }finally{runningRef.current=false;setRunning(false);}
  }
  async function confirmResult() {
    if(!ledger.current || !awaitingReview)return;
    try {
      await rpc('tasks.external.finish',{id:ledger.current,status:'completed'});
      ledger.current=null;
      setAwaitingReview(false);
      setStatus('Task marked completed after your review. The AI action log remains visible below.');
    }catch(e){setStatus(`Could not record verification: ${String(e)}`);}
  }
  async function capture() {
    try{
      const screenshot=await invoke<Result>('mac_desktop_step',{action:'screenshot',value:''});
      setScreenshot(String(screenshot.result.image||''));
      setStatus('Screenshot displayed locally. It was not sent to any AI model.');
    }catch(e){setStatus(String(e));}
  }
  return <Modal title="Control a Mac app" wide onClose={()=>void stop().then(onClose)}>
    <div className="desktop-autopilot">
      <p className="muted">Choose the app and describe your goal. By default, the selected AI receives only visible control labels; screen text is optional and off by default. Screenshots are never sent to the AI. It cannot operate outside the selected app or run shell commands. macOS Accessibility permission is required.</p>
      <div className="button-group wrap"><Button onClick={()=>void checkPermissions(false)}>Check Mac permissions</Button><Button onClick={()=>void checkPermissions(true)}>Open Mac permission settings</Button></div>
      {permissions&&<p role="status" className="muted">{permissions}</p>}
      <label>Desktop control engine
        <select value={engine} onChange={e=>setEngine(e.target.value as 'built-in'|'vercel')} disabled={active||running}>
          <option value="built-in">Built-in Mac control (fallback)</option>
          <option value="vercel" disabled={!vercelState?.working}>Vercel agent-desktop (recommended)</option>
        </select>
      </label>
      <p className="muted">{vercelState?.working ? 'Vercel agent-desktop is available and will use the AI selected in Settings.' : 'Vercel agent-desktop not detected. Install it through setup-mac.sh in Settings/Terminal, or use built-in control.'} No separate AI API key is needed.</p>
      <label>Application location<input value={path} onChange={e=>setPath(e.target.value)} disabled={active||running} placeholder="/Applications/Burp Suite.app" /></label>
      <div className="button-group wrap"><Button disabled={active||running} onClick={()=>void choose()}>Choose an app…</Button><Button disabled={!desktop()||!path||active||running} onClick={()=>void testAppAccess()}>Test app access</Button></div>
      <label>What should the AI do?<textarea value={objective} onChange={e=>setObjective(e.target.value)} disabled={active||running} rows={3}/></label>
      <label className="check-row"><input type="checkbox" checked={allowUiSharing} disabled={active||running} onChange={e=>setAllowUiSharing(e.target.checked)}/>Share visible control labels with the configured AI for this task.</label>
      <label className="check-row"><input type="checkbox" checked={allowVisibleText} disabled={active||running||!allowUiSharing} onChange={e=>setAllowVisibleText(e.target.checked)}/>Also share visible page text and window titles (optional; may contain private information; do not enable for private apps).</label>
      <label className="check-row"><input type="checkbox" checked={allowInputs} disabled={active||running} onChange={e=>setAllowInputs(e.target.checked)}/>Allow clicks and typing in this app for up to 15 minutes (do not use for accounts, payments or secrets)</label>
      <div className="button-group wrap">
        {!active&&<Button variant="primary" disabled={!desktop()||!path||!objective.trim()||!allowUiSharing} onClick={()=>void start()}>Start controlled run</Button>}
        {active&&!awaitingReview&&<Button disabled={running} onClick={()=>void run()}>Continue</Button>}
        {awaitingReview&&<Button variant="primary" onClick={()=>void confirmResult()}>I checked the result</Button>}
        {active&&<Button disabled={running} onClick={()=>void capture()}>Show screen locally</Button>}
        <Button onClick={()=>void stop()}>Stop control</Button>
      </div>
      <p role="status">{status}</p>
      {ui&&<details className="settings-more"><summary>What the AI can see</summary><pre className="desktop-ui-observation">{ui}</pre></details>}
      {screenshot&&<img alt="Local screenshot of the Mac screen" src={screenshot} className="desktop-shot" />}
      {!!trace.length&&<div className="desktop-run-trace"><strong>Executed steps ({trace.length})</strong>{trace.map((s,i)=><p key={i}><strong>{i+1}. {s.action}</strong> {s.value.slice(0,100)} — {s.outcome.slice(0,150)}</p>)}</div>}
    </div>
  </Modal>;
}
