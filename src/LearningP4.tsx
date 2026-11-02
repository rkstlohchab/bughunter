import {useEffect,useState} from 'react';
import type {FormEvent} from 'react';
import {rpc} from './api';
import {Button} from './components';

type Gap={code:string;priority:number;detail:string;action:string;procedure_id?:string};
type Reference={source_id:string;chunk_id:string;locator:string;title:string;excerpt:string};
type Procedure={procedure_id:string;source_id:string;title:string;step_count:number;cited_steps:number;fidelity_status:string;lab_status:string;has_bundled_lab:boolean;status:string};
type Plan={id:string;goal:string;status:string;checked_at:string;project_id:string;gaps?:Gap[];evidence?:Reference[];procedures?:Procedure[];history?:Array<{event:string;at:string;run_id?:string}>};

/** The planner indexes existing evidence; it never executes content from courses. */
export function LearningP4({projectId}:{projectId:string}){
  const [plans,setPlans]=useState<Plan[]>([]);
  const [goal,setGoal]=useState('');
  const [selected,setSelected]=useState<Plan|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const scope=projectId||'*';
  const refreshList=()=>rpc<Plan[]>('learning.p4.list',{project_id:scope}).then(setPlans).catch(e=>setError(String(e)));
  const inspect=(id:string)=>rpc<Plan>('learning.p4.get',{id,project_id:scope}).then(setSelected).catch(e=>setError(String(e)));
  useEffect(()=>{setSelected(null);void refreshList();},[scope]);
  async function act(fn:()=>Promise<unknown>,selectId?:string){setBusy(true);setError('');try{
    const result=await fn();await refreshList();
    if(selectId)await inspect(selectId);
    else if(result && typeof result==='object' && 'id' in result)setSelected(result as Plan);
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function create(e:FormEvent){e.preventDefault();if(!goal.trim())return;
    await act(async()=>{const result=await rpc<Plan>('learning.p4.create',{goal:goal.trim(),project_id:scope});setGoal('');return result;});}
  return <details className="surface course-experiment-overview">
    <summary><strong>Learning investigations</strong> · {plans.filter(p=>p.status==='open').length} active</summary>
    <p className="muted">Find the next knowledge gap from your own indexed lessons, citations and real experiment logs. An investigation is a plan, not a verified finding.</p>
    <form onSubmit={e=>void create(e)} className="learning-p4-form">
      <label>What should the AI learn or reproduce?<input value={goal} maxLength={500} minLength={6} required onChange={e=>setGoal(e.target.value)} placeholder="Understand how pointer resolution survives game restarts"/></label>
      <Button type="submit" disabled={busy||goal.trim().length<6}>Investigate</Button>
    </form>
    {error&&<p role="alert" className="error-text">{error}</p>}
    <div className="course-procedures">{plans.map(p=><div className="course-progress-row" key={p.id}>
      <div><strong>{p.goal}</strong><p className="muted">{p.status} · reviewed {new Date(p.checked_at).toLocaleString()}</p></div>
      <Button disabled={busy} onClick={()=>void inspect(p.id)}>Inspect</Button>
    </div>)}</div>
    {selected&&<div className="course-experiment-result">
      <h4>{selected.goal}</h4><p className="muted">{selected.status} · evidence is not permission to run instructions from a course or website</p>
      <div className="button-group wrap">
        <Button disabled={busy||selected.status==='archived'} onClick={()=>void act(()=>rpc('learning.p4.refresh',{id:selected.id,project_id:scope}),selected.id)}>Recheck evidence</Button>
        <Button disabled={busy||selected.status==='archived'} onClick={()=>void act(()=>rpc('learning.p4.advance',{id:selected.id,project_id:scope}),selected.id)}>Run next safe check</Button>
        <Button disabled={busy} onClick={()=>void act(()=>rpc('learning.p4.archive',{id:selected.id,archived:selected.status!=='archived',project_id:scope}),selected.id)}>{selected.status==='archived'?'Restore':'Archive'}</Button>
        <Button onClick={()=>setSelected(null)}>Close</Button>
      </div>
      <h4>Unresolved knowledge gaps</h4>
      {(selected.gaps||[]).length===0?<p>No unresolved gaps detected by these checks. This does not prove an external claim.</p>:
        (selected.gaps||[]).map((gap,i)=><div className="course-experiment-result" key={`${gap.code}-${gap.procedure_id||i}`}>
          <strong>{gap.code.replaceAll('_',' ')}</strong><p>{gap.detail}</p>
          {gap.action==='check_citations'&&gap.procedure_id&&selected.status==='open'&&<Button disabled={busy} onClick={()=>void act(()=>rpc('learning.p4.check_citations',{id:selected.id,procedure_id:gap.procedure_id,project_id:scope}),selected.id)}>Run source-citation check</Button>}
          {gap.action==='review_experiment'&&<p className="muted">Run supported tests in the lesson's “Try and verify” panel, with explicit lab approval.</p>}
        </div>)}
      <h4>Cited source passages ({(selected.evidence||[]).length})</h4>
      {(selected.evidence||[]).map(e=><details key={e.chunk_id}><summary>{e.title} · {e.locator}</summary><p>{e.excerpt}</p><small>Source: {e.source_id} · passage: {e.chunk_id}</small></details>)}
      <h4>Related procedures ({(selected.procedures||[]).length})</h4>
      {(selected.procedures||[]).map(p=><p key={p.procedure_id}><strong>{p.title}</strong> · {p.cited_steps}/{p.step_count} cited steps · source check: {p.fidelity_status} · lab: {p.lab_status}</p>)}
      <details><summary>Investigation history</summary>{(selected.history||[]).map((h,i)=><p key={i}>{h.event.replaceAll('_',' ')} · {new Date(h.at).toLocaleString()}{h.run_id?` · ${h.run_id}`:''}</p>)}</details>
    </div>}
  </details>;
}
