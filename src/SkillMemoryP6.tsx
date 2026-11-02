import {useEffect,useState} from 'react';
import type {FormEvent} from 'react';
import {Button} from './components';
import {rpc} from './api';

type Assessment={level:string;reason:string;source_valid:boolean;recheck_due:boolean;citation_run_id?:string|null;lab_run_id?:string|null};
type Skill={id:string;title:string;summary:string;source_id:string;procedure_id:string;reviewed_at?:string|null;review_due_at?:string|null;review_note?:string;assessment:Assessment;steps:Array<{locator?:string;quote:string}>;claim_scope:string};

/** Read-only shared model recall; only a desktop operator can review or recheck a skill. */
export function SkillMemoryP6({projectId}:{projectId:string}){
  const project_id=projectId||'*';
  const [cards,setCards]=useState<Skill[]>([]);
  const [selected,setSelected]=useState<Skill|null>(null);
  const [query,setQuery]=useState('');
  const [summary,setSummary]=useState('');
  const [note,setNote]=useState('');
  const [days,setDays]=useState(30);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const load=async()=>setCards(await rpc<Skill[]>('skill.p6.list',{project_id,query:''}));
  const inspect=async(id:string)=>{
    const skill=await rpc<Skill>('skill.p6.get',{id,project_id});
    setSelected(skill);setSummary(skill.summary||'');setNote(skill.review_note||'');
  };
  useEffect(()=>{setSelected(null);setError('');void load().catch(e=>setError(String(e)));},[project_id]);
  useEffect(()=>{
    const update=()=>{void load().catch(()=>{});};
    window.addEventListener('bughunter-p6-refresh',update);
    return ()=>window.removeEventListener('bughunter-p6-refresh',update);
  },[project_id]);
  async function perform(f:()=>Promise<unknown>,id?:string){setBusy(true);setError('');setMessage('');
    try{const output=await f();await load();if(id)await inspect(id);
      if(output&&typeof output==='object'&&'path' in output)setMessage(`Export saved: ${String((output as {path:string}).path)}`);
      else if(output&&typeof output==='object'&&'run_id' in output)setMessage(`Source check ${String((output as unknown as {status:string}).status)}. Refresh when it finishes.`);
    }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function review(event:FormEvent){event.preventDefault();if(!selected)return;
    await perform(()=>rpc('skill.p6.review',{id:selected.id,project_id,summary:summary.trim(),note:note.trim(),recheck_days:days,confirm:true}),selected.id);
  }
  const shown=cards.filter(c=>(c.title+' '+c.summary).toLowerCase().includes(query.toLowerCase()));
  return <details className="surface course-experiment-overview">
    <summary><strong>P6 · Skill memory</strong> · {cards.filter(c=>c.reviewed_at&&c.assessment.level!=='stale').length} reviewed</summary>
    <p className="muted">Keep source-backed methods in one shared library for your configured AI models. Review is an operator decision; source checks and fixture results do not verify real-world targets or train model weights.</p>
    <div className="button-group wrap">
      <label>Find a skill<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search saved skills"/></label>
      <Button disabled={busy} onClick={()=>void perform(()=>rpc('skill.p6.export',{project_id}))}>Export evidence index</Button>
      <Button disabled={busy} onClick={()=>void perform(load)}>Refresh</Button>
    </div>
    {error&&<p className="error-text" role="alert">{error}</p>}
    {message&&<p className="muted">{message}</p>}
    {shown.length===0?<p className="muted">No saved skill records. Open a course lesson, expand a candidate procedure, and choose “Save to skill memory.”</p>:
      <div className="course-procedures">{shown.map(row=><div className="course-progress-row" key={row.id}>
        <div><strong>{row.title}</strong><p className="muted">{row.assessment.level.replaceAll('_',' ')} · {row.reviewed_at?'Operator reviewed':'Review pending'}{row.assessment.recheck_due?' · Recheck due':''}</p></div>
        <Button disabled={busy} onClick={()=>void inspect(row.id).catch(e=>setError(String(e)))}>Open</Button>
      </div>)}</div>}
    {selected&&<div className="course-experiment-result">
      <h4>{selected.title}</h4>
      <p><strong>Current evidence:</strong> {selected.assessment.level.replaceAll('_',' ')}</p>
      <p className="muted">{selected.assessment.reason}. {selected.claim_scope}</p>
      <p className="muted">Source: {selected.source_id} · Procedure: {selected.procedure_id}</p>
      <p className="muted">Citation evidence: {selected.assessment.citation_run_id||'none'} · Fixture evidence: {selected.assessment.lab_run_id||'none'}</p>
      {selected.review_due_at&&<p className="muted">Review due: {new Date(selected.review_due_at).toLocaleString()}</p>}
      {!!selected.steps.length&&<details><summary>Original cited steps ({selected.steps.length})</summary><ol>{selected.steps.map((s,i)=><li key={i}>{s.quote} <small>{s.locator||'No locator'}</small></li>)}</ol></details>}
      <form className="learning-p4-form" onSubmit={e=>void review(e)}>
        <label>Reviewed skill summary<textarea required minLength={8} maxLength={1000} rows={3} value={summary} onChange={e=>setSummary(e.target.value)} placeholder="Describe what the source and test actually support."/></label>
        <label>Review notes<textarea maxLength={1000} rows={2} value={note} onChange={e=>setNote(e.target.value)}/></label>
        <label>Recheck interval (days)<input type="number" min={1} max={90} value={days} onChange={e=>setDays(Number(e.target.value))}/></label>
        <div className="button-group wrap">
          <Button type="submit" disabled={busy||!selected.assessment.source_valid||summary.trim().length<8}>Confirm reviewed skill</Button>
          <Button disabled={busy||selected.assessment.level==='stale'} onClick={()=>void perform(()=>rpc('skill.p6.recheck',{id:selected.id,project_id}),selected.id)}>Recheck citations</Button>
          <Button onClick={()=>setSelected(null)}>Close</Button>
        </div>
      </form>
    </div>}
  </details>;
}
