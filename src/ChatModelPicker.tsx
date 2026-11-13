import {useEffect,useLayoutEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import type {KeyboardEvent} from 'react';
import {ArrowLeft,Check,ChevronDown,ChevronRight,RefreshCw,Search,Sparkles} from 'lucide-react';
import {AI_CATEGORIES,categoryFor} from './aiProviders';
import type {Category} from './aiProviders';
import {effortSummary} from './ModelEffort';
import type {ModelOption} from './ModelEffort';

export type ProviderInfo={provider:string;label:string;kind:string;ready:boolean;needs:string;models:string[];model_options?:(ModelOption&{detail?:string})[];active:boolean};
type Props={providers:ProviderInfo[];active:{provider:string;model:string};compact?:boolean;floating?:boolean;disabled?:boolean;loading?:boolean;error?:string;label?:string;onPick:(provider:string,model:string)=>void;onConfigure:()=>void;onRefresh?:()=>void|Promise<void>};

export function modelDisplayName(id:string,label?:string){
  if(label&&label!==id)return label;
  return id.includes('/')?id.slice(id.lastIndexOf('/')+1).replace(/:latest$/,''):id;
}

export function compactModelName(id:string,label?:string){
  if(id.includes('/')||id.includes(':')){
    const base=id.slice(id.lastIndexOf('/')+1).replace(/:latest$/,'');
    const size=base.match(/(?:^|[-_:])(\d+(?:\.\d+)?b)(?=$|[-_:])/i)?.[1];
    const family=base.match(/omnicoder|qwen[\d.]*(?:-vl)?|llama[\d.]*/i)?.[0]||base.split(/[-_:]/)[0];
    if(size)return `${family.replace(/-/g,' ')} · ${size.toUpperCase()}`;
  }
  return modelDisplayName(id,label);
}

export default function ChatModelPicker({providers,active,onPick,onConfigure,onRefresh,compact=false,floating=false,disabled=false,loading=false,error='',label:accessibleLabel='Choose a model'}:Props){
  const [open,setOpen]=useState(false);const [category,setCategory]=useState<Category>('subscription');
  const [providerId,setProviderId]=useState('');const [query,setQuery]=useState('');
  const [refreshing,setRefreshing]=useState(false);const [refreshError,setRefreshError]=useState('');
  const root=useRef<HTMLDivElement>(null);const trigger=useRef<HTMLButtonElement>(null);const menuRoot=useRef<HTMLDivElement>(null);
  const providerMenu=useRef<HTMLDivElement>(null);const modelMenu=useRef<HTMLDivElement>(null);const categoryMenu=useRef<HTMLDivElement>(null);
  const focusModels=useRef(false);const menuId=useId();
  const [position,setPosition]=useState<{top?:number;bottom?:number;left:number;width:number;maxHeight:number}>({top:0,left:0,width:620,maxHeight:500});
  const current=providers.find(provider=>provider.provider===active.provider);
  const activeOption=current?.model_options?.find(model=>model.id===active.model);
  const displayName=compact?compactModelName(active.model,activeOption?.label):modelDisplayName(active.model,activeOption?.label);
  const needle=query.trim().toLowerCase();
  const matches=(provider:ProviderInfo,model:string)=>!needle||`${provider.label} ${model} ${provider.model_options?.find(option=>option.id===model)?.label||''}`.toLowerCase().includes(needle);
  const listed=providers.filter(provider=>needle||categoryFor(provider.provider)===category).map(provider=>({...provider,models:provider.models.filter(model=>matches(provider,model))})).filter(provider=>!needle||provider.models.length);
  const selected=listed.find(provider=>provider.provider===providerId)||listed.find(provider=>provider.ready)||listed[0];
  const categoryLabel=AI_CATEGORIES.find(item=>item.id===category)?.label||'';
  function close(){setOpen(false);setQuery('');trigger.current?.focus();}
  function chooseCategory(value:Category){setCategory(value);setProviderId('');setQuery('');}
  function chooseProvider(value:string,focus=false){focusModels.current=focus;setProviderId(value);if(focus&&value===selected?.provider)modelMenu.current?.querySelector<HTMLElement>('[role^="menuitem"]:not(:disabled)')?.focus();}
  function toggle(){
    if(open){close();return;}
    const initial=current?.ready?current:providers.find(provider=>provider.ready&&provider.models.length)||current;
    setCategory(categoryFor(initial?.provider||active.provider));setProviderId(initial?.provider||'');setQuery('');setOpen(true);
  }
  useEffect(()=>{if(open)categoryMenu.current?.querySelector<HTMLElement>('[aria-expanded="true"]')?.focus();},[open]);
  useEffect(()=>{if(open&&focusModels.current){modelMenu.current?.querySelector<HTMLElement>('[aria-checked="true"]:not(:disabled),[role^="menuitem"]:not(:disabled)')?.focus();focusModels.current=false;}},[selected?.provider,open]);
  useLayoutEffect(()=>{
    if(!open||!floating)return;
    const reposition=()=>{const rect=trigger.current?.getBoundingClientRect();if(!rect)return;
      const width=Math.min(innerWidth-24,innerWidth<820?360:620);const above=rect.top-24,below=innerHeight-rect.bottom-24,up=above>below;
      const maxHeight=Math.min(520,Math.max(200,above,below));
      setPosition({...(up?{bottom:innerHeight-rect.top+8}:{top:rect.bottom+8}),left:Math.max(12,Math.min(rect.left,innerWidth-width-12)),width,maxHeight});};
    reposition();window.addEventListener('resize',reposition);return()=>window.removeEventListener('resize',reposition);
  },[open,floating]);
  function keys(event:KeyboardEvent<HTMLDivElement>){
    const element=event.target as HTMLElement;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return;}
    if(element.tagName==='INPUT')return;
    if(event.key==='ArrowRight'&&categoryMenu.current?.contains(element)&&selected){event.preventDefault();chooseProvider(selected.provider,true);return;}
    if(event.key==='ArrowLeft'&&modelMenu.current?.contains(element)){event.preventDefault();providerMenu.current?.querySelector<HTMLElement>('[aria-expanded="true"]')?.focus();return;}
    if(event.key==='ArrowRight'&&providerMenu.current?.contains(element)){const id=element.dataset.provider;if(id){event.preventDefault();chooseProvider(id,true);}return;}
    const zone=modelMenu.current?.contains(element)?modelMenu.current:providerMenu.current?.contains(element)?providerMenu.current:categoryMenu.current;
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const items=Array.from(zone?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)')||[]);if(!items.length)return;
    event.preventDefault();const index=items.indexOf(element);
    const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next].focus();
  }
  async function refresh(){setRefreshing(true);setRefreshError('');try{await onRefresh?.();}catch{setRefreshError('Could not refresh models. Check the connection and try again.');}finally{setRefreshing(false);}}
  const menu=<><div className="model-picker-backdrop" style={floating?{zIndex:1499}:undefined} onMouseDown={event=>event.preventDefault()} onClick={close}/>
    <div ref={menuRoot} id={menuId} className="model-picker-menu provider-model-menu" style={floating?{position:'fixed',top:'auto',...position,height:position.maxHeight,zIndex:1500,maxWidth:'calc(100vw - 24px)'}:undefined} onKeyDown={keys}>
      <div className="model-catalog-toolbar"><label className="model-search"><Search size={15}/><input type="search" aria-label="Search all models" placeholder="Search models or providers…" value={query} onChange={event=>{setQuery(event.target.value);setProviderId('');}}/></label>{onRefresh&&<button type="button" className="model-refresh" aria-label="Refresh available models" aria-busy={refreshing} aria-disabled={refreshing} title="Reload models from connected providers" onClick={()=>{if(!refreshing)void refresh();}}><RefreshCw size={16} className={refreshing?'spin':''}/><span>{refreshing?'Refreshing…':'Refresh'}</span></button>}</div>
      {loading&&<p role="status" className="model-need">Loading provider models…</p>}{(refreshError||error)&&<p role="alert" className="model-need">{refreshError||error}</p>}
      <div ref={categoryMenu} role="menu" aria-label="AI connection types" className="provider-category-tabs">{AI_CATEGORIES.map(item=><button key={item.id} type="button" role="menuitem" data-category={item.id} aria-haspopup="menu" aria-expanded={!needle&&category===item.id} onMouseEnter={()=>{if(!needle&&category!==item.id)chooseCategory(item.id);}} onClick={()=>chooseCategory(item.id)}>{item.label}</button>)}</div>
      <div className="provider-model-panels">
        <div ref={providerMenu} role="menu" aria-label={needle?'Matching providers':`${categoryLabel} providers`} className="model-provider-list">
          {listed.map(provider=><button key={provider.provider} type="button" role="menuitem" aria-label={provider.label} aria-haspopup="menu" aria-expanded={selected?.provider===provider.provider} aria-controls={`${menuId}-models`} data-provider={provider.provider} className={`model-provider-item ${selected?.provider===provider.provider?'active':''}`} onMouseEnter={()=>chooseProvider(provider.provider)} onClick={()=>chooseProvider(provider.provider,true)}><span><strong>{provider.label}</strong><small>{provider.models.length} {needle?'matches':'models'} · {provider.ready?'Connected':'Setup needed'}</small></span><ChevronRight size={15}/></button>)}
          {!listed.length&&!loading&&<p className="model-need">{needle?'No matching models. Refresh the list or connect the provider in Settings.':'Connect a provider in Settings.'}</p>}
        </div>
        <div id={`${menuId}-models`} ref={modelMenu} role="menu" aria-label={selected?`${selected.label} models`:'Provider models'} className="model-provider-submenu">
          {selected&&<><div className="model-submenu-title"><ArrowLeft size={14} aria-hidden="true"/><strong>{selected.label}</strong><span>{selected.models.length}</span></div>
            <div className="model-group">{selected.models.map(model=>{const option=selected.model_options?.find(item=>item.id===model);const label=option?.label||model;const checked=selected.provider===active.provider&&model===active.model;
              return <button key={model} type="button" role="menuitemradio" aria-label={label} disabled={disabled||!selected.ready} aria-checked={checked} className={`model-opt ${checked?'on':''}`} title={model} onClick={()=>{onPick(selected.provider,model);close();}}><span><strong>{modelDisplayName(model,option?.label)}</strong>{option&&<small className="model-effort-summary">{effortSummary(option.effort)}{option.detail&&` · ${option.detail}`}</small>}</span>{checked&&<Check size={15}/>}</button>;})}</div>
            {selected.needs&&<p className="model-need">{selected.needs}</p>}{(!selected.ready||!selected.models.length)&&<button type="button" role="menuitem" className="model-opt" onClick={()=>{close();onConfigure();}}>Configure {selected.label}</button>}
          </>}
        </div>
      </div>
      <p className="model-catalog-note">Choose a provider to browse its models. Only that provider’s list opens.</p>
    </div></>;
  return <div className={`model-picker ${compact?'compact-model-picker':''}`} ref={root} onBlur={event=>{const next=event.relatedTarget as Node|null;
    // macOS WebKit blurs to null when a button is clicked without focusing it.
    // The backdrop handles pointer dismissal; only a real focus move closes here.
    if(next&&!event.currentTarget.contains(next)&&!menuRoot.current?.contains(next))setOpen(false);}}>
    <button ref={trigger} type="button" disabled={disabled} aria-busy={disabled||loading} className="model-picker-btn" aria-label={accessibleLabel} title={`${current?.label||'Model'} · ${active.model}`} aria-haspopup="menu" aria-expanded={open} aria-controls={open?menuId:undefined} onClick={toggle}><Sparkles size={16}/><span><strong>{current?.label||'Choose a model'}</strong><em>{displayName||'Choose a model'}</em></span><ChevronDown size={16}/></button>
    {open&&(floating?createPortal(menu,document.body):menu)}
  </div>;
}
