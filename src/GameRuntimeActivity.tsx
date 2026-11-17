import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Activity, ChevronRight, Cpu, FlaskConical, Square } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';

const PENDING = ['queued','claimed','dispatched','cancellation_requested'];
const label = (value: unknown) => String(value ?? '').replaceAll('_', ' ');
const tone = (status: string) =>
  ['succeeded','running','completed','verified'].includes(status) ? 'ok'
  : ['failed','outcome_unknown','interrupted','helper_exited'].includes(status) ? 'bad'
  : PENDING.includes(status) || status === 'starting' ? 'busy' : 'idle';

function Operation({op,active,busy,pending,stopped,onMore}:{op:Row;active:boolean;busy:boolean;pending:boolean;stopped:boolean;onMore:()=>void}) {
  const r = op.result || {};
  const facts: ReactNode[] = [];
  if (r.value !== undefined) facts.push(<span key="v">value <code>{String(r.value)}</code></span>);
  if (r.verified === true) facts.push(<span key="rb">readback verified</span>);
  if (r.progress) facts.push(<span key="p">scan {r.progress.phase} · {r.progress.bytes_read} bytes read</span>);
  if (r.total_matches !== undefined) facts.push(<span key="m">{r.total_matches} matches</span>);
  if (r.watch) facts.push(<span key="w">watch · {r.samples ?? 0} samples{r.achieved_hz ? ` · ${r.achieved_hz}/s measured` : ''}{r.windows ? ` · ${r.windows.reduce((n: number, w: Row) => n + (w.rows_total || 0), 0)} changing slots` : ''}</span>);
  if (r.job_id) facts.push(<span key="pr">probe {label(r.probe)} · {r.complete ? 'complete' : `continues (${label(r.phase)})`}</span>);
  if (r.engine?.engine) facts.push(<span key="en">engine {label(r.engine.engine)}</span>);
  if (r.findings?.length) facts.push(<span key="fd">{r.findings.length} finding{r.findings.length === 1 ? '' : 's'} recorded</span>);
  if (r.changed !== undefined && r.snapshot_id) facts.push(<span key="df">{r.changed} slots changed in {r.elapsed_s}s</span>);
  if (r.complete === false && !r.job_id) facts.push(<span key="i">{r.watch ? 'Watch ended early' : 'Search incomplete'}{r.reason ? `: ${r.reason}` : ''}</span>);
  if (r.state) facts.push(<span key="s">feature {r.state}</span>);
  if (r.cleanup_status) facts.push(<span key="c">cleanup {label(r.cleanup_status)}</span>);
  if (op.status === 'outcome_unknown') facts.push(<span key="u">The result is uncertain; this write will not be repeated.</span>);
  if (op.error?.message) facts.push(<span key="e" className="rt-op-error">{op.error.message}</span>);
  return <li className="rt-op">
    <span className="rt-op-kind">{op.kind}</span>
    <span className={`rt-op-status rt-${tone(op.status)}`}>{label(op.status)}</span>
    {facts.length > 0 && <span className="rt-op-facts">{facts}</span>}
    {r.results?.length > 0 && <details className="rt-op-results"><summary>View results ({r.results.length})</summary>
      <ul>{r.results.map((item: Row, index: number) => <li key={index}><code>{item.address || item.root}</code>{item.value !== undefined && ` · ${item.value}`}</li>)}</ul></details>}
    {r.next_cursor && active && <button type="button" className="rt-btn" disabled={busy || pending || stopped} onClick={onMore}>More scan results</button>}
  </li>;
}

export default function GameRuntimeActivity({harnessId,executionMode,stopped}:{harnessId:string;executionMode?:string;stopped:boolean}) {
  const [data,setData]=useState<Row|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{
    let live=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try { const value=await rpc<Row>('gamelab.runtime.status',{harness_id:harnessId});if(live)setData(value); }
      catch(e) {if(live)setError(String(e));}
      if(live)timer=setTimeout(()=>void poll(),800);
    };
    setData(null);void poll();return()=>{live=false;clearTimeout(timer);};
  },[harnessId,refresh]);
  async function action(method:string,params:Row={}) {
    setBusy(true);setError('');
    try {await rpc(method,{...params,harness_id:harnessId});setRefresh(v=>v+1);}
    catch(e){setError(String(e));}
    finally{setBusy(false);}
  }
  const sessions:Row[]=data?.sessions||[];
  // Newest session stays open; earlier ones fold away so repeated runs don't stack up.
  const latest=sessions.at(-1);
  const earlier=sessions.slice(0,-1).reverse();
  const native=!!data?.native_available;

  const renderSession=(session:Row)=>{
    const operations:Row[]=session.operations||[];
    const active=['running','starting'].includes(session.state);
    const pending=operations.some(op=>PENDING.includes(op.status)&&!session.cleanup_operation_ids?.includes(op.id));
    const read=[...operations].reverse().find(op=>op.kind==='read'&&op.status==='succeeded');
    const sample=session.descriptor?.fields?.countdown;
    const featureRuns:Row[]=session.feature_runs||[];
    const mappings:Row[]=session.mappings||[];
    const findings:Row[]=session.findings||[];
    return <article key={session.id} className={`rt-session rt-${tone(session.state)}`}>
      <div className="rt-session-head">
        <strong className="rt-session-name">{session.descriptor?.name||'Selected game process'}</strong>
        <span className="rt-state" role="status">Session: {session.state}{session.last_error?` · ${label(session.last_error)}`:''}</span>
      </div>
      {(session.descriptor?.fixture||session.knowledge_plan_id||session.engine)&&<div className="rt-meta">
        {session.engine&&<span className="rt-tag">Engine: {label(session.engine)}</span>}
        {session.descriptor?.fixture&&<span className="rt-tag"><FlaskConical size={12}/>Disposable test process. These results do not verify a real game.</span>}
        {session.knowledge_plan_id&&<span className="rt-tag" title={session.knowledge_plan_id}>Course plan attached: <code>{String(session.knowledge_plan_id).slice(0,26)}…</code></span>}
      </div>}
      {active ? <div className="rt-actions">
        {sample&&active&&<>
          <button type="button" className="rt-btn" disabled={busy||pending||stopped} onClick={()=>void action('gamelab.runtime.read',{session_id:session.id,...sample})}>Read test value</button>
          <button type="button" className="rt-btn" disabled={busy||pending||!read||stopped||executionMode==='plan'||session.cleanup_operation_ids?.length>0} onClick={()=>void action('gamelab.runtime.write',{session_id:session.id,read_operation_id:read?.id,value:7,restore_on_stop:true})}>Set test value to 7</button>
        </>}
        {active&&<button type="button" className="rt-btn rt-btn-stop" disabled={busy} onClick={()=>void action('gamelab.sessions.stop',{session_id:session.id})}><Square size={11}/>Stop game session</button>}
      </div> : null}
      {featureRuns.length>0&&<div className="rt-features" aria-label="Feature controls">{featureRuns.map(feature=><div key={feature.id} className="rt-feature">
        <span className={`rt-dot ${feature.enabled?'rt-ok':'rt-idle'}`}/>
        <strong>{feature.feature_id}</strong><span className="rt-muted"> · {feature.enabled?'Updating':'Updates stopped'}</span>
        {feature.enabled&&<button type="button" className="rt-btn rt-btn-stop" disabled={busy} onClick={()=>void action('gamelab.features.stop',{session_id:session.id,feature_id:feature.feature_id})}>Stop {feature.feature_id}</button>}
      </div>)}</div>}
      {mappings.length>0&&<details className="rt-fold"><summary><ChevronRight size={13}/>Saved mappings ({mappings.length})</summary><ul className="rt-list">{mappings.map(mapping=><li key={mapping.id}>
        {mapping.name} · revision {mapping.revision} · {label(mapping.verification_status)}
      </li>)}</ul></details>}
      {findings.length>0&&<details className="rt-fold" open><summary><ChevronRight size={13}/>Findings ({findings.length})</summary><ul className="rt-list">{findings.map(finding=><li key={finding.id}>
        {finding.title} · <code>{finding.address}</code> · {label(finding.status)}{finding.recipe_id?' · saved as mapping':''}
      </li>)}</ul></details>}
      {operations.length>0&&<details className="rt-fold" open><summary><ChevronRight size={13}/>Recorded operations ({operations.length})</summary>
        <ul className="rt-ops">{operations.map(op=><Operation key={op.id} op={op} active={active} busy={busy} pending={pending} stopped={stopped}
          onMore={()=>void action('gamelab.scans.control',{session_id:session.id,scan_id:op.result.scan_id,cursor:op.result.next_cursor})}/>)}</ul>
      </details>}
    </article>;
  };

  return <section aria-label="Game runtime" className="rt-card">
    <header className="rt-head">
      <span className="rt-icon"><Cpu size={16}/></span>
      <h3>Game task</h3>
      <span className={`rt-pill ${native?'rt-ok':'rt-bad'}`} role="status" aria-live="polite">
        <Activity size={12}/>{native?'Desktop tools connected':'Native tools unavailable — open the desktop app to run a game task.'}
      </span>
    </header>
    {error&&<p role="alert" className="rt-error">{error}</p>}
    {sessions.length===0&&<p className="rt-empty">No game process selected. Ask the AI to inspect installed targets, or use the separate test process below.</p>}
    {latest&&renderSession(latest)}
    {earlier.length>0&&<details className="rt-fold rt-earlier"><summary><ChevronRight size={13}/>Earlier sessions ({earlier.length})</summary>{earlier.map(renderSession)}</details>}
    {executionMode!=='plan'&&<div className="rt-foot">
      <button type="button" className="rt-btn" disabled={busy||stopped||!native} onClick={()=>void action('gamelab.runtime.fixture')}><FlaskConical size={12}/>{busy?'Working…':'Start disposable test target'}</button>
    </div>}
  </section>;
}
