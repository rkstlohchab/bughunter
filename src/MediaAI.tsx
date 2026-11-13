import { useEffect, useRef, useState } from 'react';
import { rpc } from './api';
import { Button, Field } from './components';
import { Clapperboard, RefreshCw,CheckCircle2, Circle,Monitor,Mic,Settings2,ShieldCheck } from 'lucide-react';
import ResourceModelPicker,{configAnalysisModel} from './ResourceModelPicker';
import SourceProgress from './SourceProgress';
import SourceControls from './SourceControls';
import type { MediaProgress } from './SourceProgress';

type MediaStatus = {
  platform?: string; config: {gemini_vision_model?:string;api_vision_model?:string;hosted_vision_consent?:boolean;vision_effort?:string;enabled:boolean;vision_provider:'local'|'codex_subscription'|'claude_subscription'|'gemini_subscription'|'gemini'|'openai'|'anthropic';vision_model:string;codex_vision_model:string;codex_detail_model:string;codex_vision_consent:boolean;claude_vision_model:string;claude_vision_consent:boolean;claude_parallel_enabled:boolean;claude_parallel_after:number;frame_interval:number;max_frames:number;adaptive_frames:boolean;scene_threshold:number;vision_enabled:boolean;speech_enabled:boolean};
  ollama_bin:boolean;ollama_running:boolean;installed_models:string[];vision_ready:boolean;
  codex_available?:boolean;
  claude_available?:boolean;gemini_available?:boolean;
  verification?:{ok:boolean;model:string;provider:string;detail?:string}|null;
  test_job?:{state:string;detail?:string;model?:string};
  ffmpeg:boolean;whisper:boolean;speech_model:boolean;
  setup:{state:string;detail:string;target:string|null;completed_bytes?:number;total_bytes?:number;percent?:number};
};
type LocalModel = {id:string;name?:string;label?:string;roles:string[];download_bytes?:number;installed:boolean;memory_note?:string};
type Catalog = {models:LocalModel[];vision_models?:LocalModel[];chat_models?:LocalModel[];disk?:{free_bytes:number;reserve_bytes:number};error?:string|null};
type VisionModels = {ready:boolean;models:{id:string;label?:string;default?:boolean}[];detail?:string};
const defaults:MediaStatus['config']={enabled:true,vision_provider:'local',vision_model:'qwen3-vl:2b',codex_vision_model:'gpt-6.1-sol',codex_detail_model:'',codex_vision_consent:false,claude_vision_model:'sonnet',claude_vision_consent:false,claude_parallel_enabled:false,claude_parallel_after:0,frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.30,vision_enabled:true,speech_enabled:true};
type Source = {id:string;title?:string;filename?:string;media_type?:string;detected_format?:string;status?:string;media_progress?:MediaProgress};
type Clip = {start?:number;end?:number;locator?:string;modality?:string;text:string};
function bytes(value:number|undefined){return value ? `${(value/1024**3).toFixed(1)} GiB` : 'size checked before download';}
function stamp(seconds:number|undefined){let s=Math.floor(Number(seconds||0));return `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;}

export default function MediaAI({sources,mode='viewer',onOpenSettings,initialSourceId='',tasksStopped=false}:{tasksStopped?:boolean;initialSourceId?:string;sources:Source[];mode?:'viewer'|'settings';onOpenSettings?:()=>void}) {
  const [status,setStatus]=useState<MediaStatus|null>(null);
  const [working,setWorking]=useState(''); const [error,setError]=useState('');
  const [approved,setApproved]=useState(false);
  const [toolsOpen,setToolsOpen]=useState(false);
  const [selected,setSelected]=useState(initialSourceId);
  const [showCount,setShowCount]=useState(100);
  const [timeline,setTimeline]=useState<Clip[]>([]);
  const [changes,setChanges]=useState<Partial<MediaStatus['config']>>({});
  const [models,setModels]=useState<VisionModels|null>(null);
  const [modelsLoading,setModelsLoading]=useState(false);
  const [modelsError,setModelsError]=useState('');
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [catalogError,setCatalogError]=useState('');
  const [chatModel,setChatModel]=useState('');
  const [stopped,setStopped]=useState(false);
  const generation=useRef(0);
  const statusRequest=useRef(0);
  const modelRequest=useRef(0);
  const mounted=useRef(true);
  const setupMode = mode === 'settings';
  const config={...defaults,...status?.config,...changes};
  const hosted=config.vision_provider!=='local';
  const claude=config.vision_provider==='claude_subscription';
  const hostedName=claude?'Claude':config.vision_provider==='codex_subscription'?'ChatGPT':config.vision_provider==='gemini_subscription'?'Google AI':config.vision_provider==='gemini'?'Gemini API':config.vision_provider==='openai'?'OpenAI API':'Anthropic API';
  const hostedModel=configAnalysisModel(config).model;
  const consentKey=claude?'claude_vision_consent':config.vision_provider==='codex_subscription'?'codex_vision_consent':'hosted_vision_consent';
  const hostedConsent=Boolean(config[consentKey]);
  const videoSources=sources.filter(s=>/video|audio|mp4|mov|mkv|m4a|webm|wav|mp3/i.test(`${s.media_type||''} ${s.detected_format||''} ${s.filename||''}`));
  async function refresh(){const request=++statusRequest.current;try{const value=await rpc<MediaStatus>('media.status');if(mounted.current&&request===statusRequest.current)setStatus(value);}catch(e){if(mounted.current&&request===statusRequest.current)setError(String(e));}}
  async function refreshCatalog(){try{const value=await rpc<Catalog>('media.catalog');if(mounted.current){setCatalog(value);setCatalogError('');}}catch(e){if(mounted.current)setCatalogError(String(e));}}
  async function refreshModels(){const request=++modelRequest.current;setModelsLoading(true);setModelsError('');try{const result=await rpc<VisionModels>('media.models',claude?{provider:'claude_subscription'}:{});if(mounted.current&&request===modelRequest.current)setModels(result);}catch(e){if(mounted.current&&request===modelRequest.current){setModels(null);setModelsError(String(e));}}finally{if(mounted.current&&request===modelRequest.current)setModelsLoading(false);}}
  useEffect(()=>{mounted.current=true;refresh();const id=setInterval(refresh,3500);const stop=()=>{generation.current++;setStopped(true);setWorking('');};const resume=()=>setStopped(false);window.addEventListener('bughunter-stop-all',stop);window.addEventListener('bughunter-resume-all',resume);return ()=>{mounted.current=false;generation.current++;clearInterval(id);window.removeEventListener('bughunter-stop-all',stop);window.removeEventListener('bughunter-resume-all',resume);};},[]);
  useEffect(()=>{if(setupMode)void refreshCatalog();},[setupMode,status?.setup?.state]);
  useEffect(()=>{if(setupMode&&config.vision_provider==='codex_subscription')void refreshModels();else{modelRequest.current++;setModels(null);setModelsLoading(false);}},[setupMode,config.vision_provider]);
  useEffect(()=>{if(!selected){setTimeline([]);return;}let active=true;
    const reload=()=>rpc<{items:Clip[]}>('media.timeline',{source_id:selected}).then(r=>{if(active)setTimeline(r.items||[]);}).catch(e=>{if(active)setError(String(e));});
    reload();const id=setInterval(reload,4000);return ()=>{active=false;clearInterval(id);};},[selected]);
  async function action(label:string,method:string,p:Record<string,unknown>={}){
    if(stopped||tasksStopped)return;
    const version=generation.current;setWorking(label);setError('');
    try{
      if(method==='media.test'||(method==='media.setup'&&p.target==='vision')){
        await rpc('media.configure',config);
        if(version!==generation.current)return;
      }
      const result=await rpc(method,p);
      if(version!==generation.current)return;
      if(method==='media.configure'){
        statusRequest.current++;
        setStatus(current=>current?{...current,config:result}:current);
        // Keep edits made after Save was pressed. A completed request must not
        // replace a newly chosen backend or model with the older submitted one.
        setChanges(current=>Object.fromEntries(Object.entries(current).filter(([key,value])=>value!==p[key])));
      }
      await refresh();
    }catch(e){if(version===generation.current)setError(String(e));}
    finally{if(version===generation.current)setWorking('');}
  }
  const localModels=(catalog?.vision_models || catalog?.models?.filter(model=>model.roles.includes('vision')) || []);
  const chatModels=(catalog?.chat_models || catalog?.models?.filter(model=>model.roles.includes('chat')) || []);
  const selectedLocal=localModels.find(model=>model.id===config.vision_model);
  const selectedChat=chatModels.find(model=>model.id===chatModel);
  const disabled=tasksStopped||stopped||!!working||status?.setup?.state==='running'||status?.test_job?.state==='running';
  const hostedModels=models?.models||[];
  const visionReady=Boolean((!hosted||hostedConsent)&&status?.vision_ready&&status?.verification?.ok&&status.verification.model===(hosted?hostedModel:config.vision_model)&&status.verification.provider===config.vision_provider);
  return <section className="surface media-ai"><div className="section-title"><div><h2><Clapperboard size={19}/> {setupMode?'Video AI setup':'Video timeline'}</h2><p>{setupMode?'Configure screen analysis and local transcription here.':'Review what the AI heard and saw at each point in a recording.'}</p></div>{setupMode&&<Button onClick={()=>{refresh();void refreshCatalog();if(hosted)void refreshModels();}} icon={RefreshCw}>Recheck</Button>}</div>
    {setupMode && <div className="video-setup-body">
      <div className="video-setup-overview">
        {[{title:'Screen analysis',ready:visionReady,detail:visionReady?'Image test passed':'Choose a model and test an image',icon:Monitor},
          {title:'Local transcription',ready:Boolean(status?.whisper&&status?.speech_model),detail:status?.whisper&&status.speech_model?'Whisper is ready on this Mac':'Set up Whisper to hear the recording',icon:Mic},
          {title:'Video tools',ready:Boolean(status?.ffmpeg),detail:status?.ffmpeg?'FFmpeg is ready':'Install FFmpeg to extract frames',icon:Clapperboard}].map(item=><div className="video-readiness-card" key={item.title}><item.icon size={20}/><div><strong>{item.title}</strong><span>{item.detail}</span></div>{item.ready?<CheckCircle2 size={18} className="ready"/>:<Circle size={18}/>}</div>)}
      </div>
      <section className="video-setup-step" aria-label="Choose video analysis AI"><div className="video-step-heading"><span>1</span><div><h3>Choose the AI that reads your screens</h3><p>This is the default. You can change the model for each resource in Library.</p></div></div>
        <ResourceModelPicker value={configAnalysisModel(config)} allowDefault={false} disabled={disabled} onChange={choice=>{if(!choice)return;const key=choice.provider==='local'?'vision_model':choice.provider==='codex_subscription'?'codex_vision_model':choice.provider==='claude_subscription'?'claude_vision_model':choice.provider==='gemini_subscription'?'gemini_vision_model':'api_vision_model';setChanges(current=>({...current,vision_provider:choice.provider,[key]:choice.model,vision_effort:choice.effort??'medium'}));}}/>
        {hosted&&<label className="video-sharing check-row"><input type="checkbox" checked={hostedConsent} onChange={event=>setChanges(current=>({...current,[consentKey]:event.target.checked}))}/>I consent to sending selected frames and nearby narration to {hostedName} for video analysis.</label>}
        {!hosted&&<div className="button-group wrap"><Button disabled={disabled} onClick={()=>action('Starting Ollama','media.start')}>Start local model</Button><Button disabled={disabled} onClick={()=>action('Stopping Ollama','media.stop')}>Stop app-managed model</Button></div>}
        <p className="video-privacy"><ShieldCheck size={16}/>The original video and audio stay on your Mac. This workflow sends sampled frames and narration, not a full-video upload.</p>
      </section>
      <section className="video-setup-step" aria-label="Video learning preferences"><div className="video-step-heading"><span>2</span><div><h3>Choose what to extract</h3><p>Capture the explanation and the screen together.</p></div></div>
        <div className="video-extraction-options"><label className="video-option"><input type="checkbox" checked={config.vision_enabled} onChange={e=>setChanges({...changes,vision_enabled:e.target.checked})}/><Monitor size={19}/><span><strong>Read the screen</strong><small>Timestamped observations linked to the surrounding narration.</small></span></label><label className="video-option"><input type="checkbox" checked={config.speech_enabled} onChange={e=>setChanges({...changes,speech_enabled:e.target.checked})}/><Mic size={19}/><span><strong>Transcribe the audio locally</strong><small>Whisper processes speech on this Mac.</small></span></label></div>
        <div className="video-sampling-summary"><strong>Current sampling</strong><span>Every {config.frame_interval} seconds{config.adaptive_frames?' + scene changes':''} · {config.max_frames===0?'No frame cap':`${config.max_frames} frames maximum`}</span></div>
        <details className="video-advanced"><summary><Settings2 size={16}/>Sampling and advanced options</summary><div className="video-advanced-body">
          <Field label="Sample one frame every (seconds)" hint="Shorter intervals capture more UI changes and use more model requests."><input type="number" min={2} max={120} value={config.frame_interval} onChange={e=>setChanges({...changes,frame_interval:Number(e.target.value)})}/></Field>
          <label className="check-row"><input type="checkbox" checked={config.adaptive_frames} onChange={e=>setChanges({...changes,adaptive_frames:e.target.checked})}/>Also capture important screen changes</label>
          <label className="check-row"><input type="checkbox" checked={config.max_frames===0} onChange={e=>setChanges({...changes,max_frames:e.target.checked?0:300})}/>Process entire video (no frame cap)</label>
          {config.max_frames>0&&<Field label="Maximum sampled frames"><input type="number" min={1} max={1000000} value={config.max_frames} onChange={e=>setChanges({...changes,max_frames:Number(e.target.value)})}/></Field>}
          <label className="check-row"><input type="checkbox" checked={config.enabled} onChange={e=>setChanges({...changes,enabled:e.target.checked})}/>Enable video enrichment</label>
          {config.vision_provider==='codex_subscription'&&<><Field label="Hard-to-read frame model" hint="Optional higher-resolution retry model."><select value={config.codex_detail_model} onChange={e=>setChanges({...changes,codex_detail_model:e.target.value})}><option value="">Use selected frame model</option>{config.codex_detail_model&&!hostedModels.some(m=>m.id===config.codex_detail_model)&&<option value={config.codex_detail_model}>{config.codex_detail_model}</option>}{hostedModels.map(m=><option key={m.id} value={m.id}>{m.label||m.id}</option>)}</select></Field>
            <label className="check-row"><input type="checkbox" checked={config.claude_parallel_enabled} onChange={e=>setChanges({...changes,claude_parallel_enabled:e.target.checked})}/>Use Claude alongside ChatGPT for concurrent imports</label>
            {config.claude_parallel_enabled&&<><Field label="Parallel Claude model"><select value={config.claude_vision_model} onChange={e=>setChanges({...changes,claude_vision_model:e.target.value})}><option value="claude-sonnet-4-6">Claude Sonnet 4.6</option><option value="claude-opus-4-7">Claude Opus 4.7</option><option value="claude-sonnet-5-5">Claude Sonnet 5.5</option></select></Field><label className="check-row"><input type="checkbox" checked={config.claude_vision_consent} onChange={e=>setChanges({...changes,claude_vision_consent:e.target.checked})}/>I consent to sending selected frames and nearby narration to Claude for parallel analysis.</label><Button disabled={disabled} onClick={()=>setChanges({...changes,claude_parallel_after:Date.now()/1000+3600})}>Schedule Claude in one hour</Button></>}
          </>}
        </div></details>
        <p className="muted">Screenshots can miss brief actions between samples. Saved descriptions are cited evidence, not proof that every click was captured.</p>
      </section>
      <section className="video-setup-step" aria-label="Save and verify video AI"><div className="video-step-heading"><span>3</span><div><h3>Save and verify</h3><p>A real image test checks the selected model before it is marked ready.</p></div></div>
        <div className="button-group wrap"><Button variant="primary" disabled={!status||disabled||(hosted&&!hostedConsent)||(config.claude_parallel_enabled&&!config.claude_vision_consent)} onClick={()=>action('Saving','media.configure',config)}>Save video settings</Button><Button disabled={!status||disabled||(hosted&&!hostedConsent)||(!hosted&&!selectedLocal?.installed)} onClick={()=>action('Testing frame analysis','media.test')}>Test selected frame model</Button><Button onClick={()=>setToolsOpen(value=>!value)}>Set up local tools</Button></div>
        <p role="status" className={`video-test-result ${visionReady?'verified':''}`}>{visionReady?'Image connection verified.':status?.test_job?.state==='running'?'Testing an image…':status?.test_job?.detail||'Image test not yet verified for this selection.'}</p>
      </section>
      <details className="video-tools" open={toolsOpen} onToggle={event=>setToolsOpen(event.currentTarget.open)}><summary>Local tools and optional model downloads</summary><div className="video-advanced-body">
        <p>Transcription needs FFmpeg, whisper.cpp and a speech model. Local screen analysis also needs Ollama and vision weights.</p>
        {catalog?.disk&&<p className="muted">Free disk: {bytes(catalog.disk.free_bytes)} · Reserve: {bytes(catalog.disk.reserve_bytes)}</p>}
        {selectedLocal&&!hosted&&<p className="muted">Selected vision download: {bytes(selectedLocal.download_bytes)} · {selectedLocal.memory_note}</p>}
        <label className="check-row"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/>I approve downloading and installing the component I select.</label>
        <div className="media-install-grid">{([['ollama','Install Ollama',status?.ollama_bin],['ffmpeg','Install FFmpeg',status?.ffmpeg],['whisper','Install whisper.cpp',status?.whisper],['speech','Download Whisper model',status?.speech_model],['vision','Download selected local model',!!selectedLocal?.installed],['chat','Download selected chat model',!!selectedChat?.installed]] as const).filter(([value])=>!hosted||!['ollama','vision'].includes(value)).map(([value,label,ready])=><div key={value} className="media-install-row"><span>{label.replace(/^Install |^Download /,'')}</span><Button disabled={!!ready||!approved||disabled||(value==='vision'&&!selectedLocal)||(value==='chat'&&!selectedChat)} onClick={()=>action(`Installing ${value}`,'media.setup',{target:value,approved:true,...(value==='vision'?{model_id:config.vision_model,role:'vision'}:value==='chat'?{model_id:chatModel,role:'chat'}:{})})}>{ready?'Installed':label}</Button></div>)}</div>
        {status?.setup?.state==='running'&&<progress aria-label="Model download progress" max={100} value={status.setup.percent||0}/>}
        <p role="status">Setup: {status?.setup?.state||'idle'} · {status?.setup?.detail||'No installation underway'}{status?.setup?.total_bytes?` · ${bytes(status.setup.completed_bytes)} / ${bytes(status.setup.total_bytes)}`:''}</p>
        <Field label="Local chat model" hint="Optional. Separate from the model that reads video frames."><select aria-label="Local chat model" value={chatModel} onChange={e=>setChatModel(e.target.value)}><option value="">Select a local chat model</option>{chatModels.map(m=><option key={m.id} value={m.id}>{m.label||m.name||m.id}{m.installed?' · installed':''}</option>)}</select></Field>
        {selectedChat&&<><p className="muted">{bytes(selectedChat.download_bytes)} · {selectedChat.memory_note}</p><Button disabled={disabled||!selectedChat.installed} onClick={()=>action('Selecting local chat model','models.select',{provider:'ollama',model:chatModel})}>Use selected local chat model</Button></>}
        {catalogError&&<p role="alert" className="error-text">{catalogError}</p>}
      </div></details>
      {(modelsLoading||modelsError)&&<p role="status" className="muted">{modelsLoading?'Checking optional retry models…':modelsError}</p>}
    </div>}
    {!setupMode && !status?.vision_ready && !status?.speech_model && <div className="info-note"><p>Video AI tools aren't ready yet. Existing text stays available. Install or check video tools in Settings.</p>{onOpenSettings&&<Button onClick={onOpenSettings}>Open Settings</Button>}</div>}
    {!setupMode && <div className="media-timeline"><div className="section-title"><div><h3>Video timeline</h3><p>Select a previously imported recording to see time-stamped speech and screen descriptions.</p></div></div>
      <Field label="Imported video / audio"><select value={selected} onChange={e=>{setSelected(e.target.value);setShowCount(100);}}><option value="">Select a recording</option>{videoSources.map(s=><option key={s.id} value={s.id}>{s.title||s.filename||s.id} · {s.status}</option>)}</select></Field>
      {selected&&<><SourceControls source={videoSources.find(s=>s.id===selected)||{id:selected,status:'queued'}} stopped={tasksStopped||stopped} disabled={disabled}/>

      <SourceProgress status={videoSources.find(s=>s.id===selected)?.status} progress={videoSources.find(s=>s.id===selected)?.media_progress}/>
      <div className="media-entries">{timeline.length?timeline.slice(0,showCount).map((item,i)=><div className="media-entry" key={i}><div><strong>{stamp(item.start)}{item.end!=null?` – ${stamp(item.end)}`:''}</strong><small>{item.modality||'text'}</small></div><p>{item.text}</p></div>):<p className="muted">No timestamped transcript or visual descriptions yet. Import or reprocess this recording after setup.</p>}</div>{timeline.length>showCount && <Button onClick={()=>setShowCount(n=>n+100)}>Show 100 more ({timeline.length-showCount} remaining)</Button>}</>}
    </div>}
    {(stopped||tasksStopped)&&<p role="status">All tasks are stopped. Resume tasks before starting setup or analysis.</p>}
    {working&&<p role="status">{working}…</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </section>;
}
