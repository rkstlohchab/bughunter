import {useEffect,useLayoutEffect,useState} from 'react';
import type {RefObject} from 'react';

export default function ChatPromptNavigator({prompts,stream,sessionId}:{prompts:{index:number;text:string}[];stream:RefObject<HTMLDivElement|null>;sessionId?:string}){
 const [active,setActive]=useState(0);
 const [position,setPosition]=useState({top:0,maxHeight:0});
 useLayoutEffect(()=>{
  const element=stream.current;if(!element)return;
  const update=()=>setPosition({top:element.offsetTop+element.clientHeight/2,maxHeight:Math.max(40,element.clientHeight-32)});
  const observer=new ResizeObserver(update);observer.observe(element);update();
  return()=>observer.disconnect();
 },[stream,sessionId]);
 useEffect(()=>{
  const element=stream.current;if(!element)return;
  function update(){
   const top=element!.getBoundingClientRect().top+50;
   const nodes=Array.from(element!.querySelectorAll<HTMLElement>('[data-prompt-index]'));
   let current=nodes[0];for(const node of nodes){if(node.getBoundingClientRect().top<=top)current=node;else break;}
   if(current)setActive(Number(current.dataset.promptIndex));
  }
  element.addEventListener('scroll',update,{passive:true});update();
  return()=>element.removeEventListener('scroll',update);
 },[stream,sessionId,prompts.length]);
 function jump(index:number){
  const element=stream.current;const prompt=element?.querySelector<HTMLElement>(`[data-prompt-index="${index}"]`);
  if(element&&prompt){const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;element.scrollTo({top:element.scrollTop+prompt.getBoundingClientRect().top-element.getBoundingClientRect().top-16,behavior:reduced?'instant':'smooth'});setActive(index);}
 }
 if(prompts.length<2)return null;
 return <nav className="chat-prompt-navigator" style={position} aria-label="Jump between your prompts">{prompts.map((prompt,position)=><button type="button" key={prompt.index} aria-label={`Go to prompt ${position+1}: ${prompt.text.slice(0,70)}`} title={prompt.text.slice(0,160)} aria-current={active===prompt.index?'step':undefined} onClick={()=>jump(prompt.index)} onKeyDown={event=>{
  if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();const next=prompts[Math.max(0,Math.min(prompts.length-1,position+(event.key==='ArrowUp'?-1:1)))];jump(next.index);const buttons=event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button');buttons?.[prompts.indexOf(next)]?.focus();}
 }}><span aria-hidden="true"/></button>)}</nav>;
}
