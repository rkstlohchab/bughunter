import {useEffect,useRef,useState} from 'react';
import {Mic,MicOff} from 'lucide-react';
import {rpc} from './api';
import type {RecordData as Row} from './api';
import {DictationDraft} from './VoiceTranscript';
export default function VoiceInput({disabled,value,onChange,onSend,onSetup}:{disabled:boolean;value:string;onChange:(text:string)=>void;onSend:(text:string)=>void;onSetup:()=>void}){
 const [listening,setListening]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const [modelLabel,setModelLabel]=useState('');const [feedback,setFeedback]=useState('');
 const current=useRef({value,onChange,onSend});current.current={value,onChange,onSend};
 const draft=useRef(new DictationDraft(value));const lastRendered=useRef(value);
 useEffect(()=>{if(value!==lastRendered.current){draft.current.edit(value);lastRendered.current=value;}},[value]);
 const resources=useRef<{stream:MediaStream;context:AudioContext;node:AudioWorkletNode;id:string}|null>(null);const starting=useRef(false);const version=useRef(0);const lastSeq=useRef(0);
 function accept(events:Row[],flushing=false){for(const event of events){if(event.seq<=lastSeq.current)continue;lastSeq.current=event.seq;
  if(event.kind==='error'){setError(event.error);void stop();return;}
  if(event.kind==='partial'||event.kind==='final'){
   const update=draft.current.accept(String(event.line_id),String(event.text||''),event.kind==='final');
   if(!update.changed)continue;lastRendered.current=update.text;current.current.onChange(update.text);
   if(update.send&&!flushing){if(update.text.trim()){setError('');setFeedback('Send command received.');current.current.onSend(update.text);}else setError('Dictate a message before saying “send”.');}else if(event.kind==='partial')setFeedback('');
  }
 }}
 async function stop(){version.current++;starting.current=false;const active=resources.current;resources.current=null;setListening(false);setBusy(false);if(active){active.node.port.onmessage=null;active.node.disconnect();active.stream.getTracks().forEach(track=>track.stop());await active.context.close().catch(()=>{});try{const result=await rpc<Row>('voice.stop',{id:active.id});accept(result.events||[],true);}catch{}}}
 useEffect(()=>{const halt=()=>{void stop();};window.addEventListener('bughunter-stop-all',halt);window.addEventListener('bughunter-stop-listening',halt);return()=>{window.removeEventListener('bughunter-stop-all',halt);window.removeEventListener('bughunter-stop-listening',halt);void stop();};},[]);
 useEffect(()=>{if(disabled)void stop();},[disabled]);
 async function start(){if(starting.current||resources.current)return;starting.current=true;setBusy(true);setError('');const request=++version.current;let stream:MediaStream|undefined;let context:AudioContext|undefined;let id='';
  try{
   context=new AudioContext();const resumed=context.resume();void resumed.catch(()=>{});
   const status=await rpc<Row>('voice.status');if(!status.models?.some((m:Row)=>m.id===status.selected&&m.ready)){setError('Set up local speech in Settings → Voice.');await context.close();onSetup();return;}
   stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
   if(request!==version.current){stream.getTracks().forEach(track=>track.stop());await context.close();return;}
   await context.audioWorklet.addModule('/voice-worklet.js');await resumed;const session=await rpc<Row>('voice.start');id=session.id;
   if(request!==version.current){await rpc('voice.stop',{id});stream.getTracks().forEach(track=>track.stop());await context.close();return;}
   setModelLabel(session.model_label||status.models.find((m:Row)=>m.id===status.selected)?.label||'');setFeedback('');lastSeq.current=0;draft.current=new DictationDraft(current.current.value);lastRendered.current=current.current.value;const node=new AudioWorkletNode(context,'local-voice-capture');const source=context.createMediaStreamSource(stream);const silent=context.createGain();silent.gain.value=0;source.connect(node);node.connect(silent);silent.connect(context.destination);resources.current={stream,context,node,id};let chain=Promise.resolve();let queued=0;
   node.port.onmessage=event=>{if(resources.current?.id!==id)return;queued++;if(queued>12){setError('Local transcription cannot keep up. Try Tiny or close another local model.');void stop();return;}chain=chain.then(async()=>{if(resources.current?.id!==id)return;try{const bytes=new Uint8Array(event.data);let binary='';for(const b of bytes)binary+=String.fromCharCode(b);const result=await rpc<Row>('voice.feed',{id,pcm:btoa(binary),after:lastSeq.current});if(resources.current?.id===id)accept(result.events||[]);}catch(error){setError(String(error));void stop();}finally{queued--;}});};
   setListening(true);
  }catch(error){stream?.getTracks().forEach(track=>track.stop());await context?.close().catch(()=>{});if(id)await rpc('voice.stop',{id}).catch(()=>{});setError(error instanceof Error?error.message:String(error));}
  finally{starting.current=false;setBusy(false);}
 }
 return <div className="voice-input"><button type="button" className={`chat-tool-btn ${listening?'voice-live':''}`} aria-label={listening?'Turn microphone off':'Voice input'} aria-pressed={listening} disabled={disabled||busy} onClick={()=>void(listening?stop():start())}>{listening?<MicOff size={18}/>:<Mic size={18}/>}</button>{(listening||busy||error)&&<div className="voice-status" role="status"><div><strong>{busy?'Starting microphone…':listening?'Listening locally':'Voice input'}</strong>{listening&&<button type="button" className="text-button" onClick={()=>void stop()}>Stop listening</button>}</div>{error&&<p role="alert">{error}</p>}{feedback&&<p>{feedback}</p>}{listening&&<><small>{modelLabel}</small><small>Speak naturally. Say “send it” or “send the message”, then pause to submit. Audio stays on this device.</small></>}</div>}</div>;
}
