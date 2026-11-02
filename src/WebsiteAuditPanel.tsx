import { useEffect, useState } from 'react';
import { Activity, ArrowRight, Download, Globe2, ShieldCheck, Square, Play } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Status } from './components';

const isActive = (s: string) => ['starting','running','cancelling'].includes(s);
type Props = { projectId: string; navigate: (page: any) => void };
export default function WebsiteAuditPanel({projectId, navigate}: Props) {
  const [task,setTask] = useState<Row | null>(null);
  const [project,setProject] = useState<Row | null>(null);
  const [count,setCount] = useState(12);
  const [seed,setSeed] = useState('');
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [agreed,setAgreed] = useState(false);
  useEffect(()=>{
    setTask(null);setError('');setAgreed(false);setSeed('');
    if(!projectId){setProject(null);return;}
    void rpc<Row>('projects.get',{id:projectId}).then(value=>{
      setProject(value);setSeed(value.scope?.[0]||'');
    }).catch(e=>setError(String(e)));
    void rpc<Row[]>('tasks.list',{project_id:projectId}).then(rows=>{
      const prior=rows.find(t=>t.kind==='website_audit');
      if(prior)setTask(prior);
    }).catch(()=>{});
  },[projectId]);
  useEffect(()=>{
    if(!task?.id || !isActive(String(task.status)))return;
    const interval=setInterval(()=>{void rpc<Row>('tasks.get',{id:task.id}).then(setTask).catch(e=>setError(String(e)));},1400);
    return ()=>clearInterval(interval);
  },[task?.id,task?.status]);
  async function start() {
    setBusy(true);setError('');
    try {
      const result=await rpc<Row>('website.audit.start',{
        project_id:projectId, approved:agreed, max_pages:count,
        seed_url:seed.trim() || undefined,
      });
      setTask(result);
    } catch(e) {setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  async function stop(){
    if(!task?.id)return;
    setBusy(true);setError('');
    try{setTask(await rpc<Row>('website.audit.stop',{id:task.id}));}
    catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  async function downloadReport(){
    if(!task?.id)return;
    try{const report=await rpc<Row>('website.audit.report',{id:task.id});const blob=new Blob([report.content],{type:'text/markdown;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=report.filename;link.click();URL.revokeObjectURL(url);}
    catch(e){setError(e instanceof Error?e.message:String(e));}
  }
  const results:Row=task?.result||{};
  const pages:Row[]=results.pages||[];
  const hasProject=!!project?.scope?.length;
  const rules=project?.read_only_paths||[];
  return <section className="surface website-audit-panel" aria-label="Autonomous website audit">
    <div className="section-title"><div><h2><Globe2 size={19}/> Autonomous website review</h2>
      <p>Discover public pages and run real, authorized read-only checks. Every request travels through your verified built-in policy gateway (Burp optional). Findings are reviewable leads—not assumed exploits.</p>
    </div>{task&&<Status value={task.status}/>}</div>
    <div className="website-audit-content">
      <div className="website-audit-guide"><ShieldCheck size={20}/><div><strong>Read-only mode</strong>
        <p>No login attempts, POST requests, cart modifications, checkout, payment, or automatic exploit payloads. The scanner skips queries and sensitive routes. Only explicitly permitted GET paths are visited.</p></div></div>
      {!hasProject?<div className="website-audit-notice">Choose a website project with documented authorization. <Button onClick={()=>navigate('Targets')}>Set up a target</Button></div>:
        <div className="website-audit-controls">
          <label className="field"><span>Start URL</span><input aria-label="Audit start URL" type="url" value={seed} onChange={e=>setSeed(e.target.value)} placeholder="https://staging.example.com/" /></label>
          <label className="field"><span>Maximum pages (1–30)</span><input aria-label="Page limit" type="number" min={1} max={30} value={count} onChange={e=>setCount(Number(e.target.value))}/></label>
          <div className="website-audit-scope"><strong>Project:</strong> {project?.name}<small>Approved GET rules: {rules.length ? rules.map((r:any)=>typeof r==='string'?r:r.path).join(', ') : 'None configured'}</small><Button onClick={()=>navigate('Targets')}>Edit authorization & safe paths</Button></div>
          <label className="website-audit-consent"><input type="checkbox" checked={agreed} onChange={e=>setAgreed(e.target.checked)}/><span>I own this target or am explicitly authorized to crawl these paths. They are safe for GET requests, and the audit must obey my program rules.</span></label>
          <div className="button-group wrap">
            {task&&isActive(String(task.status))?<Button icon={Square} disabled={busy} onClick={()=>void stop()}>Stop audit</Button>:
              <Button icon={Play} variant="primary" disabled={busy||!agreed||!seed.trim()||!rules.length||!Number.isInteger(count)||count<1||count>30} onClick={()=>void start()}>Run authorized audit</Button>}
            {task&&<><Button icon={ArrowRight} onClick={()=>navigate('Team')}>View Agents history</Button><Button icon={Download} onClick={()=>void downloadReport()}>Download report</Button></>}
          </div>
        </div>}
      {error&&<p role="alert" className="conversation-error">{error}</p>}
      {task&&<div aria-live="polite" className="website-audit-summary">
        <div className="cart-audit-stat"><b>{pages.length}</b><small>Pages checked</small></div>
        <div className="cart-audit-stat"><b>{results.requests_sent??0}</b><small>Observed requests</small></div>
        <div className="cart-audit-stat"><b>{results.observations?.length??0}</b><small>Review leads</small></div>
        <div className="cart-audit-stat"><b>{results.skipped??0}</b><small>Links skipped</small></div>
      </div>}
      {task?.error&&<p role="alert" className="conversation-error">{task.error}</p>}
      {pages.length>0&&<details className="advanced-options"><summary><Activity size={15}/> Observed pages and evidence</summary>
        <div className="website-audit-pages">{pages.map((page,i)=><div className="website-audit-page" key={page.action_id||i}><strong>HTTP {page.status}</strong><span>{page.path}</span><small>Evidence: {(page.evidence_ids||[]).join(', ')}</small></div>)}</div>
      </details>}
    </div>
  </section>;
}
