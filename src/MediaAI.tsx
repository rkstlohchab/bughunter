import { useEffect, useState } from 'react';
import { rpc } from './api';
import { Button, Field } from './components';
import { Clapperboard, RefreshCw } from 'lucide-react';
import SourceProgress from './SourceProgress';
import type { MediaProgress } from './SourceProgress';

type MediaStatus = {
  platform?: string; config: {enabled:boolean;vision_provider:'local'|'codex_subscription';vision_model:string;codex_vision_model:string;codex_vision_consent:boolean;frame_interval:number;max_frames:number;adaptive_frames:boolean;scene_threshold:number;vision_enabled:boolean;speech_enabled:boolean};
  ollama_bin:boolean;ollama_running:boolean;installed_models:string[];vision_ready:boolean;
  codex_available?:boolean;
  ffmpeg:boolean;whisper:boolean;speech_model:boolean;
  setup:{state:string;detail:string;target:string|null};
};
type VisionModels = {ready:boolean;models:{id:string;label?:string;default?:boolean}[];detail?:string};
const defaults:MediaStatus['config']={enabled:true,vision_provider:'local',vision_model:'qwen3-vl:2b',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:false,frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.30,vision_enabled:true,speech_enabled:true};
type Source = {id:string;title?:string;filename?:string;media_type?:string;detected_format?:string;status?:string;media_progress?:MediaProgress};
type Clip = {start?:number;end?:number;locator?:string;modality?:string;text:string};
function stamp(seconds:number|undefined){let s=Math.floor(Number(seconds||0));return `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;}

export default function MediaAI({sources,mode='viewer',onOpenSettings}:{sources:Source[];mode?:'viewer'|'settings';onOpenSettings?:()=>void}) {
  const [status,setStatus]=useState<MediaStatus|null>(null);
  const [working,setWorking]=useState(''); const [error,setError]=useState('');
  const [approved,setApproved]=useState(false);
  const [selected,setSelected]=useState('');
  const [showCount,setShowCount]=useState(100);
  const [timeline,setTimeline]=useState<Clip[]>([]);
  const [changes,setChanges]=useState<Partial<MediaStatus['config']>>({});
  const [models,setModels]=useState<VisionModels|null>(null);
  const [modelsLoading,setModelsLoading]=useState(false);
  const [modelsError,setModelsError]=useState('');
  const setupMode = mode === 'settings';
  const config={...defaults,...status?.config,...changes};
  const hosted=config.vision_provider==='codex_subscription';
  const videoSources=sources.filter(s=>/video|audio|mp4|mov|mkv|m4a|webm|wav|mp3/i.test(`${s.media_type||''} ${s.detected_format||''} ${s.filename||''}`));
  function refresh(){rpc<MediaStatus>('media.status').then(setStatus).catch(e=>setError(String(e)));}
  async function refreshModels(){setModelsLoading(true);setModelsError('');try{setModels(await rpc<VisionModels>('media.models'));}catch(e){setModels(null);setModelsError(String(e));}finally{setModelsLoading(false);}}
  useEffect(()=>{refresh();const id=setInterval(refresh,3500);return ()=>clearInterval(id);},[]);
  useEffect(()=>{if(setupMode&&hosted)void refreshModels();},[setupMode,hosted]);
  useEffect(()=>{if(!selected){setTimeline([]);return;}let active=true;
    const reload=()=>rpc<{items:Clip[]}>('media.timeline',{source_id:selected}).then(r=>{if(active)setTimeline(r.items||[]);}).catch(e=>{if(active)setError(String(e));});
    reload();const id=setInterval(reload,4000);return ()=>{active=false;clearInterval(id);};},[selected]);
  async function action(label:string,method:string,p:any={}){setWorking(label);setError('');try{if(method==='media.setup' && p.target==='vision') await rpc('media.configure',config);await rpc(method,p);refresh();}catch(e){setError(String(e));}finally{setWorking('');}}
  const localModels=Array.from(new Set(['qwen3-vl:2b','qwen3-vl:4b','qwen3-vl:8b',...(status?.installed_models||[]),config.vision_model]));
  const hostedModels=models?.models||[];
  const visionReady=hosted?Boolean(config.codex_vision_consent&&status?.codex_available):Boolean(status?.ollama_running&&(status?.installed_models||[]).includes(config.vision_model));
  return <section className="surface media-ai"><div className="section-title"><div><h2><Clapperboard size={19}/> {setupMode?'Video AI setup':'Video timeline'}</h2><p>{setupMode?'Configure screen analysis and local transcription here.':'Review what the AI heard and saw at each point in a recording.'}</p></div>{setupMode&&<Button onClick={()=>{refresh();if(hosted)void refreshModels();}} icon={RefreshCw}>Recheck</Button>}</div>
    {setupMode && <><div className="media-metrics"><span>{hosted?`Codex ${status?.codex_available?'● installed':'○ not installed'}`:`Ollama ${status?.ollama_running?'● running':'○ stopped'}`}</span><span>Frame analysis {visionReady?'● configured':'○ needs setup'}</span><span>Whisper {status?.whisper&&status?.speech_model?'● ready':'○ needs setup'}</span><span>FFmpeg {status?.ffmpeg?'● ready':'○ needs setup'}</span></div>
    <p className="muted">{hosted?'Selected frames and nearby narration are sent to ChatGPT through Codex. Full raw videos and audio stay on your Mac.':'Frame analysis and transcription run locally. Indexed text may later be included in prompts sent to your configured chat provider.'} Model outputs are estimates, not a recording of every click.</p>
    <div className="media-settings">
      <Field label="Frame analysis backend"><select value={config.vision_provider} onChange={e=>setChanges({...changes,vision_provider:e.target.value as 'local'|'codex_subscription'})}><option value="local">Local</option><option value="codex_subscription">ChatGPT subscription</option></select></Field>
      {hosted?<><Field label="ChatGPT vision model" hint="Models come from your Codex subscription connection. This selection only affects course video analysis and does not change your chat provider."><select value={config.codex_vision_model} onChange={e=>setChanges({...changes,codex_vision_model:e.target.value})}>{!hostedModels.some(model=>model.id===config.codex_vision_model)&&<option value={config.codex_vision_model}>{config.codex_vision_model} · saved selection</option>}{hostedModels.map(model=><option key={model.id} value={model.id}>{model.label||model.id}</option>)}</select></Field>
        <p role="status" className="muted">{modelsLoading?'Checking Codex connection and models…':models?.detail||(models?.ready?'Codex connection ready.':'Recheck your Codex subscription connection.')} Installed CLI and consent do not guarantee a working connection.</p>
        {modelsError&&<p role="alert" className="error-text">{modelsError}</p>}
        <label className="check-row"><input type="checkbox" checked={config.codex_vision_consent} onChange={e=>setChanges({...changes,codex_vision_consent:e.target.checked})}/> I consent to sending selected frames and nearby narration to ChatGPT through Codex for video analysis.</label>
      </>:<Field label="Vision model"><select value={config.vision_model} onChange={e=>setChanges({...changes,vision_model:e.target.value})}>{localModels.map(model=><option key={model} value={model}>{model==='qwen3-vl:2b'?'Qwen3-VL 2B · lighter for M1':model==='qwen3-vl:4b'?'Qwen3-VL 4B':model==='qwen3-vl:8b'?'Qwen3-VL 8B · higher memory':model}</option>)}</select></Field>}
      <Field label="Sample one frame every (seconds)" hint="12 = a screenshot every 12 seconds. Shorter intervals capture more UI changes but take longer."><input type="number" min={2} max={120} value={config.frame_interval} onChange={e=>setChanges({...changes,frame_interval:Number(e.target.value)})}/></Field>
      <label className="check-row"><input type="checkbox" checked={config.adaptive_frames} onChange={e=>setChanges({...changes,adaptive_frames:e.target.checked})}/> Also capture important screen changes (scene-detection pass)</label>
      <div className="media-frame-limit"><label className="check-row"><input type="checkbox" checked={config.max_frames===0} onChange={e=>setChanges({...changes,max_frames:e.target.checked?0:300})}/> Process entire video (no frame cap)</label>{config.max_frames>0 && <Field label="Maximum sampled frames" hint="Optional: the app spreads these samples across a long recording."><input type="number" min={1} max={1000000} value={config.max_frames} onChange={e=>setChanges({...changes,max_frames:Number(e.target.value)})}/></Field>}</div>
    </div>
    <div className="media-checks"><label><input type="checkbox" checked={config.enabled} onChange={e=>setChanges({...changes,enabled:e.target.checked})}/> Enable video enrichment</label><label><input type="checkbox" checked={config.vision_enabled} onChange={e=>setChanges({...changes,vision_enabled:e.target.checked})}/> Analyze sampled frames</label><label><input type="checkbox" checked={config.speech_enabled} onChange={e=>setChanges({...changes,speech_enabled:e.target.checked})}/> Transcribe locally with Whisper</label></div>
    <div className="button-group wrap"><Button disabled={!status||!!working||(hosted&&!config.codex_vision_consent)} onClick={()=>action('Saving','media.configure',config)}>Save video settings</Button>{!hosted&&<><Button disabled={!!working} onClick={()=>action('Starting Ollama','media.start')}>Start local model</Button><Button disabled={!!working} onClick={()=>action('Stopping Ollama','media.stop')}>Stop app-managed model</Button></>}</div>
    <details className="settings-more media-installer"><summary>Set up local AI tools</summary><div className="settings-more-body">
      <p>Installation downloads packages to your Mac using Homebrew and official model hosts. A component is installed only when you approve it here. Homebrew must already be installed for the binaries. Large Qwen model downloads can take significant time and disk space.</p>
      <label className="check-row"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I approve downloading and installing the component I select.</label>
      <div className="media-install-grid">{([['ollama','Install Ollama',status?.ollama_bin],['ffmpeg','Install FFmpeg',status?.ffmpeg],['whisper','Install whisper.cpp',status?.whisper],['speech','Download Whisper model',status?.speech_model],['vision','Download selected local model',(status?.installed_models||[]).includes(config.vision_model)]] as const).filter(([value])=>!hosted||!['ollama','vision'].includes(value)).map(([value,label,ready])=><div key={value} className="media-install-row"><span>{label.replace(/^Install |^Download /,'')}</span><Button disabled={!!ready||!approved||!!working||status?.setup?.state==='running'} onClick={()=>action(`Installing ${value}`,'media.setup',{target:value,approved:true})}>{ready?'Installed':label}</Button></div>)}</div>
      <p role="status" className="muted">Setup: {status?.setup?.state||'idle'} · {status?.setup?.detail||'No installation underway'}</p>
    </div></details>
    </>}
    {!setupMode && !status?.vision_ready && !status?.speech_model && <div className="info-note"><p>Video AI tools aren't ready yet. Existing text stays available. Install or check video tools in Settings.</p>{onOpenSettings&&<Button onClick={onOpenSettings}>Open Settings</Button>}</div>}
    {!setupMode && <div className="media-timeline"><div className="section-title"><div><h3>Video timeline</h3><p>Select a previously imported recording to see time-stamped speech and screen descriptions.</p></div></div>
      <Field label="Imported video / audio"><select value={selected} onChange={e=>{setSelected(e.target.value);setShowCount(100);}}><option value="">Select a recording</option>{videoSources.map(s=><option key={s.id} value={s.id}>{s.title||s.filename||s.id} · {s.status}</option>)}</select></Field>
      {selected&&<><div className="button-group wrap">{(['pause','resume','cancel'] as const).map(op=><Button key={op} disabled={!!working} onClick={()=>action(op,'media.control',{source_id:selected,operation:op})}>{op.charAt(0).toUpperCase()+op.slice(1)}</Button>)}<Button disabled={!!working} onClick={()=>action('Reprocessing','sources.retry',{id:selected})}>Reprocess using saved settings</Button></div>
      <SourceProgress status={videoSources.find(s=>s.id===selected)?.status} progress={videoSources.find(s=>s.id===selected)?.media_progress}/>
      <div className="media-entries">{timeline.length?timeline.slice(0,showCount).map((item,i)=><div className="media-entry" key={i}><div><strong>{stamp(item.start)}{item.end!=null?` – ${stamp(item.end)}`:''}</strong><small>{item.modality||'text'}</small></div><p>{item.text}</p></div>):<p className="muted">No timestamped transcript or visual descriptions yet. Import or reprocess this recording after setup.</p>}</div>{timeline.length>showCount && <Button onClick={()=>setShowCount(n=>n+100)}>Show 100 more ({timeline.length-showCount} remaining)</Button>}</>}
    </div>}
    {working&&<p role="status">{working}…</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </section>;
}
