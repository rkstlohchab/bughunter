import {useEffect,useLayoutEffect,useRef,useState,useId} from 'react';
import {createPortal} from 'react-dom';
import type {ReactNode} from 'react';
import {ChevronDown,RotateCcw,Zap} from 'lucide-react';
export type EffortInfo={levels:string[];default?:string|null;source?:string;detail?:string};
export type ModelOption={id:string;label?:string;default?:boolean;effort?:EffortInfo};
const labels:Record<string,string>={none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra high',max:'Max',ultra:'Ultra'};
export function effortSummary(info?:EffortInfo){return info?.levels?.length?`Effort: ${info.levels.map(level=>labels[level]||level).join(' · ')}`:'Effort: provider-managed (no levels reported)';}

type Props={info?:EffortInfo;value?:string;onChange:(effort:string)=>void;disabled?:boolean;variant?:'inline'|'card'|'compact';children?:ReactNode};

function CompactEffort({info,value,onChange,disabled=false,children}:Props){
  const [open,setOpen]=useState(false);const trigger=useRef<HTMLButtonElement>(null);const panel=useRef<HTMLDivElement>(null);const id=useId();
  const [position,setPosition]=useState({left:0,top:0,width:320});const supported=!!info?.levels.length;
  const level=value||info?.default||(info?.levels.includes('medium')?'medium':info?.levels[0])||'';
  const label=supported?labels[level]||level:'Auto';
  function close(){setOpen(false);trigger.current?.focus();}
  useLayoutEffect(()=>{if(!open)return;const place=()=>{const rect=trigger.current?.getBoundingClientRect();if(!rect)return;const width=Math.min(320,innerWidth-24),height=panel.current?.offsetHeight||180;
    setPosition({width,left:Math.max(12,Math.min(rect.left,innerWidth-width-12)),top:Math.max(12,rect.top>height+20?rect.top-height-8:Math.min(rect.bottom+8,innerHeight-height-12))});};place();window.addEventListener('resize',place);return()=>window.removeEventListener('resize',place);},[open,supported]);
  useEffect(()=>{if(open)panel.current?.querySelector<HTMLElement>('select,button')?.focus();},[open]);
  useEffect(()=>{if(!open)return;const escape=(event:globalThis.KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setOpen(false);trigger.current?.focus();}};window.addEventListener('keydown',escape,true);return()=>window.removeEventListener('keydown',escape,true);},[open]);
  return <div className="compact-model-controls">{children}<button ref={trigger} type="button" className="compact-effort-button" disabled={disabled} aria-label={`Effort: ${supported?label:'Automatic'}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open?id:undefined} title={supported?'Adjust reasoning effort':'This connection does not expose adjustable effort'} onClick={()=>setOpen(!open)}><Zap size={13}/><span><span className="compact-effort-prefix">Effort: </span>{label}</span><ChevronDown size={12}/></button>
    {open&&createPortal(<><div className="effort-popover-backdrop" onMouseDown={event=>event.preventDefault()} onClick={close}/><div id={id} ref={panel} role="dialog" aria-label="Choose effort" className="effort-popover" style={position} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}}}>
      {supported?<ModelEffort info={info} value={value} onChange={onChange} disabled={disabled} variant="card"/>:<div className="effort-auto-detail"><strong>Effort is automatic</strong><p>This connection does not expose adjustable effort levels.</p><button type="button" onClick={close}>Got it</button></div>}
    </div></>,document.body)}
  </div>;
}

export default function ModelEffort({info,value,onChange,disabled=false,variant='inline',children}:Props){
  const levels=info?.levels||[];const signature=levels.join('|');const id=useId();
  const effective=value||info?.default||(levels.includes('medium')?'medium':levels[0])||'';
  const [draft,setDraft]=useState(effective);const [dirty,setDirty]=useState(false);const dragging=useRef(false);
  useEffect(()=>{if(!dragging.current){setDraft(effective);setDirty(false);}},[effective,signature]);
  if(variant==='compact')return <CompactEffort info={info} value={value} onChange={onChange} disabled={disabled}>{children}</CompactEffort>;
  if(!levels.length)return variant==='card'?<div className="model-effort-card effort-card-managed">
    <div className="effort-card-heading"><Zap size={18} aria-hidden="true"/><span>Effort <strong>Automatic</strong></span></div>
    {children&&<div className="effort-card-model">{children}</div>}
    <p className="model-effort-note" title={info?.detail}>This model has no effort setting. Choose a ChatGPT or Claude model to adjust effort.</p>
  </div>:<span className="model-effort-note" title={info?.detail}>Effort is automatic for this model.</span>;
  function commit(index:number){const level=levels[index];dragging.current=false;setDirty(false);if(level)onChange(level);}
  const index=Math.max(0,levels.indexOf(draft));const text=value||dirty?labels[draft]||draft:info?.default?`Default (${labels[info.default]||info.default})`:'Provider default';
  const reset=()=>{dragging.current=false;setDraft(info?.default||(levels.includes('medium')?'medium':levels[0]));setDirty(false);onChange('');};
  const slider=<input id={id} type="range" aria-label="Reasoning effort" aria-valuetext={text} aria-describedby={`${id}-levels`} min={0} max={levels.length-1} step={1} value={index} disabled={disabled} title={info?.detail}
    onPointerDown={()=>{dragging.current=true;}} onChange={event=>{setDraft(levels[Number(event.target.value)]);setDirty(true);}}
    onPointerUp={event=>commit(Number(event.currentTarget.value))}
    onPointerCancel={()=>{dragging.current=false;setDirty(false);setDraft(effective);}}
    onKeyUp={event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key))commit(Number(event.currentTarget.value));}}
    onBlur={event=>{if(dirty)commit(Number(event.currentTarget.value));}}/>;
  const levelDescription=<span id={`${id}-levels`} className="sr-only">Available effort levels: {levels.map(level=>labels[level]||level).join(', ')}. Use arrow keys to change effort.</span>;
  if(variant==='card')return <div className="model-effort-card" aria-label="Model reasoning effort">
    <div className="effort-card-heading"><Zap size={18} aria-hidden="true"/><label htmlFor={`${id}-select`}>Effort</label>
      <select id={`${id}-select`} aria-label="Effort level" disabled={disabled} value={value||''} onChange={event=>{setDirty(false);setDraft(event.target.value||info?.default||effective);onChange(event.target.value);}}>
        <option value="">{info?.default?`Default (${labels[info.default]||info.default})`:'Default'}</option>{levels.map(level=><option key={level} value={level}>{labels[level]||level}</option>)}
      </select>
      <button type="button" className="effort-card-reset" aria-label="Use provider default effort" aria-pressed={!value} title="Reset to provider default" disabled={disabled} onClick={reset}><RotateCcw size={18}/></button>
    </div>
    {children&&<div className="effort-card-model">{children}</div>}
    <div className="effort-card-track"><div className="effort-card-rail" aria-hidden="true"><div className="effort-card-fill" style={{width:`${index/Math.max(1,levels.length-1)*100}%`}}/><div className="effort-card-dots">{levels.map(level=><span key={level} title={labels[level]||level}/>)}</div></div>{slider}</div>
    {levelDescription}
    <div className="effort-card-scale" aria-hidden="true"><span>{labels[levels[0]]||levels[0]}</span><span>{labels[levels[levels.length-1]]||levels[levels.length-1]}</span></div>
  </div>;
  return <div className="model-effort-slider" aria-label="Model reasoning effort">
    <div className="effort-slider-caption"><label htmlFor={id}>Effort <strong>{text}</strong></label><button type="button" aria-label="Use provider default effort" aria-pressed={!value} disabled={disabled} onClick={reset}>Default</button></div>
    {slider}{levelDescription}
    <div className="effort-slider-levels">{levels.map((level,i)=><button key={level} type="button" disabled={disabled} aria-label={`Set effort to ${labels[level]||level}`} aria-pressed={!!value&&value===level} className={i===index?'selected':''} onClick={()=>{setDraft(level);setDirty(false);onChange(level);}}>{labels[level]||level}</button>)}</div>
  </div>;
}
