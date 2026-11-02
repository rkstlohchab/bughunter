import { useEffect, useState } from 'react';
import { rpc } from './api';
import { Button, Field } from './components';
import { Clapperboard, RefreshCw } from 'lucide-react';

type MediaStatus = {
  platform?: string; config: {enabled:boolean;vision_model:string;frame_interval:number;max_frames:number;adaptive_frames:boolean;scene_threshold:number;vision_enabled:boolean;speech_enabled:boolean};
  ollama_bin:boolean;ollama_running:boolean;installed_models:string[];vision_ready:boolean;
  ffmpeg:boolean;whisper:boolean;speech_model:boolean;
  setup:{state:string;detail:string;target:string|null};
};
type Source = {id:string;title?:string;filename?:string;media_type?:string;detected_format?:string;status?:string;media_progress?:{analyzed:number;total:number;timestamp:number}};
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
  const setupMode = mode === 'settings';
  const videoSources=sources.filter(s=>/video|audio|mp4|mov|mkv|m4a|webm|wav|mp3/i.test(`${s.media_type||''} ${s.detected_format||''} ${s.filename||''}`));
  function refresh(){rpc<MediaStatus>('media.status').then(setStatus).catch(e=>setError(String(e)));}
  useEffect(()=>{refresh();const id=setInterval(refresh,3500);return ()=>clearInterval(id);},[]);
  useEffect(()=>{if(!selected){setTimeline([]);return;}let active=true;
    const reload=()=>rpc<{items:Clip[]}>('media.timeline',{source_id:selected}).then(r=>{if(active)setTimeline(r.items||[]);}).catch(e=>{if(active)setError(String(e));});
    reload();const id=setInterval(reload,4000);return ()=>{active=false;clearInterval(id);};},[selected]);
  async function action(label:string,method:string,p:any={}){setWorking(label);setError('');try{if(method==='media.setup' && p.target==='vision') await rpc('media.configure',config);await rpc(method,p);refresh();}catch(e){setError(String(e));}finally{setWorking('');}}
  const config={...(status?.config||{enabled:true,vision_model:'qwen3-vl:2b',frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.30,vision_enabled:true,speech_enabled:true}),...changes};
  return <section className="surface media-ai"><div className="section-title"><div><h2><Clapperboard size={19}/> {setupMode?'Video AI setup':'Video timeline'}</h2><p>{setupMode?'Install and configure local video analysis tools here.':'Review what the AI heard and saw at each point in a recording.'}</p></div>{setupMode&&<Button onClick={refresh} icon={RefreshCw}>Recheck</Button>}</div>
    {setupMode && <><div className="media-metrics"><span>Ollama {status?.ollama_running?'● running':'○ stopped'}</span><span>Qwen3-VL {status?.vision_ready?'● ready':'○ needs setup'}</span><span>Whisper {status?.whisper&&status?.speech_model?'● ready':'○ needs setup'}</span><span>FFmpeg {status?.ffmpeg?'● ready':'○ needs setup'}</span></div>
    <p className="muted">Local processing keeps raw video, images and audio on your Mac; only indexed text may be included in prompts sent to the configured Kaggle AI. Model outputs are estimates, not a recording of every click.</p>
    <div className="media-settings">
      <Field label="Vision model"><select value={config.vision_model} onChange={e=>setChanges({...changes,vision_model:e.target.value})}><option value="qwen3-vl:2b">Qwen3-VL 2B · lighter for M1</option><option value="qwen3-vl:4b">Qwen3-VL 4B</option><option value="qwen3-vl:8b">Qwen3-VL 8B · higher memory</option></select></Field>
      <Field label="Sample one frame every (seconds)" hint="12 = a screenshot every 12 seconds. Shorter intervals capture more UI changes but take longer."><input type="number" min={2} max={120} value={config.frame_interval} onChange={e=>setChanges({...changes,frame_interval:Number(e.target.value)})}/></Field>
      <label className="check-row"><input type="checkbox" checked={config.adaptive_frames} onChange={e=>setChanges({...changes,adaptive_frames:e.target.checked})}/> Also capture important screen changes (scene-detection pass)</label>
      <div className="media-frame-limit"><label className="check-row"><input type="checkbox" checked={config.max_frames===0} onChange={e=>setChanges({...changes,max_frames:e.target.checked?0:300})}/> Process entire video (no frame cap)</label>{config.max_frames>0 && <Field label="Maximum sampled frames" hint="Optional: the app spreads these samples across a long recording."><input type="number" min={1} max={1000000} value={config.max_frames} onChange={e=>setChanges({...changes,max_frames:Number(e.target.value)})}/></Field>}</div>
    </div>
    <div className="media-checks"><label><input type="checkbox" checked={config.enabled} onChange={e=>setChanges({...changes,enabled:e.target.checked})}/> Enable Mac-local video enrichment</label><label><input type="checkbox" checked={config.vision_enabled} onChange={e=>setChanges({...changes,vision_enabled:e.target.checked})}/> Analyze frames with Qwen3-VL</label><label><input type="checkbox" checked={config.speech_enabled} onChange={e=>setChanges({...changes,speech_enabled:e.target.checked})}/> Transcribe with Whisper</label></div>
    <div className="button-group wrap"><Button disabled={!!working} onClick={()=>action('Saving','media.configure',config)}>Save video settings</Button><Button disabled={!!working} onClick={()=>action('Starting Ollama','media.start')}>Start local model</Button><Button disabled={!!working} onClick={()=>action('Stopping Ollama','media.stop')}>Stop app-managed model</Button></div>
    <details className="settings-more media-installer"><summary>Set up local AI tools</summary><div className="settings-more-body">
      <p>Installation downloads packages to your Mac using Homebrew and official model hosts. A component is installed only when you approve it here. Homebrew must already be installed for the binaries. Large Qwen model downloads can take significant time and disk space.</p>
      <label className="check-row"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I approve downloading and installing the component I select.</label>
      <div className="media-install-grid">{([['ollama','Install Ollama',status?.ollama_bin],['ffmpeg','Install FFmpeg',status?.ffmpeg],['whisper','Install whisper.cpp',status?.whisper],['speech','Download Whisper model',status?.speech_model],['vision','Download selected Qwen3-VL',status?.vision_ready]] as const).map(([value,label,ready])=><div key={value} className="media-install-row"><span>{label.replace(/^Install |^Download /,'')}</span><Button disabled={!!ready||!approved||!!working||status?.setup?.state==='running'} onClick={()=>action(`Installing ${value}`,'media.setup',{target:value,approved:true})}>{ready?'Installed':label}</Button></div>)}</div>
      <p role="status" className="muted">Setup: {status?.setup?.state||'idle'} · {status?.setup?.detail||'No installation underway'}</p>
    </div></details>
    </>}
    {!setupMode && !status?.vision_ready && !status?.speech_model && <div className="info-note"><p>Video AI tools aren't ready yet. Existing text stays available. Install or check video tools in Settings.</p>{onOpenSettings&&<Button onClick={onOpenSettings}>Open Settings</Button>}</div>}
    {!setupMode && <div className="media-timeline"><div className="section-title"><div><h3>Video timeline</h3><p>Select a previously imported recording to see time-stamped speech and screen descriptions.</p></div></div>
      <Field label="Imported video / audio"><select value={selected} onChange={e=>{setSelected(e.target.value);setShowCount(100);}}><option value="">Select a recording</option>{videoSources.map(s=><option key={s.id} value={s.id}>{s.title||s.filename||s.id} · {s.status}</option>)}</select></Field>
      {selected&&<><div className="button-group wrap">{(['pause','resume','cancel'] as const).map(op=><Button key={op} disabled={!!working} onClick={()=>action(op,'media.control',{source_id:selected,operation:op})}>{op.charAt(0).toUpperCase()+op.slice(1)}</Button>)}<Button disabled={!!working} onClick={()=>action('Reprocessing','sources.retry',{id:selected})}>Reprocess using saved settings</Button></div>
      {videoSources.find(s=>s.id===selected)?.media_progress&&<p className="muted">Frames: {videoSources.find(s=>s.id===selected)?.media_progress?.analyzed} / {videoSources.find(s=>s.id===selected)?.media_progress?.total}</p>}
      <div className="media-entries">{timeline.length?timeline.slice(0,showCount).map((item,i)=><div className="media-entry" key={i}><div><strong>{stamp(item.start)}{item.end!=null?` – ${stamp(item.end)}`:''}</strong><small>{item.modality||'text'}</small></div><p>{item.text}</p></div>):<p className="muted">No timestamped transcript or visual descriptions yet. Import or reprocess this recording after setup.</p>}</div>{timeline.length>showCount && <Button onClick={()=>setShowCount(n=>n+100)}>Show 100 more ({timeline.length-showCount} remaining)</Button>}</>}
    </div>}
    {working&&<p role="status">{working}…</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </section>;
}
