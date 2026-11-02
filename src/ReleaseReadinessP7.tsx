import {useCallback, useEffect, useState} from 'react';
import {Activity, CheckCircle2, CircleHelp, Download, RefreshCw, Square, XCircle} from 'lucide-react';
import {rpc} from './api';
import {Button} from './components';

type Check = {id:string;title:string;state:string;detail:string;next_step?:string};
type Run = {id:string;suite:string;title:string;status:string;output?:string;detail?:string;finished_at?:string;duration_seconds?:number};
type Suite = {id:string;title:string;available:boolean;detail:string;last?:{id:string;status:string;finished_at?:string}|null};
type Probe = {id:string;target:string;ok:boolean;checked:string;detail:string;checked_at:string};
type Preflight = {checked_at:string;checks:Check[];suites:Suite[];interpretation:string};

export default function ReleaseReadinessP7() {
  const [preflight,setPreflight] = useState<Preflight|null>(null);
  const [runs,setRuns] = useState<Run[]>([]);
  const [probes,setProbes] = useState<Probe[]>([]);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [path,setPath] = useState('');
  const [expanded,setExpanded] = useState('');
  const refresh = useCallback(async()=>{
    try {
      const [status,history,checks] = await Promise.all([
        rpc<Preflight>('readiness.p7.preflight'),rpc<Run[]>('readiness.p7.runs'),rpc<Probe[]>('readiness.p7.probes')]);
      setPreflight(status);setRuns(history);setProbes(checks);setError('');
    }catch(e){setError(String(e));}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  const active = runs.some(r=>r.status==='running'||r.status==='queued');
  useEffect(()=>{
    if(!active)return;
    const timer = window.setInterval(()=>void refresh(),1500);
    return ()=>window.clearInterval(timer);
  },[active,refresh]);
  async function start(id:string){
    setError('');setBusy(true);
    try{await rpc('readiness.p7.start',{suite:id,approved:true}); await refresh();}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function stop(id:string){
    setError('');setBusy(true);
    try{await rpc('readiness.p7.stop',{id,approved:true});await refresh();}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function probe(target:string){
    setError('');setBusy(true);
    try{await rpc('readiness.p7.probe',{target,approved:true}); await refresh();}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function exportReport(){
    setError('');setBusy(true);
    try{const result = await rpc<{path:string}>('readiness.p7.export'); setPath(result.path); await refresh();}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  const stateLabel = (v:string)=>v.replaceAll('_',' ');
  return <section className="surface settings-card" aria-label="P7 release readiness">
    <div className="section-title"><div><h2>P7 · Release readiness</h2><p>One place to check the full app before your final Mac test.</p></div><Activity size={20}/></div>
    <div className="settings-card-body">
      <p className="muted">Source present ≠ installed ≠ connected ≠ verified. These checks do not contact websites, Tor, Kaggle, Burp, or third-party games.</p>
      <div className="form-actions"><Button icon={RefreshCw} onClick={()=>void refresh()} disabled={busy}>Refresh preflight</Button><Button icon={Download} disabled={busy} onClick={()=>void exportReport()}>Export evidence report</Button></div>
      {path && <p role="status">Report saved locally: <code>{path}</code></p>}
      {error && <p role="alert" className="connection-result bad">{error}</p>}
      <details className="settings-more">
      <summary>Show {preflight?.checks.length || 0} readiness checks and blockers</summary>
      <div className="tool-access-list">
        {preflight?.checks.map(check=><div key={check.id} className="tool-access-row"><div>
          <div className="row-between"><strong>{check.title}</strong><span className="quiet-badge">{check.state==='ready'||check.state==='installed'||check.state==='configured'?<CheckCircle2 size={14}/>:check.state==='blocked'?<XCircle size={14}/>:<CircleHelp size={14}/>} {stateLabel(check.state)}</span></div>
          <p className="muted">{check.detail}</p>{check.next_step && <small className="muted">Next: {check.next_step}</small>}
        </div></div>)}
      </div></details>
      <h3>Optional connection probes</h3>
      <p className="muted">These are initiated by you. They check configuration or local reachability, not full model reasoning, Burp scanning, or Tor browsing. The model probe does not submit a billed generation request.</p>
      <div className="form-actions">
        {([['model','Configured AI'],['burp','Burp bridge'],['tor','Tor SOCKS'],['media','Media runtime']] as const).map(([id,title])=>{
          const last=probes.find(p=>p.target===id);
          return <Button key={id} disabled={busy||active} onClick={()=>void probe(id)}>{title}{last?last.ok?' · reached':' · not ready':''}</Button>;
        })}
      </div>
      {probes.length>0 && <p className="muted">Most recent: {probes[0].target} — {probes[0].detail}</p>}
      <h3>Reproducible local smoke tests</h3>
      <p className="muted">Each test is started manually, runs a fixed bundled script, and records pass/fail evidence. No paid model calls or production checkout requests.</p>
      <div className="tool-access-list">
        {preflight?.suites.map(s=>{
          const last = runs.find(r=>r.suite===s.id);
          return <div className="tool-access-row" key={s.id}><div style={{width:'100%'}}>
            <div className="row-between"><strong>{s.title}</strong><span className="quiet-badge">{stateLabel(last?.status||'not_run')}</span></div>
            <div className="form-actions">
              <Button disabled={busy||active||!s.available} onClick={()=>void start(s.id)}>{last ? 'Run again' : 'Run test'}</Button>
              {last&&(last.status==='running'||last.status==='queued') && <Button icon={Square} disabled={busy} onClick={()=>void stop(last.id)}>Stop</Button>}
              {last?.output && <Button onClick={()=>setExpanded(expanded===last.id?'':last.id)}>{expanded===last.id?'Hide':'Show'} result</Button>}
            </div>
            {!s.available && <p className="muted">{s.detail}</p>}
            {last?.detail && <p className="muted">{last.detail}</p>}
            {last?.id===expanded && <pre className="code-block" style={{maxHeight:280,overflow:'auto',whiteSpace:'pre-wrap'}}>{last.output}</pre>}
          </div></div>;
        })}
      </div>
      <small className="muted">Mac-only acceptance checks remain separate: real Accessibility permissions, native Tauri build, Burp extension, model reachability, and original course processing.</small>
    </div>
  </section>;
}
