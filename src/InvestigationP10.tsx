import {useCallback, useEffect, useState} from 'react';
import {BookOpenCheck, RefreshCw} from 'lucide-react';
import {rpc} from './api';
import {Button} from './components';

type Case = {id:string;title:string;source_type:string;assessment:string;state:string;evidence_state:string;next_actions:string[]};
type Review = {id:string;type:string;result:string};
type Detail = Case & {source_report?:Record<string,unknown>|null;events?:unknown[];evidence_current:boolean};

export default function InvestigationP10({projectId}:{projectId:string}) {
  const [cases,setCases]=useState<Case[]>([]);
  const [reviews,setReviews]=useState<Review[]>([]);
  const [reviewId,setReviewId]=useState('');
  const [frames,setFrames]=useState<Record<string,unknown>[]>([]);
  const [title,setTitle]=useState('');
  const [detail,setDetail]=useState<Detail|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const refresh=useCallback(async()=>{
    if(!projectId){setCases([]);setReviews([]);return;}
    const [items,evidence]=await Promise.all([
      rpc<Case[]>('investigation.p10.list',{project_id:projectId}),
      rpc<Review[]>('verification.p9.list',{project_id:projectId})
    ]);
    setCases(items);setReviews(evidence);
    setReviewId(old=>evidence.some(x=>x.id===old)?old:evidence[0]?.id||'');
  },[projectId]);
  useEffect(()=>{setFrames([]);setDetail(null);void refresh().catch(e=>setMessage(String(e)));},[refresh]);
  async function perform(task:()=>Promise<unknown>,success:string){
    setBusy(true);setMessage('');
    try{await task();await refresh();setMessage(success);}catch(error){setMessage(String(error));}finally{setBusy(false);}
  }
  async function capture(){
    setBusy(true);setMessage('');
    try{
      const frame=await rpc<Record<string,unknown>>('verification.p9.capture_local',{project_id:projectId});
      setFrames(old=>[...old.slice(-11),frame]);
      setMessage('One local radar frame captured. Change the offline game state before capturing another.');
    }catch(error){setMessage(String(error));}finally{setBusy(false);}
  }
  async function inspect(id:string){
    setBusy(true);setMessage('');
    try{setDetail(await rpc<Detail>('investigation.p10.get',{project_id:projectId,id}));}
    catch(error){setMessage(String(error));}finally{setBusy(false);}
  }
  function download(){
    void perform(async()=>{
      const result=await rpc('investigation.p10.export',{project_id:projectId});
      const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download='bughunter-evidence-casebook.json';link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    },'Metadata-only casebook exported.');
  }
  return <section className="surface settings-card" aria-label="P10 evidence investigations">
    <div className="section-title"><div><h2>P10 · Evidence investigations</h2><p>Turn observed radar frames and gateway-correlated P9 findings into inspectable cases.</p></div><BookOpenCheck size={20}/></div>
    <div className="settings-card-body">
      <h3>Track entities across observations</h3>
      <p className="muted">Start the local radar against your offline fixture or a known entity layout. Capture at least three observations while the test state changes. P10 compares decoded values; it does not discover unknown offsets or prove stable game entity identity.</p>
      <p className="muted">Captured frames: <strong>{frames.length}</strong> / 12</p>
      <div className="form-actions"><Button disabled={busy||!projectId||frames.length>=12} onClick={()=>void capture()}>Capture frame</Button><Button disabled={busy||!frames.length} onClick={()=>setFrames([])}>Clear frames</Button></div>
      <label>Case title (optional)<input value={title} maxLength={140} onChange={e=>setTitle(e.target.value)} placeholder="Player coordinates and health observations"/></label>
      <div className="form-actions"><Button disabled={busy||!projectId||frames.length<3} onClick={()=>void perform(()=>rpc('investigation.p10.create_timeline',{project_id:projectId,frames,title}), 'Timeline case recorded.')}>Analyze and save timeline</Button></div>
      <h3>Investigate existing P9 evidence</h3>
      <p className="muted">Create a follow-up case from a previously recorded P9 entity comparison or staging quote review. This sends no new web requests.</p>
      <label>Existing evidence <select value={reviewId} onChange={e=>setReviewId(e.target.value)}>{!reviews.length&&<option value="">No P9 evidence yet</option>}{reviews.map(r=><option key={r.id} value={r.id}>{r.type.replaceAll('_',' ')} · {r.result||'unassessed'}</option>)}</select></label>
      <div className="form-actions"><Button disabled={busy||!projectId||!reviewId} onClick={()=>void perform(()=>rpc('investigation.p10.create_from_review',{project_id:projectId,review_id:reviewId,title}), 'Follow-up case recorded.')}>Create evidence case</Button><Button icon={RefreshCw} disabled={busy} onClick={()=>void refresh()}>Refresh</Button><Button disabled={!projectId||busy} onClick={download}>Export case index</Button></div>
      <h3>Evidence casebook</h3>
      {cases.length ? cases.map(c=><div className="tool-access-row" key={c.id}><div><strong>{c.title}</strong><p className="muted">{c.source_type.replaceAll('_',' ')} · {c.assessment} · {c.evidence_state} · {c.state}</p><p className="muted">Next: {c.next_actions[0]||'Review the evidence.'}</p><div className="form-actions"><Button disabled={busy} onClick={()=>void inspect(c.id)}>Inspect</Button><Button disabled={busy||c.state==='closed'} onClick={()=>void perform(()=>rpc('investigation.p10.set_state',{project_id:projectId,id:c.id,state:'closed'}),'Case closed.')}>Close</Button></div></div></div>) : <p className="muted">No P10 investigations yet.</p>}
      {detail&&<details open><summary>Evidence details · {detail.title}</summary><p className="muted">{detail.evidence_current?'Source observation still matches its saved hash.':'Source changed or missing; the result is stale.'}</p><pre className="code-block">{JSON.stringify(detail,null,2)}</pre><Button disabled={busy} onClick={()=>void perform(async()=>{await rpc('investigation.p10.refresh',{project_id:projectId,id:detail.id});await inspect(detail.id);},'Evidence freshness checked.')}>Check evidence freshness</Button></details>}
      {message&&<p role="status" className="muted">{message}</p>}
    </div>
  </section>;
}
