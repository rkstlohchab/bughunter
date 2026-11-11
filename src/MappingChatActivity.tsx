import { useState } from 'react';
import { Check, LoaderCircle, Square } from 'lucide-react';
import { mappingActive, useMappingChat } from './useMappingChat';
import { Button } from './components';
import './mapping-chat.css';

export default function MappingChatActivity({controller}:{controller:ReturnType<typeof useMappingChat>}) {
  const {data,programs,attached,approval,error,busy,connecting}=controller;
  const [filter,setFilter]=useState('');
  const [pid,setPid]=useState('');
  if (!data) return <section className="agent-live mapping-chat" aria-label="Mapping task activity">
    {error?<><p role="alert">{error}</p><Button onClick={controller.refreshPrograms}>Retry loading task</Button></>:<p role="status">Loading the saved mapping task…</p>}
  </section>;
  const workflow=data.workflow;
  const running=!!connecting || ['researching','checking'].includes(workflow.status);
  const visible=programs.filter(p=>p.program.toLowerCase().includes(filter.toLowerCase()) || String(p.pid)===filter);
  const selected=visible.find(p=>String(p.pid)===pid);
  return <section className="agent-live mapping-chat" aria-label="Mapping task activity">
    <div className="agent-live-head" role="status">{running?<LoaderCircle className="spin" size={16}/>:<Check size={16}/>}
      <strong>{connecting?'connecting':workflow.status.replaceAll('_',' ')}</strong>
      {mappingActive(workflow.status) && <button className="chat-tool-btn" disabled={busy} aria-label="Stop mapping task" onClick={()=>void controller.stop()}><Square size={15}/></button>}
    </div>
    <p role="status">{connecting?`Connecting to ${connecting}… Checking process access.`:workflow.progress}</p>
    {workflow.context && <p className="muted">{workflow.context.program} · PID {workflow.context.pid}</p>}
    {error && <p role="alert" className="conversation-error">{error}</p>}
    {workflow.status==='blocked' && <Button disabled={busy} onClick={()=>void controller.reply('choose program')}>Choose program again</Button>}
    {workflow.status==='needs_program' && <div className="mapping-chat-selection">
      <p>Select the program to attach and inspect. I’ll run the research and checks from here.</p>
      {attached && <Button disabled={busy} onClick={()=>void controller.select(attached,true)}>Use attached {attached.program}</Button>}
      <label>Find a running program<input disabled={busy} value={filter} onChange={e=>{setFilter(e.target.value);setPid('');}} placeholder="Program name or PID" /></label>
      <label>Program<select disabled={busy} value={pid} onChange={e=>setPid(e.target.value)}><option value="">Choose a program</option>
        {visible.map(p=><option key={p.pid} value={p.pid}>{p.program} · PID {p.pid}</option>)}
      </select></label>
      <div className="button-group wrap"><Button variant="primary" disabled={!selected||busy} onClick={()=>selected && void controller.select(selected)}>{connecting?'Connecting…':'Use selected program'}</Button>
        <Button disabled={busy} onClick={controller.refreshPrograms}>Refresh programs</Button></div>
    </div>}
    {approval && <div className="mapping-chat-approval" role="alertdialog" aria-label="Approve mapping check">
      <p>Your memory settings require review of this read-only check.</p><pre>{JSON.stringify(approval.request,null,2)}</pre>
      <div className="button-group wrap"><Button disabled={busy} onClick={()=>void controller.approve()}>Approve this check</Button><Button disabled={busy} onClick={()=>void controller.stop()}>Stop task</Button></div>
    </div>}
    {data.research && <><p>{data.research.references.length} references · {data.research.candidates.length} candidates · {data.research.verified_count} verified</p>
      <details><summary>Sources and check history</summary>
        {data.research.references.map((ref:any)=><p key={ref.id}>{/^https?:\/\//i.test(ref.url||'')?<a href={ref.url} target="_blank" rel="noreferrer">{ref.title}</a>:ref.title}{ref.locator?` · ${ref.locator}`:''}</p>)}
        <ol>{[...(data.research.events||[]),...workflow.events].map((event,i)=><li key={i}>{event.text}</li>)}</ol>
        {data.research.candidates.map((candidate:any)=><div key={candidate.id}><strong>{candidate.label} · {candidate.status.replaceAll('_',' ')}</strong>
          {(candidate.observations||[]).map((o:any)=><p key={o.check_id}>{o.error || `Read ${o.value_text??o.value}; expected ${o.expected_text??o.expected}; ${o.matched?'matched':'mismatch'}`}</p>)}</div>)}
      </details><Button disabled={busy} onClick={()=>void controller.exportEvidence()}>Download mapping evidence</Button></>}
  </section>;
}
