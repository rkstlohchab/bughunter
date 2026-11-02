import {useCallback, useEffect, useState} from 'react';
import {Activity, RefreshCw, ShieldCheck} from 'lucide-react';
import {rpc} from './api';
import {Button} from './components';

type Review = {id:string;type:string;result?:string;created_on:string};
type Blocker = {id:string;status:string;blocker:string;next_action:string;capture_count:number};
type StagingTask = {id:string;status:string;title?:string;kind?:string};
type Report = {id:string;report:Record<string,unknown>};

const EXAMPLE = JSON.stringify({pid:12345,entities:[{id:1,x:0,y:0,health:100},{id:2,x:4,y:6,health:100}]},null,2);

export default function VerificationP9({projectId}:{projectId:string}) {
  const [first,setFirst] = useState('');
  const [second,setSecond] = useState('');
  const [reviews,setReviews] = useState<Review[]>([]);
  const [blockers,setBlockers] = useState<Blocker[]>([]);
  const [tasks,setTasks] = useState<StagingTask[]>([]);
  const [taskId,setTaskId] = useState('');
  const [report,setReport] = useState<Report|null>(null);
  const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState('');
  const refresh = useCallback(async()=>{
    if(!projectId) {setReviews([]);setBlockers([]);setTasks([]);return;}
    const [r,b,allTasks] = await Promise.all([
      rpc<Review[]>('verification.p9.list',{project_id:projectId}),
      rpc<Blocker[]>('verification.p9.research_blockers',{project_id:projectId}),
      rpc<StagingTask[]>('tasks.list',{project_id:projectId}).catch(()=>[] as StagingTask[])
    ]);
    setReviews(r);setBlockers(b);
    const staging=allTasks.filter(t=>t.kind==='staging_price');
    setTasks(staging);setTaskId(old=>staging.some(t=>t.id===old)?old:staging[0]?.id||'');
  },[projectId]);
  useEffect(()=>{void refresh().catch(e=>setMessage(String(e)));},[refresh]);
  async function run(fn:()=>Promise<Report>) {
    setBusy(true);setMessage('');
    try {const r=await fn();setReport(r);await refresh();setMessage('Evidence comparison recorded.');}
    catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  async function capture(which:'first'|'second') {
    setBusy(true);setMessage('');
    try {const data=await rpc('verification.p9.capture_local',{project_id:projectId});
      if(which==='first')setFirst(JSON.stringify(data,null,2));else setSecond(JSON.stringify(data,null,2));
      setMessage('Captured actual local radar observation.');
    } catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  async function openReport(id:string){
    setBusy(true);try{setReport(await rpc<Report>('verification.p9.get',{id,project_id:projectId}));}
    catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  return <section className="surface settings-card" aria-label="P9 evidence verification">
    <div className="section-title"><div><h2>P9 · Evidence verification</h2><p>Compare observed game entities, staging quote receipts, and research browser blockers.</p></div><Activity size={20}/></div>
    <div className="settings-card-body">
      <h3>Game entity observations</h3>
      <p className="muted">Paste two JSON responses from the existing radar's <code>/state</code> endpoint. This compares real decoded observations, not unknown game offsets. Different PIDs are allowed but do not establish stable identities across restarts.</p>
      <div className="form-grid">
        <label>First observed snapshot <textarea rows={6} value={first} placeholder={EXAMPLE} onChange={e=>setFirst(e.target.value)}/></label>
        <label>Second observed snapshot <textarea rows={6} value={second} placeholder={EXAMPLE} onChange={e=>setSecond(e.target.value)}/></label>
      </div>
      <div className="form-actions"><Button disabled={!projectId||busy} onClick={()=>void capture('first')}>Capture first from local radar</Button><Button disabled={!projectId||busy} onClick={()=>void capture('second')}>Capture second</Button><Button disabled={!projectId||busy||!first.trim()||!second.trim()} onClick={()=>void run(()=>rpc<Report>('verification.p9.compare_entities',{project_id:projectId,first:JSON.parse(first),second:JSON.parse(second)}))}>Compare snapshots</Button></div>
      <h3><ShieldCheck size={17} style={{verticalAlign:'middle'}}/> Staging price receipts</h3>
      <p className="muted">Recheck already recorded gateway evidence from quote-only staging tests. No additional HTTP requests or payment actions are made.</p>
      <label>Staging test <select value={taskId} onChange={e=>setTaskId(e.target.value)}>{!tasks.length&&<option value="">No staging tests found</option>}{tasks.map(t=><option key={t.id} value={t.id}>{t.title||t.id} · {t.status}</option>)}</select></label>
      <div className="form-actions"><Button disabled={!projectId||!taskId||busy} onClick={()=>void run(()=>rpc<Report>('verification.p9.review_staging',{project_id:projectId,task_id:taskId}))}>Verify receipts</Button><Button icon={RefreshCw} disabled={busy} onClick={()=>void refresh()}>Refresh</Button></div>
      <h3>Research interruptions</h3>
      <p className="muted">Challenges remain in the visible research browser for manual completion. Nothing attempts to bypass or solve a CAPTCHA.</p>
      {blockers.length ? blockers.map(b=><div className="tool-access-row" key={b.id}><div><strong>{b.status.replaceAll('_',' ')}</strong><p className="muted">{b.blocker} · {b.next_action} · {b.capture_count} captures</p></div></div>):<p className="muted">No blocked sessions in this project.</p>}
      <h3>Recorded evidence checks</h3>
      {reviews.length ? reviews.map(r=><div className="tool-access-row" key={r.id}><div><strong>{r.type.replaceAll('_',' ')}</strong><p className="muted">{r.result} · {r.created_on}</p><Button disabled={busy} onClick={()=>void openReport(r.id)}>Inspect evidence</Button></div></div>):<p className="muted">No P9 evidence checks yet.</p>}
      {report&&<details open><summary>Evidence report · {report.id}</summary><pre className="code-block">{JSON.stringify(report.report,null,2)}</pre></details>}
      {message&&<p role="status" className="muted">{message}</p>}
    </div>
  </section>;
}
