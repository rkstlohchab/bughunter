import {useEffect,useState} from 'react';
import type {FormEvent} from 'react';
import {Button} from './components';
import {rpc} from './api';

type Next={type:string;detail:string;procedure_id?:string;recipes?:string[];run_id?:string;code?:string};
type Observation={run_id:string;procedure_id:string;recipe:string;status:string;verification_level:string;error?:string;hypotheses?:string[];at:string};
type Event={kind:string;at:string;run_id?:string;status?:string};
type Mission={id:string;goal:string;status:string;stage:string;created_at:string;
  citation_checks_started:number;labs_started:number;pending_run_id?:string|null;next?:Next;
  results?:Observation[];events?:Event[];summary?:string};

/** Bounded P5 evidence workflow; lab execution requires an explicit click. */
export function MissionP5({projectId}:{projectId:string}){
  const project_id=projectId||'*';
  const [all,setAll]=useState<Mission[]>([]);
  const [selected,setSelected]=useState<Mission|null>(null);
  const [goal,setGoal]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [note,setNote]=useState('');
  const refresh=async()=>setAll(await rpc<Mission[]>('mission.p5.list',{project_id}));
  const inspect=async(id:string)=>setSelected(await rpc<Mission>('mission.p5.get',{id,project_id}));
  useEffect(()=>{setSelected(null);setError('');void refresh().catch(e=>setError(String(e)));},[project_id]);
  async function act(op:()=>Promise<unknown>, id?:string){setBusy(true);setError('');try{
    const result=await op();await refresh();
    if(id)await inspect(id);
    else if(result&&typeof result==='object'&&'id' in result)setSelected(result as Mission);
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function create(e:FormEvent){e.preventDefault();if(goal.trim().length<6)return;
    await act(async()=>{const r=await rpc<Mission>('mission.p5.create',{goal:goal.trim(),project_id});setGoal('');return r;});}
  return <details className="surface course-experiment-overview">
    <summary><strong>P5 · Study missions</strong> · {all.filter(x=>!['stopped','stale'].includes(x.status)).length} available</summary>
    <p className="muted">Follow a cited learning goal through bounded source checks and optional, explicitly approved disposable labs. Mission evidence is not proof of a result in a separate target.</p>
    <form onSubmit={e=>void create(e)} className="learning-p4-form">
      <label>Mission goal<input minLength={6} maxLength={500} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="Reproduce and evaluate memory pointer stability"/></label>
      <Button type="submit" disabled={busy||goal.trim().length<6}>Start study mission</Button>
    </form>
    {error&&<p role="alert" className="error-text">{error}</p>}
    {note&&<p className="muted">{note}</p>}
    <div className="course-procedures">{all.map(row=><div key={row.id} className="course-progress-row">
      <div><strong>{row.goal}</strong><p className="muted">{row.status} · {row.stage} · {new Date(row.created_at).toLocaleString()}</p></div>
      <Button disabled={busy} onClick={()=>void act(()=>rpc('mission.p5.get',{id:row.id,project_id}),row.id)}>Open</Button>
    </div>)}</div>
    {selected&&<div className="course-experiment-result">
      <h4>{selected.goal}</h4>
      <p className="muted">{selected.status} · {selected.stage} · {selected.citation_checks_started}/8 source checks · {selected.labs_started}/3 lab runs</p>
      {selected.next&&<div className="course-experiment-result"><strong>Next: {selected.next.type.replaceAll('_',' ')}</strong><p>{selected.next.detail}</p></div>}
      <div className="button-group wrap">
        <Button disabled={busy||['stopped','stale'].includes(selected.status)} onClick={()=>void act(()=>rpc('mission.p5.advance',{id:selected.id,project_id}),selected.id)}>{selected.pending_run_id?'Check experiment result':'Advance one step'}</Button>
        <Button disabled={busy} onClick={()=>void act(()=>rpc('mission.p5.get',{id:selected.id,project_id}),selected.id)}>Refresh</Button>
        {selected.next?.type==='failure_review'&&selected.results?.at(-1)?.recipe==='source_fidelity'&&<Button disabled={busy} onClick={()=>void act(()=>rpc('mission.p5.retry_source',{id:selected.id,project_id}),selected.id)}>Retry source check</Button>}
        <Button disabled={busy} onClick={()=>void act(async()=>{const x=await rpc<{path:string}>('mission.p5.export',{id:selected.id,project_id});setNote(`Evidence exported to ${x.path}`);},selected.id)}>Export evidence</Button>
        <Button disabled={busy||selected.status==='stopped'} onClick={()=>void act(()=>rpc('mission.p5.stop',{id:selected.id,project_id}),selected.id)}>Stop</Button>
        <Button onClick={()=>setSelected(null)}>Close</Button>
      </div>
      {selected.next?.type==='lab_approval'&&selected.next.procedure_id&&selected.next.recipes?.map(recipe=><div key={recipe} className="course-progress-row">
        <p>Run the bundled <strong>{recipe.replaceAll('_',' ')}</strong> fixture?</p>
        <Button disabled={busy} onClick={()=>void act(()=>rpc('mission.p5.approve_lab',{id:selected.id,project_id,
          procedure_id:selected.next?.procedure_id,recipe,confirm:true}),selected.id)}>Approve and run fixture</Button>
      </div>)}
      <h4>Observed experiment results</h4>
      {(selected.results||[]).length===0?<p className="muted">No experiment results recorded yet.</p>:selected.results?.map(result=><p key={result.run_id}>
        <strong>{result.recipe.replaceAll('_',' ')}</strong> · {result.status} · {result.verification_level} · evidence {result.run_id}{result.error&&<span> · {result.error}</span>}{result.hypotheses?.map((h,i)=><span key={i}> · Suggested check: {h}</span>)}</p>)}
      <details><summary>Mission audit trail</summary>{(selected.events||[]).map((evt,i)=><p key={i}>{evt.kind.replaceAll('_',' ')} · {new Date(evt.at).toLocaleString()}{evt.run_id?` · ${evt.run_id}`:''}</p>)}</details>
    </div>}
  </details>;
}
