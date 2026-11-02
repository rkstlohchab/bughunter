import { ResearchSourceSettings } from './ResearchP2';
import MacToolControl from './MacToolControl';
import GuidedToolInstall from './GuidedToolInstall';
import MacAccessPanel from './MacAccessPanel';
import MediaAI from './MediaAI';
import MemoryToolsSetup from './MemoryToolsSetup';
import ComputerSandboxPanel from './ComputerSandboxPanel';
import ModelRoutesPanel from './ModelRoutesPanel';
import { useEffect, useState } from 'react';
import { Activity, CheckCircle2, KeyRound, Monitor, Moon, Network, ShieldCheck, Sun, Wrench } from 'lucide-react';
import { rpc, pretty } from './api';
import type { RecordData as Row } from './api';
import { Button, Field, Modal, Status, Code } from './components';

type Execute = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
type Category = 'subscription' | 'api' | 'selfhosted';
const PROVIDERS: Record<Category, { id: string; name: string; detail: string }[]> = {
  subscription: [{ id: 'claude_code', name: 'Claude Code', detail: 'Existing subscription' }],
  api: [{ id: 'anthropic', name: 'Anthropic', detail: 'API billing' }, { id: 'openai', name: 'OpenAI', detail: 'API billing' }, { id: 'deepseek', name: 'DeepSeek', detail: 'API billing' }],
  selfhosted: [{ id: 'openai_compatible', name: 'Kaggle / custom AI', detail: 'llama.cpp or OpenAI-compatible server' }, { id: 'ollama', name: 'Ollama on this Mac', detail: 'Local model' }, { id: 'remote_ollama', name: 'Ollama on another computer', detail: 'Remote Ollama API' }, { id: 'crucible', name: 'CrucibleLLM', detail: 'Model on your device' }],
};
const categoryFor = (id: string): Category => (Object.keys(PROVIDERS) as Category[]).find(cat => PROVIDERS[cat].some(p => p.id === id)) || 'selfhosted';
const needsKey = (id: string) => !['claude_code','ollama','crucible'].includes(id);
const usesBaseUrl = (id: string) => id !== 'claude_code';
const defaultModel: Record<string,string> = {claude_code:'opus',ollama:'qwen3:8b',remote_ollama:'qwen3:8b',openai_compatible:'bughunter-moe',openai:'gpt-4.1-mini',anthropic:'claude-sonnet-4-5',deepseek:'deepseek-chat',crucible:'local'};

export default function SettingsView({perform,busy,revision,projectId,system,onSystemChange}: {perform:Execute;busy:string;revision:number;projectId:string;system:Row;onSystemChange:(value:Row)=>void}) {
  const [settings, setSettings] = useState<Row>({provider:'openai_compatible',model:'bughunter-moe'});
  const [category, setCategory] = useState<Category>('selfhosted');
  const [initialized, setInitialized] = useState(false);
  const [secret, setSecret] = useState('');
  const [stored, setStored] = useState<Record<string,boolean>>({});
  const [probe, setProbe] = useState<Row|null>(null);
  const [tools,setTools] = useState<Row[]>([]);
  const [dependencies,setDependencies] = useState<Row[]>([]);
  const [showToolConnections,setShowToolConnections] = useState(false);
  const [wizard,setWizard] = useState<string|null>(null);
  const [result,setResult] = useState<Row|null>(null);
  const [toolInput,setToolInput] = useState('');
  const [proxy,setProxy] = useState('http://127.0.0.1:8080');
  const [ca,setCa] = useState('');
  const [advanced,setAdvanced] = useState(false);
  const [checkingTools,setCheckingTools]=useState(false);
  const [locatingTool,setLocatingTool]=useState('');
  const [manualToolPaths,setManualToolPaths]=useState<Record<string,string>>({});
  const [toolMessage,setToolMessage]=useState('');
  const [lastToolCheck,setLastToolCheck]=useState<number|null>(null);
  const [lastBackup,setLastBackup] = useState<string>('');
  const [appearance,setAppearance] = useState<'light'|'dark'>(() => localStorage.getItem('bughunter.theme') === 'light' ? 'light' : 'dark');
  const setTheme = (mode:'light'|'dark') => { setAppearance(mode); localStorage.setItem('bughunter.theme',mode); document.documentElement.dataset.theme=mode; window.dispatchEvent(new CustomEvent('bughunter-theme-change',{detail:mode})); };
  const provider = settings.provider || 'claude_code';
  const selected = PROVIDERS[category].find(p=>p.id===provider);
  const hasKey = !!stored[provider];
  const needsPrice = category === 'api';
  async function recheckTools() {
    setCheckingTools(true);setToolMessage('');
    try {
      const [rows, listing] = await Promise.all([rpc<Row[]>('system.dependencies'),rpc<Row>('tools.status')]);
      if (!Array.isArray(rows)) throw new Error('The backend did not return a valid tool list.');
      setDependencies(rows);
      setTools(Array.isArray(listing) ? listing : Array.isArray(listing?.tools) ? listing.tools :
        Object.entries(listing || {}).map(([name,v]) => ({name,...(typeof v==='object' && v !== null ? v as Row : {available:!!v,detail:pretty(v)})})));
      setLastToolCheck(Date.now());
      setToolMessage(`Checked ${rows.length} integrations just now. “Found” only means installed, not connected.`);
    } catch (e) {setToolMessage(`Check failed: ${e instanceof Error ? e.message : String(e)}`);}
    finally {setCheckingTools(false);}
  }
  async function locateTool(name: string, useDialog = false) {
    if (useDialog) {
      if (!(window as any).__TAURI_INTERNALS__) {setToolMessage('Choose the application from the installed Mac desktop app.');return;}
      try {
        const dialog = await import('@tauri-apps/plugin-dialog');
        const selected = await dialog.open({multiple:false, directory:false, title:`Locate ${name} application or executable`});
        if (typeof selected !== 'string') return;
        setManualToolPaths(previous=>({...previous,[name]:selected}));
        await saveToolPath(name,selected);
      } catch(e) {setToolMessage(`Could not select a file: ${String(e)}`);}
    } else {
      const path = manualToolPaths[name];
      if (!path?.trim()) {setToolMessage(`Enter the path to ${name} first.`);return;}
      await saveToolPath(name,path);
    }
  }
  async function saveToolPath(name:string,path:string) {
    setLocatingTool(name);setToolMessage('');
    try {
      await rpc('system.tools.locate',{name,path});
      await recheckTools();
      setToolMessage(`${name} location verified. This does not mean the tool is running or connected.`);
    } catch(e) {setToolMessage(`Cannot verify ${name}: ${String(e)}`);}
    finally {setLocatingTool('');}
  }
  useEffect(() => { void recheckTools(); },[revision]);
  useEffect(() => {
    Promise.allSettled([rpc<Row>('settings.get'), rpc<Row>('settings.credentials'), rpc<Row>('tools.status')]).then(results => {
      const value = results[0].status==='fulfilled' ? results[0].value : null;
      const creds = results[1].status==='fulfilled' ? results[1].value : {};
      const listing = results[2].status==='fulfilled' ? results[2].value : [];
      if (value && !initialized) {
        const legacy = value.provider_system_prompts?.[value.provider] || '';
        setSettings({...value,custom_system_prompt:value.custom_system_prompt || legacy});
        setCategory(categoryFor(value.provider)); setProxy(value.burp_proxy_url || 'http://127.0.0.1:8080'); setCa(value.burp_ca || ''); setInitialized(true);
      }
      setStored(creds || {});
      setTools(Array.isArray(listing) ? listing : Array.isArray(listing?.tools) ? listing.tools : Object.entries(listing || {}).map(([name,v]) => ({name,...(typeof v==='object' && v !== null ? v as Row : {available:!!v,detail:pretty(v)})})));
    }).catch(() => {});
  },[revision,initialized]);
  function selectCategory(next:Category) {
    setCategory(next);
    if (!PROVIDERS[next].some(p=>p.id===settings.provider)) {
      const id = PROVIDERS[next][0].id;
      setSettings(prev => ({...prev,provider:id,model:defaultModel[id],api_base_url:'',local_only:id==='ollama'||id==='crucible',pricing_confirmed:false,input_price_per_million:0,output_price_per_million:0}));
    }
    setSecret(''); setProbe(null);
  }
  function selectProvider(id:string) {
    setSettings(prev => ({...prev,provider:id,model:prev.provider===id ? prev.model : defaultModel[id],api_base_url:prev.provider===id?prev.api_base_url:'',local_only:id==='ollama'||id==='crucible',pricing_confirmed:prev.provider===id ? !!prev.pricing_confirmed : false,
      input_price_per_million:prev.provider===id?prev.input_price_per_million:0,
      output_price_per_million:prev.provider===id?prev.output_price_per_million:0}));
    setSecret('');setProbe(null);
  }
  function payload(): Row { return {...settings, provider_system_prompts: {},pricing_confirmed:!needsPrice || !!settings.pricing_confirmed, api_budget_usd:needsPrice ? settings.api_budget_usd ?? 25 : 0, input_price_per_million:needsPrice ? settings.input_price_per_million ?? 0 : 0,output_price_per_million:needsPrice ? settings.output_price_per_million ?? 0 : 0}; }
  async function save(test=false) {
    const value = await perform(test?'Testing AI connection':'Saving AI settings',async()=> { await rpc('settings.save',payload());return test ? await rpc<Row>('settings.test'): {ok:true};},test?undefined:'AI settings saved.');
    if (test && value) setProbe(value);
  }
  const connections = [
    {id:'builtin',name:'Built-in web testing',description:'Start a verified, policy-controlled scanner without installing Burp.'},
    {id:'burp',name:'Burp Suite',description:'Optional for inspecting requests through Burp Proxy.'},
    {id:'browser',name:'Managed browser',description:'Available for websites in your current web-testing project.'},
    {id:'nuclei',name:'Nuclei / host discovery',description:'Optional command-line scanning tools for scoped checks.'},
  ];
  async function runTool(action:string) {
    if (!projectId && !['builtin','gateway','verify'].includes(action)) {setResult({error:'Create or select a project from the top bar first.'});return;}
    const operations:Record<string,()=>Promise<any>> = {
      gateway:()=>rpc('tools.gateway_start',{port:Number(settings.gateway_port||8091)}),
      builtin:async()=>{await rpc('tools.gateway_start',{port:Number(settings.gateway_port||8091)});return rpc('tools.gateway_verify_direct');},
      verify:()=>rpc('tools.gateway_verify',{burp_proxy:proxy,burp_ca:ca}),
      browser:()=>rpc('tools.browser',{project_id:projectId,url:toolInput}),
      nuclei:()=>rpc('tools.nuclei',{project_id:projectId,urls:[toolInput]}),
      discover:()=>rpc('tools.discover',{project_id:projectId,domain:toolInput}),
    };
    const value = await perform('Checking tool connection',operations[action]);
    if (value) setResult(value);
  }
  return <div className="settings-clean">
    <section className="surface settings-card">
      <div className="section-title"><div><h2>Choose your AI</h2><p>Pick how your model runs. Only paid APIs need token prices or budgets.</p></div><Network size={20}/></div>
      <div className="settings-card-body">
        <div className="provider-categories" role="group" aria-label="AI connection type">
          <button className={category==='subscription'?'active':''} onClick={()=>selectCategory('subscription')}>Subscription</button>
          <button className={category==='api'?'active':''} onClick={()=>selectCategory('api')}>Paid APIs</button>
          <button className={category==='selfhosted'?'active':''} onClick={()=>selectCategory('selfhosted')}>My own models</button>
        </div>
        <div className="provider-cards" role="group" aria-label="Provider">
          {PROVIDERS[category].map(item=><button type="button" key={item.id} className={`provider-card ${provider===item.id?'active':''}`} aria-pressed={provider===item.id} onClick={()=>selectProvider(item.id)}><strong>{item.name}</strong><small>{item.detail}</small></button>)}
        </div>
        <form onSubmit={e=>{e.preventDefault();void save(false);}}>
          {selected && <p className="muted settings-mode-note">Selected: {selected.name}</p>}
          <Field label="Model name"><input value={settings.model||''} onChange={e=>setSettings({...settings,model:e.target.value})} placeholder="Model identifier" required/></Field>
          {usesBaseUrl(provider) && <Field label="Server address" hint={provider==='openai_compatible'?'For Kaggle, paste the ngrok URL ending in /v1.':'Leave empty to use the provider default, if supported.'}><input type="text" value={settings.api_base_url||''} onChange={e=>setSettings({...settings,api_base_url:e.target.value})} placeholder={provider==='openai_compatible'?'https://your-tunnel.ngrok-free.app/v1':provider==='ollama'?'http://127.0.0.1:11434':'Provider URL'}/></Field>}
          {needsKey(provider) && <div className="credential-box"><div className="credential-status"><KeyRound size={17}/><strong>Connection key</strong>{hasKey?<span className="saved-pill"><CheckCircle2 size={14}/> Saved in keychain</span>:<span className="muted">No saved key</span>}</div><Field label={hasKey?'Replace saved key (optional)':'API key'} hint="Your key is never displayed again. The saved indicator will remain visible after you store it."><input type="password" autoComplete="off" value={secret} onChange={e=>setSecret(e.target.value)} placeholder={hasKey?'•••••••••• (saved — enter only to replace)':'Paste your key'}/></Field><Button disabled={!secret.trim()||!!busy} onClick={async()=>{const saved=await perform('Saving credential',()=>rpc('settings.secret',{provider,value:secret}),'API key saved securely.');if(saved){setSecret('');setStored(old=>({...old,[provider]:true}));}}}>{hasKey?'Update key':'Save key'}</Button></div>}
          {needsPrice && <details className="settings-more"><summary>API prices and spending limit <span>Required for paid providers</span></summary><div className="settings-more-body"><Field label="Maximum API spend per run (USD)"><input type="number" step="any" min="0" value={settings.api_budget_usd??25} onChange={e=>setSettings({...settings,api_budget_usd:Number(e.target.value)})}/></Field><div className="form-grid"><Field label="Input USD / million tokens"><input type="number" step="any" min="0" value={settings.input_price_per_million??0} onChange={e=>setSettings({...settings,input_price_per_million:Number(e.target.value),pricing_confirmed:false})}/></Field><Field label="Output USD / million tokens"><input type="number" step="any" min="0" value={settings.output_price_per_million??0} onChange={e=>setSettings({...settings,output_price_per_million:Number(e.target.value),pricing_confirmed:false})}/></Field></div><label className="check-row"><input type="checkbox" checked={!!settings.pricing_confirmed} onChange={e=>setSettings({...settings,pricing_confirmed:e.target.checked})}/>I have checked these prices.</label></div></details>}
          <Field label="Your AI instructions" hint="Use your own code, imported knowledge, and preferred response style across Chat and supported AI providers. Custom prompts are forwarded as instructions; the provider can still enforce its own rules."><textarea rows={5} maxLength={8000} value={settings.custom_system_prompt||''} onChange={e=>setSettings({...settings,custom_system_prompt:e.target.value})} placeholder="Use my project code and indexed knowledge first. Explain solutions with source references and clear steps."/></Field>
          <div className="form-actions"><Button type="submit" variant="primary" disabled={!!busy}>Save AI settings</Button><Button type="button" icon={Activity} disabled={!!busy} onClick={()=>void save(true)}>Save & check connection</Button></div>
          {probe && <p role="status" className={`connection-result ${probe.ok?'ok':'bad'}`}>{probe.detail||'Connection check finished.'}</p>}
        </form>
      </div>
    </section>
    <section className="surface settings-card tool-health-summary" aria-label="Tool installation status"><div className="section-title"><div><h2>Optional tools</h2><p>Detected on this Mac, not necessarily connected.</p></div><Wrench size={20}/></div><div className="settings-card-body">
      <div className="tool-health-pills">{dependencies.length ? dependencies.map(t=><span key={t.name} className={t.installed?'agents-ready':'agents-missing'} title={String(t.location||t.detail||'')}>{t.name}: {t.installed?'Found':'Not found'}</span>) : <span className="muted">No tool inventory available.</span>}</div>
      <div className="tool-access-list">{dependencies.filter(t=>t.name==='Cheat Engine'||t.name==='Burp Suite').map(t=><div key={t.name} className="tool-access-row"><div><strong>{t.name}</strong><p className="muted">{String(t.detail||'Not detected')}</p>{t.location && <small className="muted">{String(t.location)}</small>}
        <div className="tool-manual-path"><input aria-label={`Path to ${t.name}`} placeholder="/Applications/App.app or /path/to/program.exe" value={manualToolPaths[t.name]||''} onChange={e=>setManualToolPaths(old=>({...old,[t.name]:e.target.value}))}/>
        <Button disabled={!!locatingTool} onClick={()=>void locateTool(t.name,false)}>Use path</Button><Button disabled={!!locatingTool} onClick={()=>void locateTool(t.name,true)}>Browse</Button></div><MacToolControl tool={t as any} refreshKey={lastToolCheck} proxyUrl={proxy}/></div></div>)}</div>
      <div className="form-actions"><Button type="button" onClick={()=>setShowToolConnections(v=>!v)}>{showToolConnections?'Hide optional tools':'Manage optional tools'}</Button><Button type="button" disabled={checkingTools} onClick={()=>void recheckTools()}>{checkingTools?'Checking…':'Recheck now'}</Button></div>
      {toolMessage && <p className="muted" role="status">{toolMessage}</p>}
      {lastToolCheck && <small className="muted">Last checked: {new Date(lastToolCheck).toLocaleTimeString()}</small>}
    </div></section>
    <MacAccessPanel/>
    <MemoryToolsSetup/>
    <GuidedToolInstall/>
    <ResearchSourceSettings/>
    <MediaAI sources={[]} mode="settings"/>
    {showToolConnections && <section className="settings-optional" aria-label="Optional tool connections"><div className="settings-optional-inner"><p className="muted">Use these only when an AI model or specialist tool needs manual setup. Everyday tasks begin in Chat.</p>
    <ModelRoutesPanel/>
    <ComputerSandboxPanel/>
    <ResearchSettings/>
    <details className="surface settings-card settings-technical" open={advanced} onToggle={e=>setAdvanced(e.currentTarget.open)}><summary>Advanced model options</summary><div className="settings-card-body"><p className="muted">Knowledge search works without Ollama. Local semantic embeddings and transcription are independent of your reasoning provider.</p><label className="check-row"><input type="checkbox" checked={!!settings.use_local_embeddings} onChange={e=>setSettings({...settings,use_local_embeddings:e.target.checked})}/>Enable optional local semantic search</label>{settings.use_local_embeddings && <><Field label="Local embedding model"><input value={settings.embedding_model||''} onChange={e=>setSettings({...settings,embedding_model:e.target.value})}/></Field><Field label="Embedding server"><input value={settings.embedding_base_url||''} onChange={e=>setSettings({...settings,embedding_base_url:e.target.value})}/></Field></>}
      <Field label="Local speech transcription model"><input value={settings.whisper_model_path||''} onChange={e=>setSettings({...settings,whisper_model_path:e.target.value})} placeholder="Optional whisper.cpp model path"/></Field><label className="check-row"><input type="checkbox" checked={settings.auto_resume_after_execute!==false} onChange={e=>setSettings({...settings,auto_resume_after_execute:e.target.checked})}/>Continue an agent run after approved actions complete</label><Button onClick={()=>void save(false)}>Save optional settings</Button></div></details>
    <section className="surface settings-card"><div className="section-title"><div><h2>Connect tools</h2><p>Optional. Open a guide when you want to use a particular tool.</p></div><Wrench size={20}/></div><div className="tool-cards">{connections.map(item=><div className="tool-card" key={item.id}><div><strong>{item.name}</strong><p>{item.description}</p></div><Button onClick={()=>{setWizard(item.id);setResult(null);}}>Setup / connect</Button></div>)}</div></section>
    </div></section>}
    <section className="surface settings-card appearance-card" aria-label="Appearance settings"><div className="section-title"><div><h2>Appearance</h2><p>Choose the theme you prefer. Your selection is remembered on this device.</p></div><Monitor size={20}/></div><div className="appearance-options" role="group" aria-label="Appearance"><button className={appearance==='dark'?'active':''} aria-pressed={appearance==='dark'} onClick={()=>setTheme('dark')}><Moon size={18}/> Dark mode</button><button className={appearance==='light'?'active':''} aria-pressed={appearance==='light'} onClick={()=>setTheme('light')}><Sun size={18}/> Light mode</button></div></section>
    <section className="surface settings-card"><div className="section-title"><div><h2>Back up your work</h2><p>Save your projects, conversations, research files, and task history before changing versions.</p></div><ShieldCheck size={20}/></div><div className="settings-card-body"><p className="muted">Creates a private ZIP on this Mac. It is not encrypted; keep it in a trusted location. API keys stored in your macOS keychain are not included.</p><Button disabled={!!busy} onClick={async()=>{const result=await perform('Creating backup',()=>rpc<Row>('system.backup.create'),'Backup created.');if(result?.path)setLastBackup(result.path);}}>Create backup</Button>{lastBackup && <p role="status" className="muted">Saved to: <code className="backup-path">{lastBackup}</code></p>}<p className="muted">For recovery, close the app and run <code>python3 scripts/data_backup.py restore --archive /path/to/backup.zip --destination /path/to/new-data</code>.</p></div></section>

    <details className="surface settings-card settings-technical"><summary>Task controls</summary><div className="settings-card-body"><p className="muted">Stopping tasks persists across app restarts, so it does not accidentally resume sending requests. Use Resume when you want to work again.</p><p role="status">Current state: <strong>{system.emergency_stop?'Requests paused':'Requests allowed'}</strong></p><div className="button-group wrap">{system.emergency_stop ? <Button disabled={!!busy} onClick={async()=>{const v=await perform('Resuming tasks',()=>rpc<Row>('system.resume'),'New requests enabled.');if(v)onSystemChange({...system,emergency_stop:false});}}>Resume tasks</Button> : <Button disabled={!!busy} onClick={async()=>{const v=await perform('Pausing tasks',()=>rpc<Row>('system.stop'),'New requests stopped.');if(v)onSystemChange({...system,emergency_stop:true});}}>Stop all tasks</Button>}</div></div></details>
    {wizard && <Modal title={connections.find(t=>t.id===wizard)?.name+' setup'} wide onClose={()=>setWizard(null)}><div className="setup-wizard">
      {wizard==='builtin' ? <><p>Start the built-in local policy gateway, then verify both HTTP and HTTPS routes automatically. No separate Burp Suite installation is required. Only your explicitly approved safe GET paths can be crawled.</p><div className="button-group wrap"><Button variant="primary" disabled={!!busy} onClick={()=>runTool('builtin')}>Connect built-in web testing</Button></div><p className="muted">The connection proof expires after five minutes. Reconnect before a new run if needed. This does not change system-wide proxy or trust settings.</p></> : wizard==='burp' ? <><p>Use Burp Suite's proxy listener for your authorized web project. Burp is optional unless you want to intercept and inspect traffic through it.</p><ol><li>Start Burp Suite and turn on its proxy listener.</li><li>For authenticated HTTP history, build the included Java extension using <code>bash scripts/build_burp_bridge.sh</code> and load <code>integrations/burp/build/BugHunterBurpBridge.jar</code> under Burp Extensions. Configure the website target in Burp scope.</li><li>Export the Burp CA certificate as PEM if interception requires it.</li><li>Select your project in the app header, then verify this route.</li></ol><Field label="Burp proxy URL"><input value={proxy} onChange={e=>setProxy(e.target.value)}/></Field><Field label="Burp CA PEM path (optional)"><input value={ca} onChange={e=>setCa(e.target.value)} placeholder="/Users/you/Downloads/cacert.pem"/></Field><div className="button-group wrap"><Button onClick={async()=>{const v=await perform('Saving Burp connection',()=>rpc('settings.save',{...payload(),burp_proxy_url:proxy,burp_ca:ca}));if(v)setSettings(v);}}>Save connection</Button><Button onClick={()=>runTool('gateway')}>Start gateway</Button><Button variant="primary" onClick={()=>runTool('verify')}>Verify connection</Button></div></> : wizard==='browser' ? <><p>The managed browser is a built-in feature. You do not need a separate browser account. Open a URL within the selected project's allowed scope.</p><Field label="URL within project"><input type="url" placeholder="https://your-authorized-site.example" value={toolInput} onChange={e=>setToolInput(e.target.value)}/></Field><Button variant="primary" disabled={!toolInput||!!busy} onClick={()=>runTool('browser')}>Open browser</Button></> : <><p>Nuclei and subdomain discovery are optional integrations. They are used only when the tools are installed and an authorized web project is selected.</p><p className="muted">Install the relevant CLI on your Mac, then check the Tools status in this app. The app doesn't need Nuclei for ordinary chat, knowledge import or local analysis.</p><Field label="In-scope URL or domain"><input value={toolInput} onChange={e=>setToolInput(e.target.value)} placeholder="https://your-authorized-site.example"/></Field><div className="button-group wrap"><Button onClick={()=>runTool('discover')} disabled={!toolInput||!!busy}>Discover hosts</Button><Button onClick={()=>runTool('nuclei')} disabled={!toolInput||!!busy}>Run reviewed checks</Button></div></>}
      <div className="connection-result">{projectId ? 'Active project selected' : 'Select a project from the app header before using a target-dependent tool.'}</div>
      {tools.filter(t=>(t.name||'').toLowerCase().includes(wizard)).map((tool,i)=><div key={i}><strong>{tool.name}</strong> <Status value={tool.available||tool.installed?'available':'unavailable'}/></div>)}
      {result && <Code label="Result" value={result}/>}
      <div className="form-actions"><Button onClick={()=>setWizard(null)}>Close</Button></div>
    </div></Modal>}
  </div>;
}

function ResearchSettings(){
  const [key,setKey]=useState('');const [saved,setSaved]=useState(false);const [message,setMessage]=useState('');
  useEffect(()=>{rpc<any>('research.settings').then(v=>setSaved(!!v.brave_key_saved)).catch(()=>{});},[]);
  return <section className="surface settings-card"><div className="section-title"><div><h2>Web search — ready without an API key</h2><p>Chat uses free metasearch, then Wikipedia if it is unavailable. Turn on Web in Chat, or ask the AI to search online.</p></div><Network size={20}/></div><div className="settings-card-body"><p className="muted">Your Kaggle/llama.cpp model can request tools when configured for function calling, but that does not give it live browsing by itself. BugHunterClaudex fetches public results and passes cited snippets to your AI.</p><details className="settings-more"><summary>Optional: Brave Search provider <span>{saved?'Key saved':'Not required'}</span></summary><div className="settings-more-body"><Field label={saved?'Replace saved Brave key':'Brave Search API key'}><input type="password" autoComplete="off" value={key} onChange={e=>setKey(e.target.value)} placeholder={saved?'Key saved — enter only to replace':'Optional, for explicit Brave searches'}/></Field><Button disabled={!key.trim()} onClick={async()=>{try{await rpc('research.key',{value:key});setKey('');setSaved(true);setMessage('Optional Brave key saved.');}catch(e){setMessage(String(e));}}}>Save optional key</Button></div></details>{message&&<p role="status">{message}</p>}</div></section>;
}
