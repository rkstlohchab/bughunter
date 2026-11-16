import { useEffect, useState } from 'react';
import { rpc } from './api';
import type { RecordData as Row } from './api';

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
  return <section aria-label="Game runtime" className="task-progress">
    <h3>Game task</h3>
    <p role="status" aria-live="polite">{data?.native_available?'Desktop tools connected':'Native tools unavailable — open the desktop app to run a game task.'}</p>
    {error&&<p role="alert">{error}</p>}
    {sessions.length===0&&<p>No game process selected. Ask the AI to inspect installed targets, or use the separate test process below.</p>}
    {sessions.map(session=>{
      const operations:Row[]=session.operations||[];
      const active=['running','starting'].includes(session.state);
      const pending=operations.some(op=>['queued','claimed','dispatched','cancellation_requested'].includes(op.status)&&!session.cleanup_operation_ids?.includes(op.id));
      const read=[...operations].reverse().find(op=>op.kind==='read'&&op.status==='succeeded');
      const sample=session.descriptor?.fields?.countdown;
      const featureRuns:Row[]=session.feature_runs||[];
      const mappings:Row[]=session.mappings||[];
      return <div key={session.id}>
        <strong>{session.descriptor?.name||'Selected game process'}</strong>
        <p role="status">Session: {session.state}{session.last_error?` · ${session.last_error}`:''}</p>
        {session.descriptor?.fixture&&<p>Disposable test process. These results do not verify a real game.</p>}
        {session.knowledge_plan_id&&<p>Course plan attached: <code>{session.knowledge_plan_id}</code></p>}
        {sample&&active&&<div>
          <button type="button" disabled={busy||pending||stopped} onClick={()=>void action('gamelab.runtime.read',{session_id:session.id,...sample})}>Read test value</button>
          <button type="button" disabled={busy||pending||!read||stopped||executionMode==='plan'||session.cleanup_operation_ids?.length>0} onClick={()=>void action('gamelab.runtime.write',{session_id:session.id,read_operation_id:read?.id,value:7,restore_on_stop:true})}>Set test value to 7</button>
        </div>}
        {featureRuns.length>0&&<div aria-label="Feature controls">{featureRuns.map(feature=><div key={feature.id}>
          <strong>{feature.feature_id}</strong><span> · {feature.enabled?'Updating':'Updates stopped'}</span>
          {feature.enabled&&<button type="button" disabled={busy} onClick={()=>void action('gamelab.features.stop',{session_id:session.id,feature_id:feature.feature_id})}>Stop {feature.feature_id}</button>}
        </div>)}</div>}
        {mappings.length>0&&<details><summary>Saved mappings ({mappings.length})</summary><ul>{mappings.map(mapping=><li key={mapping.id}>
          {mapping.name} · revision {mapping.revision} · {mapping.verification_status.replaceAll('_',' ')}
        </li>)}</ul></details>}
        {active&&<button type="button" disabled={busy} onClick={()=>void action('gamelab.sessions.stop',{session_id:session.id})}>Stop game session</button>}
        {operations.length>0&&<details open><summary>Recorded operations ({operations.length})</summary><ul>{operations.map(op=><li key={op.id}>
          {op.kind}: <strong>{op.status.replaceAll('_',' ')}</strong>
          {op.result?.value!==undefined&&` · value ${op.result.value}`}
          {op.result?.verified===true&&' · readback verified'}
          {op.result?.progress&&<span> · scan {op.result.progress.phase} · {op.result.progress.bytes_read} bytes read</span>}
          {op.result?.total_matches!==undefined&&<span> · {op.result.total_matches} matches</span>}
          {op.result?.complete===false&&<span> · Search incomplete{op.result.reason?`: ${op.result.reason}`:''}</span>}
          {op.result?.results?.length>0&&<details><summary>View results ({op.result.results.length})</summary><ul>{op.result.results.map((result:Row,index:number)=><li key={index}><code>{result.address||result.root}</code>{result.value!==undefined&&` · ${result.value}`}</li>)}</ul></details>}
          {op.result?.next_cursor&&active&&<button type="button" disabled={busy||pending||stopped} onClick={()=>void action('gamelab.scans.control',{session_id:session.id,scan_id:op.result.scan_id,cursor:op.result.next_cursor})}>More scan results</button>}
          {op.result?.state&&<span> · feature {op.result.state}</span>}
          {op.result?.cleanup_status&&<span> · cleanup {op.result.cleanup_status.replaceAll('_',' ')}</span>}
          {op.status==='outcome_unknown'&&' · The result is uncertain; this write will not be repeated.'}
          {op.error?.message&&<span> · {op.error.message}</span>}
        </li>)}</ul></details>}
      </div>;
    })}
    {executionMode!=='plan'&&<button type="button" disabled={busy||stopped||!data?.native_available} onClick={()=>void action('gamelab.runtime.fixture')}>{busy?'Working…':'Start disposable test target'}</button>}
  </section>;
}
