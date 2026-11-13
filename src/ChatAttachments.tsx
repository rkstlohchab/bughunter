import {useEffect,useState} from 'react';
import {Image} from 'lucide-react';
import {rpc} from './api';
import type {RecordData as Row} from './api';
export default function ChatAttachments({provider,model,hasImages,value,onChange,onConfigure,onCapability}:{provider:string;model:string;hasImages:boolean;value:string;onChange:(mode:string)=>void;onConfigure:()=>void;onCapability:(canSee:boolean)=>void}){
 const [data,setData]=useState<Row|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let live=true;setData(null);setError('');onCapability(false);void rpc<Row>('chat.attachments.capability').then(row=>{if(live){setData(row);onCapability(!!row.selected_image_transport);}}).catch(error=>{if(live)setError(String(error));});return()=>{live=false;};},[provider,model]);
 if(!hasImages)return null;
 return <div className="chat-image-assistant"><Image size={16}/><div><label>Read images with<select aria-label="Image understanding" value={value} onChange={e=>onChange(e.target.value)}><option value="current" disabled={!data?.selected_image_transport}>Current chat model{data?.selected_image_transport?'':' · No verified image input'}</option><option value="configured">Vision assistant · {data?.vision_model||'Choose in Settings'}</option></select></label><p>{value==='current'?(data?.detail||'Checking selected model image support…'):`Images go to ${data?.vision_model||'your chosen local or cloud vision model'}. Its observations accompany the current chat model’s answer.`}</p>{(!data?.selected_image_transport||value==='configured')&&<button type="button" className="text-button" onClick={onConfigure}>Enable or choose local / cloud vision</button>}{error&&<p role="alert">{error}</p>}</div></div>;
}
