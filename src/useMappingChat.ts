import { useEffect, useRef, useState } from 'react';
import { rpc } from './api';
import { ceMcpApi, isDesktop } from './ceMcpApi';
import type { MappingProgram, MappingCheckReply } from './ceMcpApi';

export type MappingChatResult = {
  session: any;
  workflow: {id:string;chat_id:string;status:string;progress:string;query:string;round:number;context:MappingProgram|null;check_id?:string|null;requires_selection?:boolean;events:{at:string;text:string}[]};
  research?: {id:string;references:any[];candidates:any[];verified_count:number;warnings:string[];events:{at:string;text:string}[]} | null;
  next?: {kind:'check';check_id:string;request:Record<string,unknown>} | null;
};
export const mappingActive = (status?:string) => !!status && ['needs_program','researching','needs_value','checking','blocked'].includes(status);
const checking = new Map<string,Promise<MappingCheckReply>>();
function checkOnce(id:string) {
  let task=checking.get(id);
  if (!task) { task=ceMcpApi.checkMappingAutomatic(id); checking.set(id,task); }
  return task.finally(()=>checking.delete(id));
}

export function useMappingChat(session:any, updateSession:(session:any)=>void) {
  const [data,setData]=useState<MappingChatResult|null>(null);
  const [programs,setPrograms]=useState<MappingProgram[]>([]);
  const [attached,setAttached]=useState<MappingProgram|null>(null);
  const [approval,setApproval]=useState<MappingCheckReply|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [connecting,setConnecting]=useState('');
  const actionError=useRef(false);
  const [wake,setWake]=useState(0);
  const version=useRef(0);
  const prepared=useRef('');
  const update=useRef(updateSession); update.current=updateSession;
  const sid=session?.id;
  const mappingId=session?.mapping_id;
  const current=useRef({sid,mappingId}); current.current={sid,mappingId};
  const activeOperation=useRef(false);
  const accept=(result:MappingChatResult) => {
    setData(result); update.current(result.session);
    setApproval(previous=>result.workflow.status==='checking' && previous?.check_id===result.workflow.check_id?previous:null);
  };

  useEffect(()=>{
    version.current++;
    prepared.current=''; actionError.current=false;
    setData(null); setPrograms([]); setAttached(null); setApproval(null); setError(''); setBusy(false); setConnecting('');
  },[sid,mappingId]);

  useEffect(()=>{
    if (!sid || !mappingId) return;
    let alive=true;
    let timer:ReturnType<typeof setTimeout>;
    const generation=version.current;
    const valid=()=>alive && generation===version.current && current.current.sid===sid;
    const poll=async()=>{
      try {
        const result=await rpc<MappingChatResult>('chat.mapping.advance',{id:sid});
        if (!valid()) return;
        accept(result);
        if (!actionError.current) setError('');
        if (result.workflow.status==='needs_program') {
          if (prepared.current!==mappingId) {
            prepared.current=mappingId;
            if (!isDesktop()) {
              const paused=await rpc<MappingChatResult>('chat.mapping.pause',{id:sid,reason:'Program checks require the desktop app. Reopen this conversation there and reply “retry”.'});
              if (valid()) accept(paused);
              return;
            }
            try {
              const preparedResult=await ceMcpApi.prepareMappingChat();
              if (!valid()) return;
              setPrograms(preparedResult.processes); setAttached(preparedResult.context);
              if (preparedResult.context && !result.workflow.requires_selection && /\b(?:(?:selected|attached|current|this) (?:program|process))\b/i.test(result.workflow.query)) {
                const selected=await rpc<MappingChatResult>('chat.mapping.select',{id:sid,context:preparedResult.context});
                if (!valid()) return;
                accept(selected); timer=setTimeout(()=>void poll(),200); return;
              }
            } catch(e) {
              if (valid()) setError(e instanceof Error?e.message:String(e));
            }
          }
          return;
        }
        if (result.next?.kind==='check') {
          if (!isDesktop()) {
            const paused=await rpc<MappingChatResult>('chat.mapping.pause',{id:sid,reason:'The desktop app is needed to continue program checks.'});
            if (valid()) accept(paused);
            return;
          }
          try {
            const outcome=await checkOnce(result.next.check_id);
            if (!valid()) return;
            if (outcome.approval_required) {setApproval(outcome);return;}
          } catch(e) {
            if (!valid()) return;
            const paused=await rpc<MappingChatResult>('chat.mapping.pause',{id:sid,reason:e instanceof Error?e.message:String(e)});
            if (valid()) accept(paused);
            return;
          }
        }
        if (['researching','checking'].includes(result.workflow.status)) timer=setTimeout(()=>void poll(),result.next?100:1000);
      } catch(e) {
        if (valid()) {setError(String(e));timer=setTimeout(()=>void poll(),2000);}
      }
    };
    void poll();
    return ()=>{alive=false;clearTimeout(timer);};
  },[sid,mappingId,wake]);

  async function action(work:()=>Promise<MappingChatResult>) {
    if (activeOperation.current) return false;
    const scope=current.current.sid;
    const generation=++version.current;
    activeOperation.current=true;actionError.current=false;setBusy(true);setError('');
    try {
      const result=await work();
      if (scope===current.current.sid && generation===version.current) {accept(result);return true;}
      return false;
    } catch(e) {if (scope===current.current.sid && generation===version.current) {
      actionError.current=true;setError(e instanceof Error?e.message:String(e));
    }return false;}
    finally {activeOperation.current=false;setBusy(false);setWake(v=>v+1);}
  }
  async function reply(message:string, source_ids:string[]=[]) {
    prepared.current='';
    return action(()=>rpc<MappingChatResult>('chat.mapping.reply',{id:sid,message,source_ids}));
  }
  async function select(program:MappingProgram, alreadyAttached=false) {
    await action(async()=>{
      setConnecting(program.program);
      try {
        let context:MappingProgram;
        try { context=alreadyAttached?program:await ceMcpApi.selectMappingProgram(program); }
        catch(e) {
          const detail=e instanceof Error?e.message:String(e);
          const denied=/task_for_pid|KERN_(?:FAILURE|PROTECTION_FAILURE)|operation not permitted/i.test(detail);
          const reason=denied
            ? `macOS refused memory access to ${program.program} (PID ${program.pid}). No mapping checks ran. The process may be protected or the helper may lack debugger access. Choose another program or resolve debugger access before retrying. Details: ${detail}`
            : `Could not connect to ${program.program} (PID ${program.pid}). No mapping checks ran. ${detail}`;
          // Save the failure in the conversation; a status refresh must not erase it.
          try { return await rpc<MappingChatResult>('chat.mapping.pause',{id:sid,reason}); }
          catch { throw new Error(reason+' The failure could not be saved; retry when the app connection is available.'); }
        }
        return await rpc<MappingChatResult>('chat.mapping.select',{id:sid,context});
      } finally { if (current.current.sid===sid) setConnecting(''); }
    });
  }
  const refreshPrograms=()=>{prepared.current='';actionError.current=false;setError('');setWake(v=>v+1);};
  const stop=()=>action(async()=>{
    const result=await rpc<MappingChatResult>('chat.mapping.stop',{id:sid}); setApproval(null); return result;
  });
  const approve=()=>action(async()=>{
    if (!approval) throw new Error('The check is no longer pending.');
    await ceMcpApi.checkMapping(approval.check_id); setApproval(null);
    return rpc<MappingChatResult>('chat.mapping.advance',{id:sid});
  });
  const exportEvidence=async()=>{
    if (!data?.research) return;
    try {
      const result=await rpc<{filename:string;content:string}>('memory.mappings.export',{id:data.research.id});
      const url=URL.createObjectURL(new Blob([result.content],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download=result.filename;link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) {setError(String(e));}
  };
  return {data,programs,attached,approval,error,busy,connecting,reply,select,refreshPrograms,stop,approve,exportEvidence};
}
