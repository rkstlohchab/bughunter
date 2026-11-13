import MediaAI from './MediaAI';
import HarnessSettings from './HarnessSettings';
import ReaSettings from './ReaSettings';
import VoiceSettings from './VoiceSettings';
import MemorySettings from './MemorySettings';
import PluginsSettings from './PluginsSettings';
import ModelEffort from './ModelEffort';
import type {ModelOption} from './ModelEffort';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, CheckCircle2, CircleAlert, CircleDot, KeyRound, Monitor, Moon, Network, ShieldCheck, Sun, Wrench } from 'lucide-react';
import { rpc } from './api';
import { invoke } from '@tauri-apps/api/core';
import type { RecordData as Row } from './api';
import { Button, Field } from './components';
import { AI_PROVIDERS as PROVIDERS, categoryFor, isSubscription } from './aiProviders';
import type { Category } from './aiProviders';
import type { KeyboardEvent } from 'react';

const SETTINGS_SECTIONS=[['ai','AI connections'],['computer','Computer & apps'],['personal','Voice & memory'],['plugins','Plugins'],['learning','Video & learning'],['general','General']] as const;
export type SettingsSection=typeof SETTINGS_SECTIONS[number][0];

type Execute = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
const needsKey = (id: string) => !['codex', 'claude_code','gemini_cli', 'ollama', 'crucible'].includes(id);
const usesBaseUrl = (id: string) => !isSubscription(id);
const defaultModel: Record<string, string> = {claude_code: 'sonnet'};

function AutoSetup({platform}:{platform?:string}) {
  const isMac=!platform||platform==='Darwin';
  const [data, setData] = useState<Row | null>(null);
  const [busy, setBusy] = useState('');
  const [permissions, setPermissions] = useState<Row | null>(null);
  const [permissionError, setPermissionError] = useState('');
  const checking = useRef(false);
  const mounted = useRef(true);
  const refreshPermissions = useCallback(async () => {
    if(!isMac)return;
    if (checking.current) return;
    if (!(window as any).__TAURI_INTERNALS__) {
      setPermissionError('Open the desktop app to check macOS permissions. Browser previews cannot check them.');
      return;
    }
    checking.current = true;
    try {
      const value = await invoke<Row>('mac_permission_status');
      if (mounted.current) { setPermissions(value); setPermissionError(''); }
    } catch (e) { if (mounted.current) setPermissionError(String(e)); }
    finally { checking.current = false; }
  }, [isMac]);
  async function run(install: boolean) {
    setBusy(install ? 'Setting up everything…' : 'Checking…');
    try { setData(await rpc<Row>('system.autosetup', { install_missing: install })); }
    catch (e) { setData({ error: String(e instanceof Error ? e.message : e), steps: [] }); }
    finally { setBusy(''); void refreshPermissions(); }
  }
  useEffect(() => {
    mounted.current = true;
    void run(false); void refreshPermissions();
    const focus = () => { void refreshPermissions(); };
    const visible = () => { if (document.visibilityState === 'visible') focus(); };
    window.addEventListener('focus', focus); document.addEventListener('visibilitychange', visible);
    const timer = setInterval(() => { if (document.visibilityState === 'visible') focus(); }, 5000);
    return () => { mounted.current = false; clearInterval(timer); window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', visible); };
  }, [refreshPermissions]);
  const open = async (pane: string) => { try { if ((window as any).__TAURI_INTERNALS__) await invoke('mac_permission_action', {kind:pane === 'screen_recording' ? 'screen_settings' : pane}); else await rpc('system.open_privacy', {pane}); } catch(e) { setPermissionError(String(e)); } };
  const mark = (s: string) => s === 'done' ? <CheckCircle2 size={17} className="ok" /> : s === 'needs_you' ? <CircleAlert size={17} className="warn" /> : s === 'error' ? <CircleAlert size={17} className="bad" /> : <CircleDot size={17} className="muted-icon" />;
  const steps: Row[] = data?.steps || [];
  return <section className="surface settings-card">
    <div className="section-title"><div><h2>Automatic setup</h2><p>Check what is ready. Optional tools are installed only when you choose Set up tools.</p></div><Wrench size={20} /></div>
    <div className="settings-card-body">
      <div className="button-group wrap"><Button variant="primary" disabled={!!busy} onClick={() => void run(true)}>{busy || 'Set up tools'}</Button><Button disabled={!!busy} onClick={() => void run(false)}>Recheck</Button></div>
      {data?.summary && <p className="autosetup-summary">{data.summary}</p>}
      {data?.error && <p role="alert" className="error-text">{String(data.error)}</p>}
      <div className="autosetup-list">{steps.map((s, i) => <div key={i} className={`autosetup-row ${s.status}`}>
        <span className="autosetup-mark">{mark(String(s.status))}</span>
        <div className="autosetup-body"><strong>{s.label}</strong><p>{s.detail}</p></div>
        {String(s.action || '').startsWith('open:') && <Button onClick={() => void open(String(s.action).split(':')[1])}>Open settings</Button>}
      </div>)}</div>
      {isMac&&<div className="permission-checks" aria-label="Mac permissions">
        <h3>Mac permissions</h3><p className="muted">Checked for this running desktop app. These are needed only for tasks that use them.</p>
        {permissionError && <p role="status">{permissionError}</p>}
        {([['full_disk','Full Disk Access','Reading protected files'],['screen_recording','Screen Recording','Capturing the screen'],['accessibility','Accessibility','Reading and controlling an app']] as const).map(([key,label,reason]) => {
          const value=permissions?.[key]; const status=value===true?'Granted':key==='full_disk'?'Check in macOS Settings':value===false?'Not granted to this app':'Unable to verify';
          return <div className={`autosetup-row ${value===true?'done':''}`} key={key}><span className="autosetup-mark">{mark(value===true?'done':value===false?'needs_you':'optional')}</span><div className="autosetup-body"><strong>{label}</strong><p>{reason}</p><span role="status">{status}</span></div>{value!==true&&<Button onClick={()=>void open(key)}>Open settings</Button>}</div>;
        })}
        {permissions && <p className="muted autosetup-note">Changes refresh when you return here. macOS does not provide a reliable Full Disk Access status check. If you already enabled it, you do not need to grant it again; relaunch the app if a file still cannot be opened.</p>}
      </div>}
    </div>
  </section>;
}

export default function SettingsView({ perform, busy, revision, system, initialSection='ai' }: { perform: Execute; busy: string; revision: number; projectId: string; system: Row; onSystemChange: (value: Row) => void; initialSection?:SettingsSection }) {
  const [activeSection,setActiveSection]=useState<SettingsSection>(initialSection);
  const [visited,setVisited]=useState<SettingsSection[]>([initialSection]);
  function selectSection(id:SettingsSection){setActiveSection(id);setVisited(previous=>previous.includes(id)?previous:[...previous,id]);}
  useEffect(()=>{selectSection(initialSection);},[initialSection]);
  function sectionKeys(event:KeyboardEvent<HTMLButtonElement>,index:number){
    const count=SETTINGS_SECTIONS.length;
    const next=event.key==='Home'?0:event.key==='End'?count-1:event.key==='ArrowRight'?(index+1)%count:event.key==='ArrowLeft'?(index+count-1)%count:null;
    if(next===null)return;event.preventDefault();const id=SETTINGS_SECTIONS[next][0];selectSection(id);document.getElementById(`settings-tab-${id}`)?.focus();
  }
  const [settings, setSettings] = useState<Row>({ provider: 'claude_code', model: 'sonnet' });
  const [category, setCategory] = useState<Category>('subscription');
  const [initialized, setInitialized] = useState(false);
  const [connections, setConnections] = useState<Record<string, Row>>({});
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelDetail, setModelDetail] = useState('');
  const [loadingModels, setLoadingModels] = useState(false);
  const [customModel, setCustomModel] = useState(false);
  const modelRequest = useRef(0);
  const [secret, setSecret] = useState('');
  const [stored, setStored] = useState<Record<string, boolean>>({});
  const [probe, setProbe] = useState<Row | null>(null);
  const [lastBackup, setLastBackup] = useState<string>('');
  const [appearance, setAppearance] = useState<'light' | 'dark'>(() => localStorage.getItem('bughunter.theme') === 'light' ? 'light' : 'dark');
  const setTheme = (mode: 'light' | 'dark') => { setAppearance(mode); localStorage.setItem('bughunter.theme', mode); document.documentElement.dataset.theme = mode; window.dispatchEvent(new CustomEvent('bughunter-theme-change', { detail: mode })); };
  const provider = settings.provider || 'claude_code';
  const selected = PROVIDERS[category].find(p => p.id === provider);
  const hasKey = !!stored[provider];
  const needsPrice = category === 'api';
  useEffect(() => {
    Promise.allSettled([rpc<Row>('settings.get'), rpc<Row>('settings.credentials'), rpc<Record<string, Row>>('settings.connections')]).then(results => {
      const value = results[0].status === 'fulfilled' ? results[0].value : null;
      const creds = results[1].status === 'fulfilled' ? results[1].value : {};
      if (value && !initialized) { setSettings(value); setCategory(categoryFor(value.provider)); setInitialized(true); }
      setStored(creds || {});
      if (results[2].status === 'fulfilled') setConnections(results[2].value || {});
    }).catch(() => { });
  }, [revision, initialized]);
  async function loadModels(refresh = false) {
    const request = ++modelRequest.current;
    setLoadingModels(true); setModelDetail('Loading models…'); setModelOptions([]);
    try {
      const result = await rpc<Row>('models.discover', {provider, api_base_url: settings.api_base_url || '', refresh});
      if (request !== modelRequest.current) return;
      const options: ModelOption[] = result.models || [];
      setModelOptions(options); setModelDetail(result.detail || 'No model list was returned.');
      if (options.length) setSettings(prev => prev.provider !== provider || prev.model ? prev : {...prev, model: (options.find(m => m.default) || options[0]).id});
    } catch (error) {
      if (request === modelRequest.current) setModelDetail(error instanceof Error ? error.message : String(error));
    } finally { if (request === modelRequest.current) setLoadingModels(false); }
  }
  useEffect(() => {
    if (initialized) void loadModels(true);
    return () => { modelRequest.current++; };
  }, [provider, hasKey, initialized]);
  function selectCategory(next: Category) {
    setCategory(next);
    if (!PROVIDERS[next].some(p => p.id === settings.provider)) selectProvider(PROVIDERS[next][0].id);
  }
  function selectProvider(id: string) {
    if (id === provider) return;
    modelRequest.current++; setModelOptions([]); setModelDetail(''); setCustomModel(false);
    setSettings(prev => ({...prev, provider: id, model: defaultModel[id] || '', reasoning_effort:'',api_base_url: '',
      local_only: id === 'ollama' || id === 'crucible', pricing_confirmed: false,
      input_price_per_million: 0, output_price_per_million: 0, ...connections[id]}));
    setSecret(''); setProbe(null);
  }
  function payload(): Row { return { ...settings, provider_system_prompts: {}, pricing_confirmed: !needsPrice || !!settings.pricing_confirmed, api_budget_usd: needsPrice ? settings.api_budget_usd ?? 25 : 0, input_price_per_million: needsPrice ? settings.input_price_per_million ?? 0 : 0, output_price_per_million: needsPrice ? settings.output_price_per_million ?? 0 : 0 }; }
  async function save(test = false) {
    const value = await perform(test ? 'Testing AI connection' : 'Saving AI settings', async () => { const saved = await rpc<Row>('settings.save', payload()); setConnections(old => ({...old, [provider]: saved})); return test ? await rpc<Row>('settings.test') : { ok: true }; }, test ? undefined : 'AI connected.');
    if (test && value) setProbe(value);
  }
  return <div className="settings-clean">
    <div className="settings-categories" role="tablist" aria-label="Settings categories">{SETTINGS_SECTIONS.map(([id,label],index)=><button key={id} id={`settings-tab-${id}`} type="button" role="tab" aria-selected={activeSection===id} aria-controls={`settings-${id}`} tabIndex={activeSection===id?0:-1} onKeyDown={event=>sectionKeys(event,index)} onClick={()=>selectSection(id)}>{label}</button>)}</div>
    <section id="settings-ai" role="tabpanel" hidden={activeSection!=='ai'} className="settings-category" aria-labelledby="settings-tab-ai"><h2>AI connections</h2><p className="muted">Models, subscriptions and API connections.</p>
    <section className="surface settings-card">
      <div className="section-title"><div><h2>Connect your AI</h2><p>Add the models you want. Switch between them any time from the picker in Chat.</p></div><Network size={20} /></div>
      <div className="settings-card-body">
        <div className="provider-categories" role="group" aria-label="AI connection type">
          <button className={category === 'subscription' ? 'active' : ''} onClick={() => selectCategory('subscription')}>Subscription</button>
          <button className={category === 'api' ? 'active' : ''} onClick={() => selectCategory('api')}>Paid APIs</button>
          <button className={category === 'selfhosted' ? 'active' : ''} onClick={() => selectCategory('selfhosted')}>My own models</button>
        </div>
        <div className="provider-cards" role="group" aria-label="Provider">
          {PROVIDERS[category].map(item => <button type="button" key={item.id} className={`provider-card ${provider === item.id ? 'active' : ''}`} aria-pressed={provider === item.id} onClick={() => selectProvider(item.id)}><strong>{item.name}</strong><small>{item.detail}</small></button>)}
        </div>
        <form onSubmit={e => { e.preventDefault(); void save(false); }}>
          {selected && <p className="muted settings-mode-note">Selected: {selected.name}</p>}
          {isSubscription(provider) && <p className="settings-mode-note">{provider === 'codex' ? <>Uses your ChatGPT sign-in in Codex. To connect another account, run <code>codex login</code> in Terminal, then refresh models. Your plan’s usage limits apply.</> : provider==='gemini_cli'?<>Uses your Google account through Gemini CLI. Sign in to <code>gemini</code> with your Google AI Pro account, then refresh. API-key access has separate quota and billing.</>: <>Uses your Claude Code sign-in. Run <code>claude auth login</code> if you have not connected it.</>}</p>}
          <Field label="Model" hint="Choose from the models this connection reports.">
            <select aria-label="Model" value={customModel ? '__custom__' : settings.model || ''} onChange={e => {if (e.target.value === '__custom__') setCustomModel(true); else {setCustomModel(false); setSettings({...settings, model:e.target.value, reasoning_effort:'',pricing_confirmed: false});}}} required>
              <option value="" disabled>{loadingModels ? 'Loading models…' : 'Choose a model'}</option>
              {settings.model && !modelOptions.some(m => m.id === settings.model) && <option value={settings.model}>{settings.model} (saved)</option>}
              {modelOptions.map(m => <option key={m.id} value={m.id}>{m.label||m.id}</option>)}
              <option value="__custom__">Enter a custom model…</option>
            </select>
          </Field>
          <ModelEffort info={modelOptions.find(model=>model.id===settings.model)?.effort} value={settings.reasoning_effort} onChange={reasoning_effort=>setSettings({...settings,reasoning_effort})} disabled={!!busy||loadingModels}/>
          {customModel && <Field label="Custom model identifier"><input value={settings.model || ''} onChange={e => setSettings({...settings, model:e.target.value, pricing_confirmed:false})} required placeholder="Exact identifier supplied by your provider" /></Field>}
          <div className="model-discovery"><Button disabled={loadingModels} onClick={() => void loadModels(true)}>Refresh models</Button><p role="status">{modelDetail}</p></div>
          {usesBaseUrl(provider) && <Field label="Server address" hint={provider === 'openai_compatible' ? 'For Kaggle, paste the tunnel URL ending in /v1.' : 'Leave empty to use the provider default, if supported.'}><input type="text" aria-label="Server address" value={settings.api_base_url || ''} onChange={e => setSettings({ ...settings, api_base_url: e.target.value })} placeholder={provider === 'openai_compatible' ? 'https://your-tunnel.ngrok-free.app/v1' : provider === 'ollama' ? 'http://127.0.0.1:11434' : 'Provider URL'} /></Field>}
          {needsKey(provider) && <div className="credential-box"><div className="credential-status"><KeyRound size={17} /><strong>Connection key</strong>{hasKey ? <span className="saved-pill"><CheckCircle2 size={14} /> Saved in keychain</span> : <span className="muted">No saved key</span>}</div><Field label={hasKey ? 'Replace saved key (optional)' : 'API key'} hint="Your key is stored in the macOS keychain and never displayed again."><input type="password" autoComplete="off" value={secret} onChange={e => setSecret(e.target.value)} placeholder={hasKey ? '•••••••••• (saved — enter only to replace)' : 'Paste your key'} /></Field><Button disabled={!secret.trim() || !!busy} onClick={async () => { const saved = await perform('Saving credential', () => rpc('settings.secret', { provider, value: secret }), 'API key saved securely.'); if (saved) { setSecret(''); setStored(old => ({ ...old, [provider]: true })); } }}>{hasKey ? 'Update key' : 'Save key'}</Button></div>}
          {needsPrice && <details className="settings-more"><summary>API prices and spending limit <span>Required for paid providers</span></summary><div className="settings-more-body"><Field label="Maximum API spend per run (USD)"><input type="number" step="any" min="0" value={settings.api_budget_usd ?? 25} onChange={e => setSettings({ ...settings, api_budget_usd: Number(e.target.value) })} /></Field><div className="form-grid"><Field label="Input USD / million tokens"><input type="number" step="any" min="0" value={settings.input_price_per_million ?? 0} onChange={e => setSettings({ ...settings, input_price_per_million: Number(e.target.value), pricing_confirmed: false })} /></Field><Field label="Output USD / million tokens"><input type="number" step="any" min="0" value={settings.output_price_per_million ?? 0} onChange={e => setSettings({ ...settings, output_price_per_million: Number(e.target.value), pricing_confirmed: false })} /></Field></div><label className="check-row"><input type="checkbox" checked={!!settings.pricing_confirmed} onChange={e => setSettings({ ...settings, pricing_confirmed: e.target.checked })} />I have checked these prices.</label></div></details>}
          <Field label="Your AI instructions (optional)" hint="Style and focus preferences applied across chats. The provider still enforces its own rules."><textarea rows={4} maxLength={8000} value={settings.custom_system_prompt || ''} onChange={e => setSettings({ ...settings, custom_system_prompt: e.target.value })} placeholder="Use my project code and indexed knowledge first. Explain with source references and clear steps." /></Field>
          <div className="form-actions"><Button type="submit" variant="primary" disabled={!!busy || !settings.model}>Save</Button><Button type="button" icon={Activity} disabled={!!busy || !settings.model} onClick={() => void save(true)}>Save & test</Button></div>
          {probe && <p role="status" className={`connection-result ${probe.ok ? 'ok' : 'bad'}`}>{probe.detail || 'Connection check finished.'}</p>}
        </form>
      </div>
    </section>
    </section>
    <section id="settings-computer" role="tabpanel" hidden={activeSection!=='computer'} className="settings-category" aria-labelledby="settings-tab-computer"><h2>Computer & apps</h2><p className="muted">Device permissions, desktop control and app installation.</p>
    {visited.includes('computer')&&<><AutoSetup platform={system.platform}/><HarnessSettings stopped={!!system.emergency_stop}/><ReaSettings stopped={!!system.emergency_stop}/></>}
    </section>
    <section id="settings-personal" role="tabpanel" hidden={activeSection!=='personal'} className="settings-category" aria-labelledby="settings-tab-personal"><h2>Voice & memory</h2><p className="muted">How your assistant listens and learns about you.</p>
    {visited.includes('personal')&&<><VoiceSettings stopped={!!system.emergency_stop}/><MemorySettings/></>}
    </section>
    <section id="settings-plugins" role="tabpanel" hidden={activeSection!=='plugins'} className="settings-category" aria-labelledby="settings-tab-plugins"><h2>Plugins</h2><p className="muted">Connect the services your tasks use.</p>
    {visited.includes('plugins')&&<PluginsSettings stopped={!!system.emergency_stop}/>}
    </section>
    <section id="settings-learning" role="tabpanel" hidden={activeSection!=='learning'} className="settings-category" aria-labelledby="settings-tab-learning"><h2>Video & learning</h2><p className="muted">Analysis models and resource processing.</p>
    {visited.includes('learning')&&<MediaAI sources={[]} mode="settings" tasksStopped={!!system.emergency_stop}/>}
    </section>
    <section id="settings-general" role="tabpanel" hidden={activeSection!=='general'} className="settings-category" aria-labelledby="settings-tab-general"><h2>General</h2><p className="muted">Appearance and backups.</p>
    <section className="surface settings-card appearance-card" aria-label="Appearance settings"><div className="section-title"><div><h2>Appearance</h2><p>Choose the theme you prefer. Remembered on this device.</p></div><Monitor size={20} /></div><div className="appearance-options" role="group" aria-label="Appearance"><button className={appearance === 'dark' ? 'active' : ''} aria-pressed={appearance === 'dark'} onClick={() => setTheme('dark')}><Moon size={18} /> Dark mode</button><button className={appearance === 'light' ? 'active' : ''} aria-pressed={appearance === 'light'} onClick={() => setTheme('light')}><Sun size={18} /> Light mode</button></div></section>

    <section className="surface settings-card"><div className="section-title"><div><h2>Back up your work</h2><p>Save your projects, conversations and history before changing versions.</p></div><ShieldCheck size={20} /></div><div className="settings-card-body"><p className="muted">Creates a private ZIP on this Mac. API keys in your keychain are not included.</p><Button disabled={!!busy} onClick={async () => { const result = await perform('Creating backup', () => rpc<Row>('system.backup.create'), 'Backup created.'); if (result?.path) setLastBackup(result.path); }}>Create backup</Button>{lastBackup && <p role="status" className="muted">Saved to: <code className="backup-path">{lastBackup}</code></p>}</div></section>
    </section>
  </div>;
}
