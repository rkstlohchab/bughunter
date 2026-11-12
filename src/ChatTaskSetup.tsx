import {useEffect,useRef,useState} from 'react';
import {Button,Field} from './components';
import {rpc,type RecordData as Row} from './api';

export default function ChatTaskSetup({plan,projectId,sessionId,stopped,onCancel,onStarted,selectProject}: {
  plan:Row;projectId?:string;sessionId?:string;stopped:boolean;onCancel:()=>void;
  onStarted:(session:Row)=>Promise<void>;selectProject?:(id:string)=>Promise<void>;
}) {
  const cancelled=useRef(false);
  useEffect(()=>{cancelled.current=false;const cancel=()=>{cancelled.current=true;};window.addEventListener('bughunter-stop-all',cancel);return()=>{cancel();window.removeEventListener('bughunter-stop-all',cancel);};},[]);
  const [project,setProject]=useState<Row|null>(null);
  const [url,setUrl]=useState((String(plan.message).match(/https?:\/\/[^\s<>]+/)||[])[0]||'');
  const [paths,setPaths]=useState('/');
  const [scope,setScope]=useState('Public-page review only. No account, checkout or state-changing actions.');
  const [agreed,setAgreed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [pages,setPages]=useState(12);
  const [render,setRender]=useState(false);
  const [loaded,setLoaded]=useState(!projectId);
  useEffect(()=>{let alive=true;if(projectId)void rpc<Row>('projects.get',{id:projectId}).then(p=>{if(alive){setProject(p);setLoaded(true);}}).catch(e=>{if(alive){setError(String(e));setLoaded(true);}});return()=>{alive=false;};},[projectId]);
  const website=plan.workflow==='website';
  const existing=!!project?.scope?.length;
  async function start(){
    setBusy(true);setError('');
    try {
      const result=await rpc<Row>('chat.task_start',{id:sessionId,message:plan.message,source_ids:plan.source_ids,
        project_id:projectId,approved:agreed,max_pages:pages,render_public:render,
        expected_scope:plan.scope_snapshot,
        ...(!existing&&website?{website:{url,read_only_paths:paths.split(/\n/).map(p=>p.trim()).filter(Boolean),authorization:scope}}:{})});
      if(stopped||cancelled.current)return;
      if(result.project_id && result.project_id!==projectId){localStorage.setItem(`bughunter.chat.${result.project_id}`,result.id);await selectProject?.(result.project_id);}
      await onStarted(result);
    } catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  }
  return <section className="chat-task-setup" aria-label="Task setup">
    <h3>Review and start</h3><p>{plan.explanation}</p>
    {website ? <>
      {existing ? <p><strong>{project.name}</strong><br/>{project.scope.join(', ')}<br/>Read-only paths: {(project.read_only_paths||[]).join(', ')||'No safe paths configured. Add them in the website workspace.'}</p> : <>
        <Field label="Website address"><input type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://your-site.example/"/></Field>
        <Field label="Pages safe to read" hint="One path per line. / means the homepage. Add /assets/* for its static files if needed."><textarea rows={3} value={paths} onChange={e=>setPaths(e.target.value)}/></Field>
        <Field label="Permission and limits"><textarea rows={3} value={scope} onChange={e=>setScope(e.target.value)}/></Field>
      </>}
      <Field label="Maximum pages"><input type="number" min={1} max={30} value={pages} onChange={e=>setPages(Number(e.target.value))}/></Field>
      <label className="check-row"><input type="checkbox" checked={render} onChange={e=>setRender(e.target.checked)}/>Render the public page and declared assets (up to 30 additional GET requests).</label>
      <p className="muted">The app connects its web gateway, runs these checks and posts the report here. Login, forms, orders and payments are outside this review.</p>
    </> : !!plan.missing?.length && <ul>{plan.missing.map((item:string)=><li key={item}>{item}</li>)}</ul>}
    <label className="check-row"><input type="checkbox" checked={agreed} onChange={e=>setAgreed(e.target.checked)}/>{website?'I have permission to read these pages and approve this bounded review.':'I approve the task described above.'}</label>
    <p className="muted">Stop all is always available at the top right. Work outside this scope will need a separate review.</p>
    {error&&<p role="alert" className="conversation-error">{error}</p>}
    <div className="button-group"><Button variant="primary" disabled={busy||stopped||!loaded||!agreed||(website&&!existing&&(!url.trim()||!paths.trim()||!scope.trim()))||(website&&existing&&!project?.read_only_paths?.length)} onClick={()=>void start()}>{busy?'Starting…':'Start task'}</Button><Button disabled={busy} onClick={onCancel}>Cancel</Button></div>
  </section>;
}
