import {useEffect,useState} from 'react';
import {rpc} from './api';
import {Button} from './components';
import ChatModelPicker from './ChatModelPicker';
import type {ProviderInfo} from './ChatModelPicker';
import ModelEffort from './ModelEffort';

export type AnalysisModel={provider:'claude_subscription'|'codex_subscription'|'gemini_subscription'|'gemini'|'openai'|'anthropic'|'local';model:string;effort?:string};
const chatProvider:Record<AnalysisModel['provider'],string>={claude_subscription:'claude_code',codex_subscription:'codex',gemini_subscription:'gemini_cli',gemini:'gemini',openai:'openai',anthropic:'anthropic',local:'ollama'};
const analysisProvider=Object.fromEntries(Object.entries(chatProvider).map(([media,chat])=>[chat,media])) as Record<string,AnalysisModel['provider']>;
const labels={claude_subscription:'Claude subscription',codex_subscription:'ChatGPT subscription',gemini_subscription:'Google AI subscription',gemini:'Gemini API',openai:'OpenAI API',anthropic:'Anthropic API',local:'Local AI'};
export function analysisModelLabel(value?:AnalysisModel|null){return value?`${labels[value.provider]} · ${value.model}`:'Use default';}
export function configAnalysisModel(config:Record<string,any>):AnalysisModel{
  const provider=(config.vision_provider||'local') as AnalysisModel['provider'];
  const key=provider==='local'?'vision_model':provider==='codex_subscription'?'codex_vision_model':provider==='claude_subscription'?'claude_vision_model':provider==='gemini_subscription'?'gemini_vision_model':'api_vision_model';
  return {provider,model:config[key]||'',effort:config.vision_effort??'medium'};
}

export default function ResourceModelPicker({value,onChange,disabled=false,allowDefault=true,onConfigure}:{value:AnalysisModel|null;onChange:(value:AnalysisModel|null)=>void;disabled?:boolean;allowDefault?:boolean;onConfigure?:()=>void}){
  const [providers,setProviders]=useState<ProviderInfo[]>([]);
  const [defaults,setDefaults]=useState<AnalysisModel|null>(null);
  const [error,setError]=useState('');const [loading,setLoading]=useState(true);const [reload,setReload]=useState(0);const [connectionHint,setConnectionHint]=useState(false);
  useEffect(()=>{
    let current=true;setLoading(true);setError('');
    Promise.allSettled([rpc<{providers:ProviderInfo[]}>('models.analysis',{refresh:reload>0}),rpc<{config:Record<string,any>}>('media.status')]).then(([catalog,status])=>{
      if(!current)return;
      if(catalog.status==='fulfilled'&&Array.isArray(catalog.value.providers))setProviders(catalog.value.providers);
      else setError(catalog.status==='rejected'?String(catalog.reason):'The model catalog is unavailable. Reopen the updated application.');
      if(status.status==='fulfilled'&&status.value.config)setDefaults(configAnalysisModel(status.value.config));
      setLoading(false);
    });return()=>{current=false;};
  },[reload]);
  const selected=value||defaults;
  const active={provider:selected?chatProvider[selected.provider]:'',model:selected?.model||''};
  const option=providers.find(provider=>provider.provider===active.provider)?.model_options?.find(model=>model.id===active.model);
  function pick(provider:string,model:string){
    const media=analysisProvider[provider];if(!media)return;
    const effort=providers.find(item=>item.provider===provider)?.model_options?.find(item=>item.id===model)?.effort;
    onChange({provider:media,model,...(media!=='local'&&!effort?.levels.includes('medium')?{effort:''}:{})});setConnectionHint(false);
  }
  return <div className="resource-model-picker">
    <div className="analysis-picker-controls"><ChatModelPicker providers={providers} active={active} floating onPick={pick} disabled={disabled||loading} label="Choose analysis model" onConfigure={()=>{setConnectionHint(true);onConfigure?.();}}/>
      {value&&<ModelEffort info={option?.effort} value={value.effort??(option?.effort?.levels.includes('medium')?'medium':'')} disabled={disabled||loading} onChange={effort=>onChange({...value,effort})}/>}
      <Button disabled={disabled||loading} onClick={()=>setReload(version=>version+1)}>Refresh models</Button>
    </div>
    {allowDefault&&<label className="check-row"><input type="checkbox" checked={!value} disabled={disabled} onChange={event=>onChange(event.target.checked?null:defaults)}/> Follow the default in Video AI setup</label>}
    {loading&&<p role="status" className="muted">Loading versioned models and supported effort levels…</p>}
    {error&&<p role="alert" className="error-text">{error}</p>}
    {connectionHint&&<p role="status">Connect this provider in Settings → Connect your AI, then refresh the model list here.</p>}
    <p className="muted">{selected?.provider==='local'?'Images are analyzed on this Mac.':`Selected text and sampled frames are shared with ${selected?labels[selected.provider]:'the default connection'}.`}{selected&&['gemini','openai','anthropic'].includes(selected.provider)&&' API quota and billing are separate from subscriptions.'} Audio is transcribed locally with Whisper. Completed citations stay intact when you switch.</p>
  </div>;
}

export function ResourceModelEditor({sourceId,value,effective,onSaved,disabled=false}:{sourceId:string;value:AnalysisModel|null;effective?:AnalysisModel;onSaved:()=>void;disabled?:boolean}){
  const [choice,setChoice]=useState(value);const [saving,setSaving]=useState(false);const [message,setMessage]=useState('');const [error,setError]=useState('');
  const savedSignature=JSON.stringify(value);const changed=JSON.stringify(choice)!==savedSignature;
  useEffect(()=>{setChoice(value);},[sourceId,savedSignature]);
  async function save(){setSaving(true);setMessage('');setError('');try{await rpc('sources.models',{ids:[sourceId],analysis_model:choice});setMessage('Saved. Remaining frames and future study use this model; completed citations are preserved.');onSaved();}catch(reason){setError(String(reason));}finally{setSaving(false);}}
  return <section className="surface resource-model-editor"><h3>AI for this resource</h3><p className="muted">Current choice: {analysisModelLabel(value)}{!value&&effective?` · ${analysisModelLabel(effective)}`:''}</p>
    <ResourceModelPicker value={choice} onChange={next=>{setChoice(next);setMessage('');}} disabled={disabled||saving}/>
    {changed&&<p role="status">Selected: {analysisModelLabel(choice)}. Save to apply this choice to remaining analysis.</p>}
    <Button disabled={disabled||saving||!!choice&&!choice.model} onClick={save}>{saving?'Saving model…':'Save resource model'}</Button>{message&&<p role="status">{message}</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </section>;
}
