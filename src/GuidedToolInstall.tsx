import { useCallback, useEffect, useState } from 'react';
import { Download, RefreshCw, Wrench } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button } from './components';

/** Explicit package approval with server-side allowlist. All installs live in Settings. */
export default function GuidedToolInstall() {
  const [catalog,setCatalog]=useState<Row|null>(null);
  const [job,setJob]=useState<Row|null>(null);
  const [message,setMessage]=useState('');
  const [confirm,setConfirm]=useState<string|null>(null);
  const refresh=useCallback(async()=>{
    try {setCatalog(await rpc<Row>('setup.catalog'));}
    catch(e){setMessage(`Could not check installed tools: ${String(e)}`);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    if (!job?.id || job.status!=='running')return;
    const id=setInterval(()=>{void rpc<Row>('setup.status',{id:job.id}).then(value=>{setJob(value);if(value.status!=='running')void refresh();}).catch(e=>setMessage(String(e)));},2500);
    return()=>clearInterval(id);
  },[job?.id,job?.status,refresh]);
  async function install(id:string){
    setMessage('');setConfirm(null);
    try {setJob(await rpc<Row>('setup.install',{package:id,approved:true}));}
    catch(e){setMessage(e instanceof Error?e.message:String(e));}
  }
  const item=(catalog?.items || []).find((p:Row)=>p.id===confirm);
  return <section className="surface settings-card" aria-label="Install supporting tools">
    <div className="section-title"><div><h2>Install tools</h2><p>Optional tools that help BugHunter inspect files and process videos. Install only what you need.</p></div><Wrench size={20}/></div>
    <div className="settings-card-body">
      {!catalog?.supported && <p className="muted">One-click installation requires macOS. You can still use Chat and imported Knowledge.</p>}
      {catalog?.supported && !catalog?.homebrew_available && <p role="status">Homebrew is not installed. Install it from <a href="https://brew.sh/" target="_blank" rel="noreferrer">brew.sh</a>, then recheck.</p>}
      <div className="tool-cards">
        {(catalog?.items||[]).map((tool:Row)=><div className="tool-card" key={tool.id}>
          <div style={{minWidth:0,overflowWrap:'anywhere'}}><strong>{tool.label}</strong><p>{tool.description}</p><small>{tool.installed?'Detected on this Mac':tool.status}</small></div>
          <Button disabled={!catalog?.supported||!catalog?.homebrew_available||!!(job?.status==='running')||!!tool.installed} onClick={()=>setConfirm(tool.id)}>{tool.installed?'Installed':'Install'}</Button>
        </div>)}
      </div>
      <div className="button-group wrap"><Button onClick={()=>void refresh()}><RefreshCw size={15}/> Recheck tools</Button></div>
      {confirm && item && <div className="info-note" role="group" aria-label="Approve installation" style={{display:'flex',flexWrap:'wrap',gap:12,alignItems:'center'}}>
        <span style={{flex:'1 1 240px'}}>Install <strong>{item.label}</strong> using <code>{item.command}</code>? Homebrew may download files or request additional setup.</span>
        <Button onClick={()=>setConfirm(null)}>Cancel</Button><Button variant="primary" onClick={()=>void install(confirm)}><Download size={15}/> Approve install</Button>
      </div>}
      {job && <div className="info-note" role="status" style={{overflowWrap:'anywhere'}}><strong>{job.package}: {job.status}</strong> — {job.message}{job.status==='failed' && job.output && <details><summary>Installation details</summary><pre style={{whiteSpace:'pre-wrap'}}>{String(job.output).slice(-2500)}</pre></details>}</div>}
      {message && <p role="alert" className="conversation-error">{message}</p>}
      <p className="muted">Burp Suite Professional and Cheat Engine require separate installation/licensing. Detected does not mean running, connected, or usable in a particular game or app.</p>
    </div>
  </section>;
}
