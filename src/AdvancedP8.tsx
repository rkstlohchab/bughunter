import {useCallback, useEffect, useState} from 'react';
import {BrainCircuit, ShieldCheck, ScanEye, Square, RefreshCw} from 'lucide-react';
import {rpc} from './api';
import {Button} from './components';

type Dataset = {filename:string;train:number;evaluation:number;sha256:string};
type Job = {id:string;filename:string;model:string;status:string;detail:string;iters:number;train_count:number;evaluation_count:number;adapter_path:string};
type Preflight = {apple_silicon:boolean;mlx_installed:boolean;datasets:Dataset[];training_jobs:number};
type Coverage = {captured_requests:number;checkout_requests_observed:number;staging_tasks_observed:number;checks:{id:string;status:string;detail:string}[];assessment:string};

export default function AdvancedP8({projectId}:{projectId:string}) {
  const [preflight,setPreflight] = useState<Preflight|null>(null);
  const [jobs,setJobs] = useState<Job[]>([]);
  const [filename,setFilename] = useState('');
  const [model,setModel] = useState('mlx-community/Qwen3-0.6B-4bit');
  const [iters,setIters] = useState(100);
  const [coverage,setCoverage] = useState<Coverage|null>(null);
  const [message,setMessage] = useState('');
  const [busy,setBusy] = useState(false);
  const refresh = useCallback(async()=>{
    try {
      const [info,history] = await Promise.all([rpc<Preflight>('advanced.p8.preflight'),rpc<Job[]>('advanced.p8.jobs')]);
      setPreflight(info);setJobs(history);
      setFilename(old=>info.datasets.some(d=>d.filename===old)?old:(info.datasets[0]?.filename||''));
    } catch(e){setMessage(String(e));}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    if(!jobs.some(j=>['queued','running','stopping'].includes(j.status)))return;
    const timer=window.setInterval(()=>void refresh(),2000);return()=>window.clearInterval(timer);
  },[jobs,refresh]);
  async function start(dry_run:boolean){
    if(!filename)return setMessage('Export a reviewed P2 dataset with independent evaluation examples first.');
    if(!dry_run && !window.confirm('Start local MLX LoRA training on this Mac? This uses CPU/GPU memory and stores an adapter in the BugHunter data directory.'))return;
    setBusy(true);setMessage('');
    try{const result=await rpc<Job>('advanced.p8.train',{filename,model,iters,approved:true,dry_run});
      setMessage(`${dry_run?'Prepared':'Started'} ${result.id}: ${result.detail}`);await refresh();}
    catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  async function stop(id:string){
    if(!window.confirm('Stop this local training job?'))return;
    setBusy(true);try{await rpc('advanced.p8.stop',{id,approved:true});await refresh();}
    catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  async function webCoverage(){
    if(!projectId)return setMessage('Select a web testing project first.');
    setBusy(true);try{setCoverage(await rpc<Coverage>('advanced.p8.web_coverage',{project_id:projectId}));setMessage('');}
    catch(e){setMessage(String(e));}finally{setBusy(false);}
  }
  return <section className="surface settings-card" aria-label="P8 advanced capabilities">
    <div className="section-title"><div><h2>P8 · Advanced capabilities</h2><p>Local model training, evidence-based web coverage, and an external-process ESP radar.</p></div><BrainCircuit size={20}/></div>
    <div className="settings-card-body">
      <h3><BrainCircuit size={17} style={{verticalAlign:'middle'}}/> Reviewed-data LoRA training</h3>
      <p className="muted">Trains a separate MLX adapter using approved P2 examples. Training requires Apple Silicon, MLX-LM, and a dataset with a held-out evaluation split. It never modifies the original model automatically.</p>
      <p className="muted">Mac: {preflight?.apple_silicon?'detected':'not detected'} · MLX-LM: {preflight?.mlx_installed?'installed':'not installed'} · Eligible exports: {preflight?.datasets.length||0}</p>
      <label>Dataset <select value={filename} onChange={e=>setFilename(e.target.value)}>
        {!preflight?.datasets.length && <option value="">No reviewed P2 exports with holdout found</option>}
        {preflight?.datasets.map(d=><option key={d.filename} value={d.filename}>{d.filename} · {d.train} training / {d.evaluation} evaluation</option>)}
      </select></label>
      <label>MLX-compatible model <input value={model} onChange={e=>setModel(e.target.value)} placeholder="org/model or local model path"/></label>
      <label>Training iterations <input type="number" min="1" max="2000" value={iters} onChange={e=>setIters(Number(e.target.value))}/></label>
      <div className="form-actions"><Button disabled={busy} onClick={()=>void start(true)}>Validate & prepare</Button><Button disabled={busy||!preflight?.apple_silicon||!preflight?.mlx_installed} onClick={()=>void start(false)}>Train local LoRA</Button><Button icon={RefreshCw} disabled={busy} onClick={()=>void refresh()}>Refresh</Button></div>
      {jobs.length>0 && <div className="tool-access-list">{jobs.slice(0,6).map(job=><div className="tool-access-row" key={job.id}><div>
        <strong>{job.model}</strong> <span className="quiet-badge">{job.status}</span>
        <p className="muted">{job.detail} · {job.train_count} train / {job.evaluation_count} evaluation · {job.iters} iterations</p>
        {job.status==='completed' && <small>Adapter: <code>{job.adapter_path}</code></small>}
        {['queued','running','stopping'].includes(job.status) && <Button icon={Square} disabled={busy} onClick={()=>void stop(job.id)}>Stop</Button>}
      </div></div>)}</div>}
      <h3><ShieldCheck size={17} style={{verticalAlign:'middle'}}/> Web vulnerability evidence coverage</h3>
      <p className="muted">Correlates captured Burp traffic and staging quote-test receipts. Missing evidence stays unverified; this does not send new web requests.</p>
      <div className="form-actions"><Button disabled={busy||!projectId} onClick={()=>void webCoverage()}>Review current project</Button></div>
      {coverage && <><p className="muted">{coverage.captured_requests} captured HTTP records · {coverage.checkout_requests_observed} price/checkout path matches · {coverage.staging_tasks_observed} staging test records</p>
        <div className="tool-access-list">{coverage.checks.map(c=><div className="tool-access-row" key={c.id}><div><strong>{c.id.replaceAll('_',' ')}</strong> · {c.status}<p className="muted">{c.detail}</p></div></div>)}</div>
        <small className="muted">{coverage.assessment}</small></>}
      <h3><ScanEye size={17} style={{verticalAlign:'middle'}}/> Live entity radar</h3>
      <p className="muted">The radar can use any accessible process with an explicitly verified entity layout. It reads values through the existing memory helper; unknown game offsets still require discovery. The display is a separate localhost browser, not an injected game-window overlay.</p>
      <pre className="code-block">python3 scripts/run_esp_lab_demo.py --layout /path/to/entity-layout.json --no-browser</pre>
      {message && <p role="status" className="muted">{message}</p>}
    </div>
  </section>;
}
