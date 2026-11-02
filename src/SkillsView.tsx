import { useCallback, useEffect, useState } from 'react';
import { BookCheck, Check, CircleHelp, Plus, RefreshCw, X } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Empty, Field, Modal, Status } from './components';

export default function SkillsView({projectId}: {projectId: string}) {
  const [items,setItems] = useState<Row[]>([]);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [creating,setCreating] = useState(false);
  const [title,setTitle] = useState('');
  const [method,setMethod] = useState('');
  const [evidence,setEvidence] = useState('');
  const [tags,setTags] = useState('');
  const [review,setReview] = useState<Row | null>(null);
  const [note,setNote] = useState('');
  const refresh=useCallback(async()=>setItems(await rpc<Row[]>('skills.list',{project_id:projectId||undefined})),[projectId]);
  useEffect(()=>{refresh().catch(e=>setError(String(e)));},[refresh]);
  async function create() {
    setBusy(true);setError('');
    try {await rpc('skills.propose',{title,method,evidence_id:evidence.trim(),project_id:projectId||undefined,
      tags:tags.split(',').map(v=>v.trim()).filter(Boolean)});
      setCreating(false);setTitle('');setMethod('');setEvidence('');setTags('');await refresh();}
    catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  async function decide(verified:boolean){
    if(!review)return;setBusy(true);setError('');
    try{await rpc('skills.review',{id:review.id,verified,note});setReview(null);setNote('');await refresh();}
    catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  return <div className="skills-page">
    <section className="surface skills-hero"><div><span className="muted">Evidence-backed learning</span><h2>Verified methods</h2><p>Save useful procedures linked to a real task, finding or source. Only methods you explicitly verify can be suggested for later work. Saved methods never execute commands by themselves.</p></div><div className="button-group"><Button icon={RefreshCw} onClick={()=>refresh().catch(e=>setError(String(e)))}>Refresh</Button><Button variant="primary" icon={Plus} onClick={()=>setCreating(true)}>Add method</Button></div></section>
    {error&&<p className="conversation-error" role="alert">{error}<button onClick={()=>setError('')} aria-label="Dismiss error"><X size={14}/></button></p>}
    <section className="skills-grid">
      {items.length===0&&<div className="surface"><Empty icon={BookCheck} title="No methods saved" description="Finish a task, review its evidence, then capture the reliable steps as a method."/></div>}
      {items.map(item=><article key={item.id} className="surface skill-card"><div className="row-between"><h3>{item.title}</h3><Status value={item.status}/></div><p>{item.method}</p><div className="skill-tags">{(item.tags||[]).map((tag:string)=><span key={tag}>{tag}</span>)}</div><small className="muted">Evidence: {item.evidence_id}</small>{item.review_note&&<p className="skill-review">Review: {item.review_note}</p>}
        <div className="skill-card-actions"><Button onClick={()=>{setReview(item);setNote('');}} icon={CircleHelp}>{item.status==='candidate'?'Review method':'Change review'}</Button></div></article>)}
    </section>
    {creating&&<Modal title="Save a reusable method" onClose={()=>setCreating(false)}><form onSubmit={e=>{e.preventDefault();void create();}}><Field label="Method name"><input required maxLength={120} value={title} onChange={e=>setTitle(e.target.value)} placeholder="Check authorization on a test API"/></Field><Field label="Procedure"><textarea required minLength={12} rows={5} value={method} onChange={e=>setMethod(e.target.value)} placeholder="What was checked, how it was verified, and when it should be used"/></Field><Field label="Evidence ID" hint="A saved task, finding, or knowledge source ID. The app validates it."><input required value={evidence} onChange={e=>setEvidence(e.target.value)} placeholder="work_task_…"/></Field><Field label="Topics (comma separated)"><input value={tags} onChange={e=>setTags(e.target.value)} placeholder="api, authorization"/></Field><div className="form-actions"><Button onClick={()=>setCreating(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={busy}>Save candidate</Button></div></form></Modal>}
    {review&&<Modal title="Review saved method" onClose={()=>setReview(null)}><p className="muted">Only verify after you have independently checked the linked evidence. This is not automatic model fine-tuning.</p><h3>{review.title}</h3><p className="skill-description">{review.method}</p><Field label="What did you verify?" hint="Verification requires a meaningful note."><textarea rows={4} value={note} onChange={e=>setNote(e.target.value)} placeholder="What evidence confirms this procedure?"/></Field><div className="form-actions"><Button onClick={()=>setReview(null)}>Cancel</Button><Button disabled={busy} onClick={()=>void decide(false)}>Reject / revoke</Button><Button icon={Check} variant="primary" disabled={busy || note.trim().length<12} onClick={()=>void decide(true)}>Mark verified</Button></div></Modal>}
  </div>;
}
