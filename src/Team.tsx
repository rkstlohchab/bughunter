import { useCallback, useEffect, useState } from 'react';
import { Activity, CircleCheck, CircleX, Clock3, RefreshCw, RotateCcw, Square, Terminal, Users } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import type { Shared } from './App';
import { Button, Status } from './components';

const names: Record<string,string> = {
  orchestrated:'AI task plan', coding_agents:'Coding agents', web_investigation:'Web investigation',
  study:'Knowledge study', tor_capture:'Tor research', memory_handoff:'Memory handoff', memory_session:'Memory tool session', cart_lab:'Cart security lab', website_audit:'Website audit', tor_browser:'Tor Browser session'
};
const labels: Record<string,string> = {
  draft:'Ready to plan', starting:'Starting', running:'Running', queued:'Queued',
  awaiting_approval:'Needs approval', awaiting_retry:'Retry required', completed:'Completed', failed:'Failed',
  cancelled:'Cancelled', interrupted:'Interrupted', paused:'Paused', awaiting_workspace:'Open workspace'
};
const active = (status:string) => ['starting','running','queued','awaiting_approval','awaiting_retry','awaiting_workspace'].includes(status);

export default function Team({perform, busy, projectId, onOpenAITesting, onOpenMemory}: Pick<Shared,'perform'|'busy'|'projectId'> & {onOpenAITesting:()=>void;onOpenMemory:()=>void}) {
  const [tasks, setTasks] = useState<Row[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Row | null>(null);
  const [installed, setInstalled] = useState<Row>({});
  const [error, setError] = useState('');
  const [useClaude, setUseClaude] = useState(true);
  const [useCodex, setUseCodex] = useState(true);
  const [directory, setDirectory] = useState('');
  const reload = useCallback(async () => {
    try {
      // Refresh linked plans before reading the shared ledger so finished work is observed.
      await rpc('plans.list',{project_id:projectId||undefined});
      const [items, available] = await Promise.all([
        rpc<Row[]>('tasks.list',projectId?{project_id:projectId}:{}),
        rpc<Row>('agents.available')
      ]);
      setTasks(items);setInstalled(available);
      if(selected) {const chosen=items.find(t=>t.id===selected);if(chosen){setDetail(await rpc<Row>(chosen.kind==='orchestrated'?'plans.get':'tasks.get',{id:selected}));}else{setDetail(null);setSelected('');}}else if(items.length){setSelected(items[0].id);}
      setError('');
    } catch(e) {setError(e instanceof Error?e.message:String(e));}
  }, [projectId,selected]);
  useEffect(()=>{void reload();const timer=setInterval(()=>void reload(),2500);return()=>clearInterval(timer);},[reload]);
  return <div className="agents-page">
    <header className="agents-head"><div><span className="muted">Unified task activity</span><h2>Your tasks</h2>
      <p>Ask in Chat, then monitor progress and review evidence here. Tasks show actual actions, not just what a model claims it did.</p></div>
      <Button icon={RefreshCw} onClick={()=>void reload()}>Refresh</Button>
    </header>
    <details className="agents-optional"><summary>Installed coding agents (optional)</summary><div className="agents-connections" aria-label="Coding agent installations">
      {(['claude','codex'] as const).map(agent=><div className="agents-connection" key={agent}><div className="row-between">
        <strong>{agent==='claude'?'Claude Code':'Codex'}</strong>
        {installed[agent]?.installed?<span className="agents-ready"><CircleCheck size={15}/>Ready</span>:<span className="agents-missing"><CircleX size={15}/>Not installed</span>}
      </div><small>{installed[agent]?.installed?'Available for a task you explicitly delegate in Chat':agent==='claude'?'Install Claude Code, then sign in from Terminal.':'Install Codex CLI, then sign in from Terminal.'}</small></div>)}
    </div>
    </details><section className="agents-workflow-hint" aria-label="How tasks work"><div><strong>1. Ask in Chat</strong><small>Describe the result, not the tools.</small></div><div><strong>2. Review the plan</strong><small>Preflight runs without changing external systems.</small></div><div><strong>3. Watch execution</strong><small>Approve external work and inspect recorded results.</small></div></section>
    {error&&<p role="alert" className="conversation-error">{error}</p>}
    <div className="agents-layout">
      <section className="surface agents-jobs" aria-label="All work tasks"><h3><Users size={18}/> Recent tasks</h3>
        {!tasks.length&&<div className="agents-empty"><Activity size={28}/><strong>No tasks yet</strong><p>Start by describing your task in Chat. Supported tools will log their actual work here.</p></div>}
        {tasks.map(job=><button key={job.id} type="button" className={`agents-job ${selected===job.id?'selected':''}`}
          onClick={()=>setSelected(job.id)}><span className="row-between"><strong>{job.title||names[job.kind]||'Task'}</strong><Status value={job.status}/></span>
          <small>{names[job.kind]||job.kind} · {labels[job.status]||job.status}</small>
          <span className="muted">{job.updated_at?new Date(job.updated_at).toLocaleString():''}</span></button>)}
      </section>
      <section className="surface agents-activity" aria-label="Task activity details"><div className="row-between"><h3><Terminal size={18}/> Actual activity</h3>{detail&&<Status value={detail.status}/>}</div>
        {!detail?<div className="agents-empty"><Clock3 size={26}/><p>Select a task to view status, recorded events, results and any outstanding approvals.</p></div>:<>
          <p className="muted agents-prompt">{names[detail.kind]||detail.kind} · {detail.title}</p>
          {detail.error&&<p role="alert" className="conversation-error">{detail.error}</p>}
          {detail.kind==='orchestrated'&&<section className="task-plan" aria-label="Plan checkpoints">
            <h4>Execution plan</h4>
            <p className="muted">Only completed steps represent observed work. Imported documents and LLM text cannot grant tool permissions.</p>
            {(detail.steps||[]).map((step:Row,i:number)=><article key={step.key||i} className="task-step"><span className="task-step-index">{i+1}</span><div className="task-step-content"><div className="row-between"><strong>{step.title}</strong><Status value={step.status}/></div>
              {step.result?.verified_methods?.length>0&&<small>Verified methods: {step.result.verified_methods.map((r:Row)=>r.title).join(' · ')}</small>}
              {step.result?.references?.length>0&&<small>Knowledge: {step.result.references.map((r:Row)=>r.title+' ['+r.source_id+']').join(' · ')}</small>}
              {step.result?.detected&&<small>Detected: {step.result.detected.join(', ')||'None'} · Missing: {(step.result.needs_setup||[]).join(', ')||'None'}</small>}
              {step.task_id&&<button className="task-linked" onClick={()=>setSelected(step.task_id)}>View linked task activity →</button>}
              {step.error&&<p role="alert" className="conversation-error">{step.error}</p>}
              {step.status==='awaiting_workspace'&&<div className="task-review-card"><p className="muted">Model tests require a configured target and observable trial results. This is a handoff, not a completed evaluation.</p><Button variant="primary" onClick={onOpenAITesting}>Open AI testing</Button></div>}
            </div></article>)}
            <Button onClick={async()=>{try{const result=await rpc<Row>('plans.report',{id:detail.id});const blob=new Blob([result.content],{type:'text/markdown;charset=utf-8'});const link=document.createElement('a');const url=URL.createObjectURL(blob);link.href=url;link.download=result.filename;link.click();URL.revokeObjectURL(url);}catch(e){setError(String(e));}}}>Export checkpoint report</Button>
            {detail.status==='draft'&&<Button disabled={!!busy} onClick={()=>perform('Planning task',()=>rpc('plans.run',{id:detail.id})).then(()=>reload())}>Run safe preflight</Button>}
            {detail.status==='awaiting_approval'&&detail.steps?.some((s:Row)=>s.key==='agents'&&s.status==='awaiting_approval')&&<div className="task-review-card">
              <strong>Approve a read-only agent review</strong><p>Choose installed coding agents. They cannot be granted local file edits from this plan.</p>
              <div className="agent-picker-choices"><label><input type="checkbox" checked={useClaude} onChange={e=>setUseClaude(e.target.checked)}/> Claude Code</label><label><input type="checkbox" checked={useCodex} onChange={e=>setUseCodex(e.target.checked)}/> Codex</label></div>
              <label className="field"><span>Project folder (optional)</span><input value={directory} onChange={e=>setDirectory(e.target.value)} placeholder="/Users/you/Projects/my-app"/></label>
              <Button variant="primary" disabled={!!busy||(!useClaude&&!useCodex)} onClick={()=>perform('Delegating approved task',()=>rpc('plans.approve',{id:detail.id,agents:[...(useClaude?['claude']:[]),...(useCodex?['codex']:[])],working_dir:directory.trim()})).then(()=>reload())}>Approve and launch read-only agents</Button>
            </div>}
            {detail.status==='awaiting_approval'&&detail.steps?.some((s:Row)=>s.key==='web'&&s.status==='awaiting_approval')&&<div className="task-review-card"><strong>Review web-testing scope</strong><p>Starting this investigation requires a project with a documented target and authorization. Individual state-changing requests still require their own approval.</p><Button variant="primary" disabled={!!busy} onClick={()=>perform('Starting approved investigation',()=>rpc('plans.approve',{id:detail.id})).then(()=>reload())}>Start authorized investigation</Button></div>}
          </section>}
          {detail.workers&&<div className="agents-workers">{Object.entries(detail.workers).map(([name,worker])=><span key={name}>{name}: <strong>{(worker as Row).status}</strong></span>)}</div>}
          {detail.approvals?.length>0&&<p role="status">{detail.approvals.length} request(s) need review in Tasks → Advanced tools → Web Testing → Approvals. No request is approved here automatically.</p>}
          {['tor_browser','memory_session'].includes(detail.kind)&&active(detail.status)&&<p className="muted">To stop this live session, return to its workspace and disconnect or close it there. Agents tracks verified status but does not replay or silently control the external session.</p>}
          {detail.status==='awaiting_workspace'&&<div className="task-review-card"><p>This handoff is not a completed scan. Connect memory and review a supported process before scanning.</p><Button onClick={onOpenMemory}>Open memory tools</Button></div>}
          <div className="agents-action-row" style={{display:'flex',gap:10,flexWrap:'wrap',margin:'14px 0'}}>
            {active(detail.status)&&!['tor_browser','memory_session'].includes(detail.kind)&&<Button icon={Square} variant="danger-quiet" disabled={!!busy} onClick={()=>perform('Stopping task',()=>rpc('tasks.cancel',{id:detail.id})).then(()=>reload())}>Stop task</Button>}
            {['failed','interrupted','completed','paused','cancelled'].includes(detail.status)&&!detail.legacy&&!['tor_browser','memory_session','orchestrated','website_audit'].includes(detail.kind)&&<Button icon={RotateCcw} disabled={!!busy} onClick={()=>perform('Retrying task',()=>rpc('tasks.retry',{id:detail.id})).then(()=>reload())}>Retry eligible task</Button>}
          </div>
          <div className="agents-event-list" aria-label="Recorded task events">
            {!(detail.events||[]).length?<p className="muted">No events reported yet.</p>:[...(detail.events||[])].reverse().map((event:Row,i:number)=><details key={event.id||i} className="agents-event"><summary><span><Activity size={14}/><b>{event.agent||event.kind||'Event'}</b> · {event.kind||'activity'}</span></summary><pre>{String(event.text||event.content||'')}</pre></details>)}
          </div>
          {detail.result&&<details className="agents-event"><summary><strong>Capture results</strong></summary><pre>{JSON.stringify(detail.result,null,2)}</pre></details>}
          {detail.summary&&<details className="agents-event" open><summary><strong>Investigation summary</strong></summary><pre>{detail.summary}</pre></details>}
          <p className="muted">Only actions actually reported by integrations appear here. Failed or interrupted tasks are not marked successful.</p>
        </>}
      </section>
    </div>
  </div>;
}
