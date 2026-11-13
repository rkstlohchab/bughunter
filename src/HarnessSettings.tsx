import {useEffect,useState} from 'react';
import {Monitor,RefreshCw,LoaderCircle} from 'lucide-react';
import {rpc} from './api';
import type {RecordData as Row} from './api';

export default function HarnessSettings({stopped}:{stopped:boolean}){
 const [status,setStatus]=useState<Row|null>(null);const [catalog,setCatalog]=useState<Row[]>([]);const [job,setJob]=useState<Row|null>(null);
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [selected,setSelected]=useState('agent-desktop');const [plan,setPlan]=useState<Row|null>(null);
 const [query,setQuery]=useState('');const [results,setResults]=useState<Row[]|null>(null);
 async function search(){setBusy(true);setError('');try{const rows=await rpc<Row>('setup.search',{query});setResults(rows.items||[]);}catch(error){setError(String(error));}finally{setBusy(false);}}
 async function refresh(){setBusy(true);setError('');const [s,c]=await Promise.allSettled([rpc<Row>('harness.status'),rpc<Row>('setup.catalog')]);if(s.status==='fulfilled')setStatus(s.value);if(c.status==='fulfilled')setCatalog(c.value.items||[]);setError([s,c].filter(result=>result.status==='rejected').map(result=>String((result as PromiseRejectedResult).reason)).join('\n'));setBusy(false);}
 useEffect(()=>{void refresh();},[]);
 useEffect(()=>{
  if(!job?.id||job.status!=='running')return;
  let alive=true;let timer:ReturnType<typeof setTimeout>;
  async function poll(){try{const row=await rpc<Row>('setup.status',{id:job!.id});if(!alive)return;setJob(row);if(row.status==='running')timer=setTimeout(()=>void poll(),1000);else void refresh();}catch(error){if(alive){setError(String(error));timer=setTimeout(()=>void poll(),2000);}}}
  void poll();return()=>{alive=false;clearTimeout(timer);};
 },[job?.id,job?.status]);
 async function review(){setBusy(true);setError('');try{setPlan(await rpc<Row>('setup.plan',{package:selected}));}catch(error){setError(String(error));}finally{setBusy(false);}}
 async function install(){setBusy(true);setError('');try{setJob(await rpc<Row>('setup.install',{package:selected,approved:true}));setPlan(null);}catch(error){setError(String(error));}finally{setBusy(false);}}
 const engine=status?.engine;
 return <section className="surface settings-card" aria-label="Task harness settings">
  <div className="section-title"><div><h2>Computer and app access</h2><p>Run tasks from Chat with your selected AI. Access is chosen when a task starts.</p></div><Monitor size={20}/></div>
  <div className="settings-card-body harness-settings">
   <div className="harness-engine"><div><strong>Vercel agent-desktop</strong><p role="status">{engine?engine.ready?'Available on this device':engine.installed?'Needs setup':'Not installed':busy?'Checking desktop engine…':'Desktop status unavailable. Recheck to retry.'}</p></div><button className="button" onClick={()=>void refresh()} disabled={busy}><RefreshCw size={15}/> Recheck</button></div>
   {engine?.detail&&<p className="muted">{engine.detail}</p>}
   <p className="muted">macOS, Windows and Linux use the same task tools. Available actions depend on the current OS, desktop session and app.</p>
   {!!engine?.permissions&&<details><summary>Detected permissions</summary>{Object.entries(engine.permissions).map(([name,value])=><p key={name}>{name.replaceAll('_',' ')}: {typeof value==='object'?String((value as Row)?.state||'unknown'):String(value)}</p>)}</details>}
   <label className="field">Supporting tool<select aria-label="Supporting tool" value={selected} disabled={!catalog.length} onChange={event=>{setSelected(event.target.value);setPlan(null);}}>{!catalog.length&&<option value={selected}>{busy?'Loading tools…':'Tool list unavailable — recheck'}</option>}{catalog.map(item=><option key={item.id} value={item.id}>{item.label}{item.installed?' · Installed':''}</option>)}</select></label>
   <form className="registry-search" onSubmit={e=>{e.preventDefault();void search();}}><label className="field">Find another app<input aria-label="Find another app" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search your system package registry" maxLength={80}/></label><button className="button" disabled={busy||query.trim().length<2}>Search apps</button></form>
   {results&&<div className="registry-results">{!results.length?<p>No matching packages. Try the publisher’s app name.</p>:results.map(row=><button type="button" className="button" key={row.id} onClick={async()=>{setSelected(row.id);try{setPlan(await rpc<Row>('setup.plan',{package:row.id}));}catch(error){setError(String(error));}}}><span>{row.label}</span><small>{row.identifier} · {row.kind}</small></button>)}</div>}
   <button className="button" disabled={busy||stopped||!catalog.length||job?.status==='running'} onClick={()=>void review()}>Review installation</button>
   {plan&&<div className="harness-install-plan"><strong>{plan.label}</strong><p>{plan.detail}</p>{plan.command&&<code>{plan.command}</code>}<div className="button-group"><button className="button primary" disabled={busy||stopped||!plan.ready_to_install} onClick={()=>void install()}>Install tool</button><button className="button" onClick={()=>setPlan(null)}>Cancel</button></div></div>}
   {job&&<div role="status"><p>{job.status==='running'&&<LoaderCircle size={14} className="spin"/>} {job.message}{job.elapsed_seconds!=null?` · ${job.elapsed_seconds}s`:''}</p>{job.status==='running'&&<button className="button" onClick={()=>void rpc('setup.cancel',{id:job.id}).catch(error=>setError(String(error)))}>Cancel installation</button>}{job.output&&<details><summary>Installation output</summary><pre>{job.output}</pre></details>}</div>}
   {error&&<p role="alert">{error}</p>}
  </div>
 </section>;
}
