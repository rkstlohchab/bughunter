import { useEffect, useState } from 'react';
import { Activity, Plus, ShieldAlert, Trash2 } from 'lucide-react';
import { rpc } from './api';
import { Button, Field } from './components';

type Route = { id:string;provider:string;model:string;base_url:string;enabled:boolean;pricing_confirmed:boolean;api_budget_usd:number;input_price_per_million:number;output_price_per_million:number };
const newRoute = ():Route => ({id:'backup-'+Date.now(),provider:'openai_compatible',model:'bughunter-moe',base_url:'',enabled:true,pricing_confirmed:false,api_budget_usd:0,input_price_per_million:0,output_price_per_million:0});
const paid = (provider:string) => !['openai_compatible','ollama','remote_ollama','crucible'].includes(provider);
export default function ModelRoutesPanel() {
  const [enabled,setEnabled] = useState(false);
  const [routes,setRoutes] = useState<Route[]>([]);
  const [keys,setKeys] = useState<Record<string,boolean>>({});
  const [draftKeys,setDraftKeys] = useState<Record<string,string>>({});
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState('');
  const reload = async() => {const result=await rpc<any>('models.routes');setRoutes(result.routes||[]);setEnabled(!!result.enabled);setKeys(result.saved_keys||{});};
  useEffect(()=>{void reload().catch(e=>setNotice(String(e)));},[]);
  function update(id:string,values:Partial<Route>) {setRoutes(rows=>rows.map(row=>row.id===id?{...row,...values}:row));}
  async function save() {setBusy(true);try{const saved=await rpc<any>('models.routes.save',{enabled,routes});setRoutes(saved.routes);setKeys(saved.saved_keys);setNotice('Fallback configuration saved. Chat will only use it when you explicitly opt in.');}catch(e){setNotice(String(e));}finally{setBusy(false);}}
  async function saveKey(id:string) {setBusy(true);try{await rpc('models.routes.secret',{id,value:draftKeys[id]});setKeys({...keys,[id]:true});setDraftKeys({...draftKeys,[id]:''});setNotice('Key saved in system keychain.');}catch(e){setNotice(String(e));}finally{setBusy(false);}}
  async function remove(id:string){setBusy(true);try{const result=await rpc<any>('models.routes.remove',{id});setRoutes(result.routes);setKeys(result.saved_keys);setNotice('Removed fallback route and its saved key.');}catch(e){setNotice(String(e));}finally{setBusy(false);}}
  return <section className="surface settings-card" aria-label="Model routing and failover">
    <div className="section-title"><div><h2>Backup AI models</h2><p>If your primary model goes offline, optionally try a backup model. Never switches on authentication errors.</p></div><Activity size={19}/></div>
    <div className="settings-card-body model-routing-panel">
      <label className="check-row"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Enable configured fallback routes</label>
      <p className="muted"><ShieldAlert size={15} style={{verticalAlign:'middle'}}/> Your full conversation and attached excerpts may be sent to the backup provider. Opt in per chat before switching. Never add an endpoint you don't trust.</p>
      {routes.map(row=><div key={row.id} className="model-route-card">
        <div className="model-route-top"><strong>{row.id}</strong><Button variant="danger-quiet" icon={Trash2} onClick={()=>void remove(row.id)} disabled={busy}>Remove</Button></div>
        <div className="route-form-grid"><Field label="Provider"><select value={row.provider} onChange={e=>update(row.id,{provider:e.target.value})}><option value="openai_compatible">Kaggle / custom OpenAI-compatible</option><option value="ollama">Local Ollama</option><option value="remote_ollama">Remote Ollama</option><option value="openai">OpenAI API</option><option value="anthropic">Anthropic API</option><option value="deepseek">DeepSeek API</option><option value="crucible">Crucible</option></select></Field>
          <Field label="Model"><input value={row.model} onChange={e=>update(row.id,{model:e.target.value})}/></Field>
          <Field label="Server base URL"><input type="url" value={row.base_url} onChange={e=>update(row.id,{base_url:e.target.value})} placeholder="https://your-server.example/v1"/></Field>
          <label className="check-row"><input type="checkbox" checked={row.enabled} onChange={e=>update(row.id,{enabled:e.target.checked})}/>Use this backup</label></div>
        {paid(row.provider)&&<div className="route-form-grid"><label className="check-row"><input type="checkbox" checked={row.pricing_confirmed} onChange={e=>update(row.id,{pricing_confirmed:e.target.checked})}/>I confirmed this API's pricing</label><Field label="Max cost per reply, USD"><input type="number" min="0" step="0.1" value={row.api_budget_usd} onChange={e=>update(row.id,{api_budget_usd:Number(e.target.value)})}/></Field><Field label="USD / million input tokens"><input type="number" min="0" step="0.01" value={row.input_price_per_million} onChange={e=>update(row.id,{input_price_per_million:Number(e.target.value)})}/></Field><Field label="USD / million output tokens"><input type="number" min="0" step="0.01" value={row.output_price_per_million} onChange={e=>update(row.id,{output_price_per_million:Number(e.target.value)})}/></Field></div>}
        {!['ollama','crucible'].includes(row.provider)&&<div className="route-secret"><Field label={`Backup key ${keys[row.id]?'— saved':''}`}><input type="password" autoComplete="off" value={draftKeys[row.id]||''} onChange={e=>setDraftKeys({...draftKeys,[row.id]:e.target.value})} placeholder={keys[row.id]?'Saved in keychain — enter only to replace':'Save route first, then enter key'}/></Field><Button disabled={!draftKeys[row.id]||busy} onClick={()=>void saveKey(row.id)}>Save key</Button></div>}
      </div>)}
      <div className="form-actions"><Button icon={Plus} disabled={routes.length>=5||busy} onClick={()=>setRoutes(rows=>[...rows,newRoute()])}>Add backup model</Button><Button variant="primary" disabled={busy} onClick={()=>void save()}>Save backup models</Button></div>
      {notice&&<p role="status" className="muted">{notice}</p>}
    </div>
  </section>;
}
