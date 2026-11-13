import {useCallback,useEffect,useRef,useState} from 'react';
import {asList,rpc} from './api';
import type {RecordData as Row} from './api';

// Source progress must not wait behind unrelated workspace requests. One request
// owns the next poll, so a slow response cannot overlap and replace newer state.
export function useLiveSources(){
 const [sources,setSources]=useState<Row[]>([]);
 const [error,setError]=useState('');
 const live=useRef(false);
 const inFlight=useRef<Promise<void>|null>(null);
 const requested=useRef(false);
 const refresh=useCallback(()=>{
  if(inFlight.current){requested.current=true;return inFlight.current;}
  const request=async()=>{
   do{
    requested.current=false;
    try{const rows=asList(await rpc('sources.list'));if(live.current){setSources(rows);setError('');}}
    catch(reason){if(live.current)setError(String(reason));}
   }while(requested.current&&live.current);
  };
  const pending=request().finally(()=>{if(inFlight.current===pending)inFlight.current=null;});
  inFlight.current=pending;return pending;
 },[]);
 useEffect(()=>{
  live.current=true;let disposed=false;let timer:ReturnType<typeof setTimeout>;
  async function poll(){if(document.visibilityState!=='hidden')await refresh();if(!disposed)timer=setTimeout(()=>void poll(),1000);}
  const changed=()=>{void refresh();};
  const visible=()=>{if(document.visibilityState!=='hidden')void refresh();};
  void poll();window.addEventListener('bughunter-sources-changed',changed);window.addEventListener('focus',visible);document.addEventListener('visibilitychange',visible);
  return()=>{disposed=true;live.current=false;clearTimeout(timer);window.removeEventListener('bughunter-sources-changed',changed);window.removeEventListener('focus',visible);document.removeEventListener('visibilitychange',visible);};
 },[refresh]);
 return {sources,error,refresh};
}
