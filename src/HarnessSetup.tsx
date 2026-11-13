import {useState} from 'react';
import {LockKeyhole,LoaderCircle} from 'lucide-react';
import {rpc} from './api';
import type {RecordData as Row} from './api';

const permissions=[
 ['integrations','Connected integrations','Use plugins connected in Settings, such as GitHub and Vercel.'],
 ['app','App and library','Use app features and saved sources.'],
 ['computer','Computer control','Read controls, capture screenshots, and operate apps. Screen content can be shared with your selected model.'],
 ['files','Local files','Read and write files needed for this task.'],
 ['shell','Commands and adapters','Run local commands and existing tool adapters.'],
 ['install','Install missing tools','Download supported dependencies with your system package manager.'],
] as const;
export default function HarnessSetup({message,mode,sessionId,projectId,sourceIds,attachmentIds=[],visionMode='current',webSearchMode='auto',stopped,initialGrants,executionMode='auto',background=true,onCancel,onStarted}:{message:string;mode:string;sessionId?:string;projectId?:string;sourceIds:string[];attachmentIds?:string[];visionMode?:string;webSearchMode?:string;stopped:boolean;initialGrants?:string[];executionMode?:string;background?:boolean;onCancel:()=>void;onStarted:(data:Row)=>void}){
 const [grants,setGrants]=useState<string[]>(initialGrants||['app',...(/\b(?:github|vercel|plugin|connector|deploy)\b/i.test(message)?['integrations']:[]),...(/\b(?:install|download)\b/i.test(message)?['install']:[]),...(mode==='game'?[]:['computer'])]);
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 async function start(){
  setBusy(true);setError('');
  try{onStarted(await rpc<Row>('harness.start',{id:sessionId,message,mode,project_id:projectId||null,source_ids:sourceIds,attachment_ids:attachmentIds,vision_mode:visionMode,web_search_mode:webSearchMode,grants,execution_mode:executionMode,background}));}
  catch(error){setError(error instanceof Error?error.message:String(error));}
  finally{setBusy(false);}
 }
 return <section className="harness-setup" aria-label="Task permissions">
  <div className="harness-title"><LockKeyhole size={18}/><h3>Allow access for this task</h3></div>
  <p>{message}</p><p className="muted">{executionMode==='plan'?'Plan mode inspects and proposes steps. Changes and installations are blocked.':executionMode==='full'?'Full access allows app controls, files, commands, and installations for this task.':'Choose once. The agent completes permitted steps and asks when information is missing.'} {background?'Background actions keep your keyboard and mouse free.':'Foreground actions can use your keyboard and mouse.'} Stop all revokes this task’s access.</p>
  {executionMode==='auto'&&<div className="harness-permissions">{permissions.map(([id,label,detail])=><label key={id}><input type="checkbox" checked={grants.includes(id)} disabled={busy||stopped} onChange={event=>setGrants(previous=>event.target.checked?[...previous,id]:previous.filter(value=>value!==id))}/><span><strong>{label}</strong><small>{detail}</small></span></label>)}</div>}
  <p className="muted">Your operating system may still ask for Accessibility, screen access, or an administrator password.</p>
  {error&&<p role="alert">{error}</p>}
  <div className="button-group"><button type="button" className="button primary" onClick={()=>void start()} disabled={busy||stopped||!grants.length}>{busy?<><LoaderCircle size={15} className="spin"/> Starting…</>:'Allow and start'}</button><button type="button" className="button" onClick={onCancel} disabled={busy}>Cancel</button></div>
 </section>;
}
