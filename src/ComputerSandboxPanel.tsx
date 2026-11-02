import { useEffect, useState } from 'react';
import { AlertTriangle, ShieldCheck, Terminal } from 'lucide-react';
import { Button, Field } from './components';
import { rpc } from './api';

const example = 'import os\nprint("Files in this workspace:")\nfor name in sorted(os.listdir("."))[:40]:\n    print(name)';
export default function ComputerSandboxPanel(){
 const [status,setStatus]=useState<any>(null);const [folder,setFolder]=useState('');const [code,setCode]=useState(example);
 const [result,setResult]=useState<any>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 useEffect(()=>{rpc<any>('system.sandbox.status').then(setStatus).catch(e=>setError(String(e)));},[]);
 async function execute(){
  if(!window.confirm('Run this Python code in the selected folder using the available OS sandbox? It has a 15-second limit and no intended network access. Continue?'))return;
  setBusy(true);setError('');setResult(null);
  try{const reply=await rpc('system.sandbox.run',{workspace:folder,code,approved:true});setResult(reply);}catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <section className="surface settings-card"><div className="section-title"><div><h2>Computer workspace</h2><p>Approved, limited local Python analysis. No unattended system-wide control.</p></div><Terminal size={20}/></div><div className="settings-card-body computer-sandbox-panel">
  <div className="computer-sandbox-state">{status?.available?<><ShieldCheck size={20}/><strong>{status.backend} detected</strong></>:<><AlertTriangle size={20}/><strong>OS sandbox unavailable</strong></>}</div>
  <p className="muted">{status?.limitations||'Checking platform support…'} The sandbox does not apply to existing Claude Code or Codex CLI sessions. These retain their own permissions.</p>
  <Field label="Folder to inspect"><div className="sandbox-folder-picker"><input value={folder} onChange={e=>setFolder(e.target.value)} placeholder="/Users/you/Projects/my-lab"/><Button onClick={async()=>{try{if(!(window as any).__TAURI_INTERNALS__)throw new Error('Folder picker is available in the desktop app.');const mod=await import('@tauri-apps/plugin-dialog');const selected=await mod.open({directory:true,multiple:false});if(typeof selected==='string')setFolder(selected);}catch(e){setError(String(e));}}}>Choose folder</Button></div></Field>
  <Field label="Python analysis script"><textarea rows={6} maxLength={16000} value={code} onChange={e=>setCode(e.target.value)} spellCheck={false}/></Field>
  <div className="form-actions"><Button onClick={()=>setCode(example)}>Example: list workspace files</Button><Button disabled={!status?.available||!folder||!code||busy} variant="primary" onClick={()=>void execute()}>Review and run in sandbox</Button></div>
  {error&&<p role="alert" className="conversation-error">{error}</p>}
  {result&&<div className="sandbox-output"><strong>Exit code: {result.exit_code}</strong><pre>{result.stdout||'(no output)'}</pre>{result.stderr&&<pre>{result.stderr}</pre>}</div>}
 </div></section>;
}
