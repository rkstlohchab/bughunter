import {useEffect,useState} from 'react';
import {rpc} from './api';
import {Button} from './components';

type Batch={id:string;title:string;status:string;completed_entries:number;total_entries:number;imported_sources:number;detail?:string;errors?:string[]};
type Step={id:string;number:number;quote:string;chunk_id:string;locator:string;modality:string;timestamp_seconds?:number};
type Procedure={id:string;title:string;status:string;steps:Step[];source_id:string};

export function CourseProgress({projectId}:{projectId:string}){
  const [courses,setCourses]=useState<Batch[]>([]);
  const [error,setError]=useState('');
  const load=()=>rpc<Batch[]>('courses.list',{project_id:projectId||'*'}).then(setCourses).catch(e=>setError(String(e)));
  useEffect(()=>{load();const poll=setInterval(load,5000);return ()=>clearInterval(poll);},[projectId]);
  if(!courses.length && !error)return null;
  return <section className="surface"><div className="section-title"><div><h2>Course imports</h2><p>Each lesson becomes one searchable file. Import can resume after an interruption.</p></div></div>
    {error&&<p role="alert">{error}</p>}
    {courses.map(c=><div className="course-progress-row" key={c.id}><div><strong>{c.title}</strong><p className="muted">{c.status} · {c.imported_sources} lesson files queued · {c.completed_entries}/{c.total_entries} archive entries</p><small>{c.detail}</small>{(c.errors||[]).slice(-2).map((msg,i)=><p key={i} className="error-text">{msg}</p>)}</div>{['partial','interrupted'].includes(c.status)&&<Button onClick={()=>rpc('courses.resume',{id:c.id}).then(load).catch(e=>setError(String(e)))}>Resume</Button>}</div>)}
  </section>;
}

export function CourseProcedures({sourceId}:{sourceId:string}){
  const [list,setList]=useState<Procedure[]>([]);const [error,setError]=useState('');const [working,setWorking]=useState(false);
  const load=()=>rpc<Procedure[]>('knowledge.procedures',{source_id:sourceId}).then(setList).catch(e=>setError(String(e)));
  useEffect(()=>{load();},[sourceId]);
  const rebuild=async()=>{setWorking(true);try{await rpc('knowledge.procedures.rebuild',{source_id:sourceId});await load();}catch(e){setError(String(e));}finally{setWorking(false);}};
  return <section className="source-memory"><div className="section-title"><div><h3>Steps extracted from this lesson</h3><p>Quotes from the source, not verified instructions. Select a step to see its timestamp or passage.</p></div><Button disabled={working} onClick={rebuild}>Rebuild steps</Button></div>
    {error&&<p role="alert" className="error-text">{error}</p>}
    {list.length>0&&<Button disabled={working} title="Checks citations for this lesson, not claims in an external app" onClick={async()=>{setWorking(true);try{await rpc('experiments.check_source',{source_id:sourceId});}catch(e){setError(String(e));}finally{setWorking(false);}}}>Check all lesson citations</Button>}
    {list.length===0?<p className="muted">No clear step-by-step instructions detected in the indexed source.</p>:<div className="course-procedures">{list.slice(0,30).map(p=><ProcedureRow key={p.id} procedure={p}/>)}</div>}
    <p className="muted">Source claims are never automatically marked as tested or added to model weights.</p>
  </section>;
}

function ProcedureRow({procedure:p}:{procedure:Procedure}){
  const [expanded,setExpanded]=useState(false);
  return <details onToggle={e=>setExpanded(e.currentTarget.open)}>
    <summary>{p.title} <small>· candidate</small></summary>
    <ol>{p.steps.map(s=><li key={s.id}><p>{s.quote}</p><small>{s.locator}{s.timestamp_seconds!=null?` · ${s.timestamp_seconds}s`:''} · {s.modality}</small></li>)}</ol>
    {expanded&&<><SaveSkill procedureId={p.id}/><ProcedureExperiments procedureId={p.id}/></>}
  </details>;
}

function SaveSkill({procedureId}:{procedureId:string}){
  const [status,setStatus]=useState('');const [busy,setBusy]=useState(false);
  async function save(){setBusy(true);try{
    const result=await rpc<{id:string}>('skill.p6.create',{procedure_id:procedureId});
    setStatus(`Saved as ${result.id}. Open P6 · Skill memory to review the evidence.`);
    window.dispatchEvent(new Event('bughunter-p6-refresh'));
  }catch(e){setStatus(String(e));}finally{setBusy(false);}}
  return <div className="course-experiments"><Button disabled={busy} onClick={()=>void save()}>Save to skill memory</Button>
    {status&&<p className="muted">{status}</p>}
  </div>;
}

type Recipe={id:string;title:string;description:string;level:string};
type ExperimentRun={id:string;status:string;recipe:string;title:string;started_at?:string;finished_at?:string;verification_level:string;error?:string;hypotheses?:string[];observations?:Array<Record<string,unknown>>};

/** The course is evidence, not executable code. Runs always use bundled fixtures. */
function ProcedureExperiments({procedureId}:{procedureId:string}){
  const [recipes,setRecipes]=useState<Recipe[]>([]);
  const [runs,setRuns]=useState<ExperimentRun[]>([]);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const refresh=()=>rpc<ExperimentRun[]>('experiments.list',{procedure_id:procedureId}).then(setRuns).catch(e=>setError(String(e)));
  useEffect(()=>{
    setError('');setRuns([]);
    rpc<Recipe[]>('experiments.available',{procedure_id:procedureId}).then(setRecipes).catch(e=>setError(String(e)));
    refresh();
    const interval=setInterval(refresh,3000);
    return ()=>clearInterval(interval);
  },[procedureId]);
  async function start(recipe:Recipe){
    const lab=recipe.id!=='source_fidelity';
    if(lab&&!window.confirm(`Run the bundled disposable test: ${recipe.title}? No commands from the lesson are executed.`))return;
    setBusy(true);setError('');
    try {await rpc('experiments.start',{procedure_id:procedureId,recipe:recipe.id,approve_lab:lab});await refresh();}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function stop(id:string){try{await rpc('experiments.stop',{id});await refresh();}catch(e){setError(String(e));}}
  return <div className="course-experiments">
    <h4>Try and verify</h4>
    <p className="muted">Runs are checked against source text or bundled test programs. Passing a lab does not prove the lesson's method works on other targets.</p>
    <div className="button-group wrap">{recipes.map(r=><Button key={r.id} disabled={busy} title={r.description} onClick={()=>start(r)}>{r.title}</Button>)}</div>
    {error&&<p role="alert" className="error-text">{error}</p>}
    {runs.slice(0,8).map(run=><details key={run.id} className="course-experiment-result">
      <summary><strong>{run.title}</strong> · {run.status.replaceAll('_',' ')} <small>· {run.verification_level.replaceAll('_',' ')}</small></summary>
      {run.started_at&&<p className="muted">Started {new Date(run.started_at).toLocaleString()}</p>}
      {run.error&&<p className="error-text">{run.error}</p>}
      {!!run.hypotheses?.length&&<p>Next checks: {run.hypotheses.join(' · ')}</p>}
      {(run.observations||[]).map((item,i)=><pre className="course-experiment-log" key={i}>{JSON.stringify(item,null,2)}</pre>)}
      {['queued','running','stopping'].includes(run.status)&&<Button onClick={()=>stop(run.id)}>Stop test</Button>}
    </details>)}
  </div>;
}

export function CourseExperimentOverview({projectId}:{projectId:string}){
  const [runs,setRuns]=useState<ExperimentRun[]>([]);
  const [insights,setInsights]=useState<Array<{recipe:string;issue:string;count:number;suggested_checks:string[]}>>([]);
  useEffect(()=>{
    const load=()=>{
      rpc<ExperimentRun[]>('experiments.list',{project_id:projectId||'*',limit:12}).then(setRuns).catch(()=>{});
      rpc<typeof insights>('experiments.insights',{project_id:projectId||'*'}).then(setInsights).catch(()=>{});
    };
    load();const timer=setInterval(load,7000);return()=>clearInterval(timer);
  },[projectId]);
  if(!runs.length)return null;
  return <details className="surface course-experiment-overview"><summary><strong>Research experiments</strong> · {runs.length} recent results</summary>
    <p className="muted">Actual tests, failures, and what needs checking next. Tests do not retrain model weights.</p>
    {runs.map(r=><p key={r.id}><strong>{r.title}</strong> · {r.status} · {r.verification_level.replaceAll('_',' ')}</p>)}
    {insights.length>0&&<details><summary>What failed and what to try next ({insights.length})</summary>
      {insights.map((i,n)=><div key={n}><strong>{i.recipe}</strong> · {i.count} run(s)<p>{i.issue}</p><p className="muted">{i.suggested_checks.join(' ')}</p></div>)}
    </details>}
  </details>;
}
