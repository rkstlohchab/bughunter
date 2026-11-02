import { useEffect, useState } from 'react';
import { rpc } from './api';
import { Button, Field } from './components';

type Watch = {id:string;title:string;url:string;kind:string;enabled:boolean;status:string;last_error?:string;last_checked_at?:string;new_count?:number;interval_seconds:number};
type Discovery = {id:string;title:string;url:string;watch_id:string;reason:string;observed_at:string;sha256:string;status:string};
type DatasetAudit = {eligible:number;counts:{train:number;evaluation:number};excluded:number;exclusion_reasons:Record<string,number>;independent_evaluation_available:boolean;dataset_ready:boolean;note:string};

export function ResearchSourceSettings() {
  const [items,setItems] = useState<Watch[]>([]);
  const [url,setUrl] = useState('');
  const [name,setName] = useState('');
  const [kind,setKind] = useState<'rss'|'html'>('rss');
  const [enabled,setEnabled] = useState(false);
  const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState('');
  const refresh = () => rpc<Watch[]>('research.p2.watches').then(setItems).catch(e => setMessage(String(e)));
  useEffect(()=>{void refresh();},[]);
  async function task(action:()=>Promise<any>){setBusy(true);setMessage('');try{await action();await refresh();}catch(e){setMessage(String(e));}finally{setBusy(false);}}
  return <details className="surface settings-card">
    <summary><strong>Research sources</strong> · monitored pages and feeds</summary>
    <div className="settings-card-body">
      <p className="muted">Add a public HTTPS RSS/Atom feed or page. Scheduled checks are opt-in (minimum every 6 hours). New material enters a review inbox; it is not automatically trusted, executed or added to training.</p>
      <form onSubmit={e=>{e.preventDefault();void task(async()=>{await rpc('research.p2.add',{url,title:name,kind,enabled,interval_hours:12});setUrl('');setName('');});}}>
        <Field label="Source name"><input required maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder="Security advisories"/></Field>
        <Field label="Feed or page URL"><input required type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://example.org/feed.xml"/></Field>
        <Field label="Source format"><select value={kind} onChange={e=>setKind(e.target.value as 'rss'|'html')}><option value="rss">RSS / Atom feed</option><option value="html">Web page (text only)</option></select></Field>
        <label className="check-row"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Automatically check this source when BugHunter is running</label>
        <Button type="submit" disabled={busy||!url.trim()||!name.trim()}>Add source</Button>
      </form>
      {items.map(item=><div key={item.id} className="tool-card">
        <div><strong>{item.title}</strong><p className="muted">{item.url}</p><small>{item.status}{item.last_checked_at?` · checked ${new Date(item.last_checked_at).toLocaleString()}`:''}{item.last_error?` · ${item.last_error}`:''}</small></div>
        <div className="button-group wrap">
          <Button disabled={busy} onClick={()=>void task(()=>rpc('research.p2.check',{id:item.id}))}>Check now</Button>
          <Button disabled={busy} onClick={()=>void task(()=>rpc('research.p2.update',{id:item.id,enabled:!item.enabled}))}>{item.enabled?'Pause checks':'Enable checks'}</Button>
          <Button disabled={busy} onClick={()=>void task(()=>rpc('research.p2.delete',{id:item.id}))}>Remove</Button>
        </div>
      </div>)}
      {message&&<p role="status" className="muted">{message}</p>}
    </div>
  </details>;
}

export function ResearchUpdates({projectId}:{projectId:string}){
  const [items,setItems]=useState<Discovery[]>([]);
  const [selected,setSelected]=useState<Record<string,any>|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const refresh=()=>rpc<Discovery[]>('research.p2.inbox',{project_id:projectId||'*'}).then(setItems).catch(e=>setError(String(e)));
  useEffect(()=>{void refresh(); const id=setInterval(()=>void refresh(),30000);return()=>clearInterval(id);},[projectId]);
  async function act(id:string,accept:boolean){setBusy(true);try{await rpc('research.p2.review',{id,accept});setSelected(null);await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <details className="surface course-experiment-overview">
    <summary><strong>New research</strong> · {items.length} item{items.length===1?'':'s'} to review</summary>
    <p className="muted">Updates from sources you explicitly subscribed to. All content is unverified until you review it.</p>
    {items.length===0?<p className="muted">No new updates. Add monitored sources under Settings → Research sources.</p>:items.map(item=><div className="course-experiment-result" key={item.id}>
      <strong>{item.title}</strong><p className="muted">{item.reason==='changed'?'Updated':'New'} · {item.url}</p>
      <Button disabled={busy} onClick={async()=>{try{setSelected(await rpc('research.p2.get',{id:item.id}));}catch(e){setError(String(e));}}}>Read observation</Button>
      <Button disabled={busy} onClick={()=>void act(item.id,false)}>Dismiss</Button>
    </div>)}
    {selected&&<div className="course-experiment-result"><h4>{selected.title}</h4><p className="muted">External, unverified content. Original digest: {selected.sha256}</p><pre className="course-experiment-log">{selected.text}</pre><div className="button-group wrap"><Button disabled={busy} onClick={()=>void act(selected.id,true)}>Add to my library</Button><Button onClick={()=>setSelected(null)}>Close</Button></div></div>}
    {error&&<p role="alert" className="error-text">{error}</p>}
  </details>;
}

export function DatasetP2Panel({projectId}:{projectId:string}){
  const [audit,setAudit]=useState<DatasetAudit|null>(null);
  const [path,setPath]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const refresh=()=>rpc<DatasetAudit>('dataset.p2.inspect',{project_id:projectId||'*'}).then(setAudit).catch(e=>setError(String(e)));
  useEffect(()=>{void refresh();},[projectId]);
  async function exportData(){setBusy(true);setError('');try{const result=await rpc<{path:string}>('dataset.p2.export',{project_id:projectId||'*'});setPath(result.path);await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <details className="surface course-experiment-overview">
    <summary><strong>Training data</strong> · {audit?.eligible??0} reviewed examples</summary>
    <p className="muted">Build training and independently held-out evaluation JSONL. Only your reviewed, permissioned examples with valid citations are eligible.</p>
    {audit&&<><p>{audit.counts.train} training · {audit.counts.evaluation} evaluation · {audit.excluded} excluded</p>
      {!audit.independent_evaluation_available&&<p className="muted">No independent evaluation set yet. Mark examples from a different source as Evaluation when curating them.</p>}
      {!!Object.keys(audit.exclusion_reasons).length&&<details><summary>Why examples were excluded</summary><pre className="course-experiment-log">{JSON.stringify(audit.exclusion_reasons,null,2)}</pre></details>}
    </>}
    <div className="button-group wrap"><Button disabled={busy} onClick={()=>void refresh()}>Refresh</Button><Button disabled={busy||!audit?.eligible} onClick={()=>void exportData()}>Export reviewed dataset</Button></div>
    {path&&<p role="status">Saved locally: <code>{path}</code></p>}
    {error&&<p role="alert" className="error-text">{error}</p>}
  </details>;
}
