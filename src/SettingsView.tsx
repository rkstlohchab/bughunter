import { useEffect, useState } from 'react';
import { Activity, CheckCircle2, CircleAlert, CircleDot, KeyRound, Monitor, Moon, Network, ShieldCheck, Sun, Wrench } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Field } from './components';

type Execute = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
type Category = 'subscription' | 'api' | 'selfhosted';
const PROVIDERS: Record<Category, { id: string; name: string; detail: string }[]> = {
  subscription: [{ id: 'claude_code', name: 'Claude subscription', detail: 'Use your Claude.ai plan' }],
  api: [{ id: 'anthropic', name: 'Anthropic', detail: 'API billing' }, { id: 'openai', name: 'OpenAI (ChatGPT)', detail: 'API billing' }, { id: 'deepseek', name: 'DeepSeek', detail: 'API billing' }],
  selfhosted: [{ id: 'openai_compatible', name: 'Kaggle / custom AI', detail: 'OpenAI-compatible server' }, { id: 'ollama', name: 'Ollama on this Mac', detail: 'Local model' }, { id: 'remote_ollama', name: 'Ollama on another computer', detail: 'Remote Ollama API' }, { id: 'crucible', name: 'CrucibleLLM', detail: 'Model on your device' }],
};
const categoryFor = (id: string): Category => (Object.keys(PROVIDERS) as Category[]).find(cat => PROVIDERS[cat].some(p => p.id === id)) || 'selfhosted';
const needsKey = (id: string) => !['claude_code', 'ollama', 'crucible'].includes(id);
const usesBaseUrl = (id: string) => id !== 'claude_code';
const defaultModel: Record<string, string> = { claude_code: 'sonnet', ollama: 'qwen3:8b', remote_ollama: 'qwen3:8b', openai_compatible: 'bughunter-moe', openai: 'gpt-4.1-mini', anthropic: 'claude-sonnet-4-5', deepseek: 'deepseek-chat', crucible: 'local' };

function AutoSetup() {
  const [data, setData] = useState<Row | null>(null);
  const [busy, setBusy] = useState('');
  async function run(install: boolean) {
    setBusy(install ? 'Setting up everything…' : 'Checking…');
    try { setData(await rpc<Row>('system.autosetup', { install_missing: install })); }
    catch (e) { setData({ error: String(e instanceof Error ? e.message : e), steps: [] }); }
    finally { setBusy(''); }
  }
  useEffect(() => { void run(false); }, []);
  const open = async (pane: string) => { try { await rpc('system.open_privacy', { pane }); } catch { /* ignore */ } };
  const mark = (s: string) => s === 'done' ? <CheckCircle2 size={17} className="ok" /> : s === 'needs_you' ? <CircleAlert size={17} className="warn" /> : s === 'error' ? <CircleAlert size={17} className="bad" /> : <CircleDot size={17} className="muted-icon" />;
  const steps: Row[] = data?.steps || [];
  return <section className="surface settings-card">
    <div className="section-title"><div><h2>Automatic setup</h2><p>One click connects your tools, starts local models, verifies the web gateway, and turns on everything the app can enable itself.</p></div><Wrench size={20} /></div>
    <div className="settings-card-body">
      <div className="button-group wrap"><Button variant="primary" disabled={!!busy} onClick={() => void run(true)}>{busy || 'Set up everything'}</Button><Button disabled={!!busy} onClick={() => void run(false)}>Recheck</Button></div>
      {data?.summary && <p className="autosetup-summary">{data.summary}</p>}
      {data?.error && <p role="alert" className="error-text">{String(data.error)}</p>}
      <div className="autosetup-list">{steps.map((s, i) => <div key={i} className={`autosetup-row ${s.status}`}>
        <span className="autosetup-mark">{mark(String(s.status))}</span>
        <div className="autosetup-body"><strong>{s.label}</strong><p>{s.detail}</p></div>
        {String(s.action || '').startsWith('open:') && <Button onClick={() => void open(String(s.action).split(':')[1])}>Open settings</Button>}
      </div>)}</div>
      <p className="muted autosetup-note">macOS requires you to toggle Full Disk Access, Screen Recording and Accessibility by hand — the app can only open the right pane and detect them. Everything else is automatic.</p>
    </div>
  </section>;
}

export default function SettingsView({ perform, busy, revision, system, onSystemChange }: { perform: Execute; busy: string; revision: number; projectId: string; system: Row; onSystemChange: (value: Row) => void }) {
  const [settings, setSettings] = useState<Row>({ provider: 'claude_code', model: 'sonnet' });
  const [category, setCategory] = useState<Category>('subscription');
  const [initialized, setInitialized] = useState(false);
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
    Promise.allSettled([rpc<Row>('settings.get'), rpc<Row>('settings.credentials')]).then(results => {
      const value = results[0].status === 'fulfilled' ? results[0].value : null;
      const creds = results[1].status === 'fulfilled' ? results[1].value : {};
      if (value && !initialized) { setSettings(value); setCategory(categoryFor(value.provider)); setInitialized(true); }
      setStored(creds || {});
    }).catch(() => { });
  }, [revision, initialized]);
  function selectCategory(next: Category) {
    setCategory(next);
    if (!PROVIDERS[next].some(p => p.id === settings.provider)) {
      const id = PROVIDERS[next][0].id;
      setSettings(prev => ({ ...prev, provider: id, model: defaultModel[id], api_base_url: '', local_only: id === 'ollama' || id === 'crucible', pricing_confirmed: false, input_price_per_million: 0, output_price_per_million: 0 }));
    }
    setSecret(''); setProbe(null);
  }
  function selectProvider(id: string) {
    setSettings(prev => ({ ...prev, provider: id, model: prev.provider === id ? prev.model : defaultModel[id], api_base_url: prev.provider === id ? prev.api_base_url : '', local_only: id === 'ollama' || id === 'crucible', pricing_confirmed: prev.provider === id ? !!prev.pricing_confirmed : false, input_price_per_million: prev.provider === id ? prev.input_price_per_million : 0, output_price_per_million: prev.provider === id ? prev.output_price_per_million : 0 }));
    setSecret(''); setProbe(null);
  }
  function payload(): Row { return { ...settings, provider_system_prompts: {}, pricing_confirmed: !needsPrice || !!settings.pricing_confirmed, api_budget_usd: needsPrice ? settings.api_budget_usd ?? 25 : 0, input_price_per_million: needsPrice ? settings.input_price_per_million ?? 0 : 0, output_price_per_million: needsPrice ? settings.output_price_per_million ?? 0 : 0 }; }
  async function save(test = false) {
    const value = await perform(test ? 'Testing AI connection' : 'Saving AI settings', async () => { await rpc('settings.save', payload()); return test ? await rpc<Row>('settings.test') : { ok: true }; }, test ? undefined : 'AI connected.');
    if (test && value) setProbe(value);
  }
  return <div className="settings-clean">
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
          <Field label="Model name"><input value={settings.model || ''} onChange={e => setSettings({ ...settings, model: e.target.value })} placeholder="Model identifier" required /></Field>
          {usesBaseUrl(provider) && <Field label="Server address" hint={provider === 'openai_compatible' ? 'For Kaggle, paste the tunnel URL ending in /v1.' : 'Leave empty to use the provider default, if supported.'}><input type="text" value={settings.api_base_url || ''} onChange={e => setSettings({ ...settings, api_base_url: e.target.value })} placeholder={provider === 'openai_compatible' ? 'https://your-tunnel.ngrok-free.app/v1' : provider === 'ollama' ? 'http://127.0.0.1:11434' : 'Provider URL'} /></Field>}
          {needsKey(provider) && <div className="credential-box"><div className="credential-status"><KeyRound size={17} /><strong>Connection key</strong>{hasKey ? <span className="saved-pill"><CheckCircle2 size={14} /> Saved in keychain</span> : <span className="muted">No saved key</span>}</div><Field label={hasKey ? 'Replace saved key (optional)' : 'API key'} hint="Your key is stored in the macOS keychain and never displayed again."><input type="password" autoComplete="off" value={secret} onChange={e => setSecret(e.target.value)} placeholder={hasKey ? '•••••••••• (saved — enter only to replace)' : 'Paste your key'} /></Field><Button disabled={!secret.trim() || !!busy} onClick={async () => { const saved = await perform('Saving credential', () => rpc('settings.secret', { provider, value: secret }), 'API key saved securely.'); if (saved) { setSecret(''); setStored(old => ({ ...old, [provider]: true })); } }}>{hasKey ? 'Update key' : 'Save key'}</Button></div>}
          {needsPrice && <details className="settings-more"><summary>API prices and spending limit <span>Required for paid providers</span></summary><div className="settings-more-body"><Field label="Maximum API spend per run (USD)"><input type="number" step="any" min="0" value={settings.api_budget_usd ?? 25} onChange={e => setSettings({ ...settings, api_budget_usd: Number(e.target.value) })} /></Field><div className="form-grid"><Field label="Input USD / million tokens"><input type="number" step="any" min="0" value={settings.input_price_per_million ?? 0} onChange={e => setSettings({ ...settings, input_price_per_million: Number(e.target.value), pricing_confirmed: false })} /></Field><Field label="Output USD / million tokens"><input type="number" step="any" min="0" value={settings.output_price_per_million ?? 0} onChange={e => setSettings({ ...settings, output_price_per_million: Number(e.target.value), pricing_confirmed: false })} /></Field></div><label className="check-row"><input type="checkbox" checked={!!settings.pricing_confirmed} onChange={e => setSettings({ ...settings, pricing_confirmed: e.target.checked })} />I have checked these prices.</label></div></details>}
          <Field label="Your AI instructions (optional)" hint="Style and focus preferences applied across chats. The provider still enforces its own rules."><textarea rows={4} maxLength={8000} value={settings.custom_system_prompt || ''} onChange={e => setSettings({ ...settings, custom_system_prompt: e.target.value })} placeholder="Use my project code and indexed knowledge first. Explain with source references and clear steps." /></Field>
          <div className="form-actions"><Button type="submit" variant="primary" disabled={!!busy}>Save</Button><Button type="button" icon={Activity} disabled={!!busy} onClick={() => void save(true)}>Save & test</Button></div>
          {probe && <p role="status" className={`connection-result ${probe.ok ? 'ok' : 'bad'}`}>{probe.detail || 'Connection check finished.'}</p>}
        </form>
      </div>
    </section>

    <AutoSetup />

    <section className="surface settings-card appearance-card" aria-label="Appearance settings"><div className="section-title"><div><h2>Appearance</h2><p>Choose the theme you prefer. Remembered on this device.</p></div><Monitor size={20} /></div><div className="appearance-options" role="group" aria-label="Appearance"><button className={appearance === 'dark' ? 'active' : ''} aria-pressed={appearance === 'dark'} onClick={() => setTheme('dark')}><Moon size={18} /> Dark mode</button><button className={appearance === 'light' ? 'active' : ''} aria-pressed={appearance === 'light'} onClick={() => setTheme('light')}><Sun size={18} /> Light mode</button></div></section>

    <section className="surface settings-card"><div className="section-title"><div><h2>Back up your work</h2><p>Save your projects, conversations and history before changing versions.</p></div><ShieldCheck size={20} /></div><div className="settings-card-body"><p className="muted">Creates a private ZIP on this Mac. API keys in your keychain are not included.</p><Button disabled={!!busy} onClick={async () => { const result = await perform('Creating backup', () => rpc<Row>('system.backup.create'), 'Backup created.'); if (result?.path) setLastBackup(result.path); }}>Create backup</Button>{lastBackup && <p role="status" className="muted">Saved to: <code className="backup-path">{lastBackup}</code></p>}</div></section>

    <details className="surface settings-card settings-technical"><summary>Task controls</summary><div className="settings-card-body"><p className="muted">Chat always works. This only stops the agent from sending new outbound requests to live targets.</p><p role="status">Current state: <strong>{system.emergency_stop ? 'Outbound requests paused' : 'Outbound requests allowed'}</strong></p><div className="button-group wrap">{system.emergency_stop ? <Button disabled={!!busy} onClick={async () => { const v = await perform('Resuming', () => rpc<Row>('system.resume'), 'Outbound requests enabled.'); if (v) onSystemChange({ ...system, emergency_stop: false }); }}>Resume outbound requests</Button> : <Button disabled={!!busy} onClick={async () => { const v = await perform('Pausing', () => rpc<Row>('system.stop'), 'Outbound requests stopped.'); if (v) onSystemChange({ ...system, emergency_stop: true }); }}>Pause outbound requests</Button>}</div></div></details>
  </div>;
}
