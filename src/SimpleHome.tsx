import { useEffect, useState } from 'react';
import { Play, Pause, Target, FlaskConical, Settings2, MessageSquare, Wrench, ExternalLink } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import type { Shared } from './App';
import { Button, Field, Status, Modal, DetailItem, Code } from './components';

export default function SimpleHome({data,projectId,perform,busy,navigate,system,selectProject}: Shared & {system: Row; selectProject: (id:string) => void}) {
  const [newTarget,setNewTarget] = useState(false);
  const [url,setUrl] = useState(''); const [permission,setPermission] = useState('');
  const [objective,setObjective] = useState('Investigate the authorized target using relevant lessons. Inspect available workflows, propose and perform permitted checks, compare evidence, and draft supported reports. Explain anything blocked or awaiting my review.');
  const [selected,setSelected] = useState<Row|null>(null);
  const [modelSettings,setModelSettings] = useState<Row | null>(null);
  const [tools,setTools] = useState<Row[]>([]);
  useEffect(() => {
    rpc<Row>('settings.get').then(setModelSettings).catch(() => setModelSettings(null));
    rpc<any>('tools.status').then(value => setTools(Array.isArray(value) ? value : Array.isArray(value?.tools) ? value.tools : Object.entries(value || {}).map(([name,v])=>({name,...(typeof v==='object' && v !== null ? v as Row : {available:!!v})})))).catch(()=>setTools([]));
  }, [system.provider]);
  const provider = modelSettings?.provider || system.provider || 'claude_code';
  const providerNames: Record<string,string> = {openai_compatible:'Kaggle / custom AI',ollama:'Ollama on this Mac',remote_ollama:'Remote Ollama',claude_code:'Claude Code subscription',anthropic:'Anthropic API',openai:'OpenAI API',deepseek:'DeepSeek API',crucible:'CrucibleLLM'};
  const modelLabel = `${providerNames[provider] || provider}${modelSettings?.model ? ` · ${modelSettings.model}` : ''}`;
  const project = data.projects.find(p => p.id === projectId);
  const latest = data.runs[0];
  const running = data.runs.some(r => ['running','starting'].includes(r.status));
  const creating = newTarget || !project || !project.scope?.length;
  async function start() {
    let id = projectId;
    if (creating) {
      const result = await perform('Saving target', () => rpc('projects.save', { ...(project && !project.scope?.length && !newTarget ? project : {}),name:project && !project.scope?.length && !newTarget ? project.name : new URL(url).hostname,scope:[url],authorization:permission,workspace_only:false,exclusions:[],read_only_paths:[],requests_per_second:1,max_concurrency:2,max_requests:1000,allow_private:false}));
      if (!result) return;
      id = result.id; selectProject(id); setNewTarget(false);
    }
    await perform('Starting investigation', () => rpc('tasks.start',{kind:'web_investigation',project_id:id,prompt:objective,auto_connect:true}),'Investigation started. Follow its activity below.');
  }
  return <div className="simple-home">
    <section className="surface home-target"><div className="section-title"><div><h2>{creating ? (project?.name && !project.scope?.length ? `Add a web target to ${project.name}` : 'Where should the AI investigate?') : project.name}</h2><p>{creating ? 'Use a website or API you have permission to test.' : project.scope.join(' · ')}</p></div><Target size={22}/></div>
      <div className="home-body">{creating ? <><Field label="Target URL"><input type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-authorized-target.example"/></Field><Field label="Permission and program rules" hint="Record the program or ownership permission, allowed accounts, and restrictions."><textarea rows={2} value={permission} onChange={e => setPermission(e.target.value)}/></Field>{project && <Button onClick={() => setNewTarget(false)}>Use current target</Button>}</> : <div className="button-group wrap"><Button onClick={() => setNewTarget(true)}>New target</Button><Button icon={Settings2} onClick={() => navigate('Targets')}>Manage or delete projects</Button></div>}
        <details className="advanced-options"><summary>Investigation objective</summary><textarea aria-label="Investigation objective" rows={4} value={objective} onChange={e => setObjective(e.target.value)}/></details>
        <div className="home-provider"><div className="home-model-identity"><span className="muted">Selected AI</span><strong>{modelLabel}</strong><small>{['ollama','remote_ollama','openai_compatible','crucible'].includes(provider) ? 'Your own model · no per-run API budget' : provider === 'claude_code' ? 'Subscription' : 'Paid API'}</small></div><div className="button-group"><Button icon={MessageSquare} onClick={() => navigate('Chat')}>Chat with AI</Button><Button onClick={() => navigate('Settings')}>Change AI</Button></div></div>
        <p className="muted">The AI searches your library, inspects workflows, and investigates within your rules. Website changes pause here for review. The built-in local web gateway connects automatically when you start. Burp is optional. Any sensitive website actions still pause for your approval.</p>
        <div className="home-start"><Button variant="primary" icon={Play} disabled={!!busy || running || !!system.emergency_stop || !objective.trim() || (creating && (!/^https?:\/\//i.test(url) || !permission.trim()))} onClick={start}>Start investigation</Button><Button icon={FlaskConical} disabled={!!busy} onClick={() => perform('Preparing local lab', () => rpc('lab.setup'), 'Disposable test-shop project created.').then(v => v?.project && selectProject(v.project.id))}>Try the disposable test shop</Button></div>
        {system.emergency_stop && <p className="inline-hint">Work is stopped. Use Resume requests at the top to enable new work.</p>}
      </div>
    </section>
    <section className="surface web-integrations" aria-label="Available web testing integrations">
      <div className="section-title"><div><h2>Web testing tools</h2><p>Use these integrations when a task needs them. Chat and saved knowledge work without connecting any of them.</p></div><Wrench size={20}/></div>
      <div className="web-integration-grid">{[
        {id:'burp',name:'Burp proxy',description:'Inspect intercepted HTTP traffic'},
        {id:'browser',name:'Managed browser',description:'Open in-scope pages for examination'},
        {id:'nuclei',name:'Nuclei checks',description:'Run available reviewed templates'},
        {id:'discovery',name:'Host discovery',description:'Discover permitted subdomains and hosts'}
      ].map(tool=>{const matches=tools.filter(row=>String(row.name || '').toLowerCase().includes(tool.id==='discovery'?'subdomain':tool.id));const installed=matches.some(row=>row.available || row.installed);return <div className="integration-tile" key={tool.id}><div><strong>{tool.name}</strong><p>{tool.description}</p></div><div className="integration-meta"><span className={`integration-status ${installed?'installed':''}`}>{installed?'Detected':'Optional'}</span><Button icon={ExternalLink} onClick={()=>navigate('Settings')}>Setup</Button></div></div>;})}</div>
    </section>
    <section className="surface home-progress" aria-label="AI progress"><div className="section-title"><div><h2>What the AI is doing</h2><p>{latest ? latest.summary || (latest.status === 'awaiting_approval' ? 'Waiting for you to review proposed requests below.' : 'Updates appear as the agent works.') : 'Ready when you are. Add lessons in Library, then start an investigation.'}</p></div>{latest && <Status value={latest.status}/>}</div>
      {latest && <div className="home-body"><DetailItem label="Needs attention" value={latest.error}/><ol className="activity-feed" aria-live="polite">{(latest.events || []).slice(-8).map((event:Row,i:number) => <li key={`${event.time}-${i}`}><span>{new Date(event.time*1000).toLocaleTimeString()}</span><div><strong>{String(event.type).replaceAll('_',' ')}</strong><p>{String(event.text).slice(0,800)}</p></div></li>)}</ol><div className="button-group wrap">{running && <Button icon={Pause} onClick={() => perform('Pausing investigation', () => rpc('runs.pause',{id:latest.id}))}>Pause investigation</Button>}{['paused','awaiting_approval','limited','failed','completed'].includes(latest.status) && <Button icon={Play} disabled={!!busy || !!system.emergency_stop || running} onClick={() => perform('Continuing investigation', () => rpc('runs.resume',{id:latest.id}))}>Continue investigation</Button>}<Button onClick={() => setSelected(latest)}>View full activity</Button></div></div>}
    </section>
    <div className="home-results"><span>{data.tests.length} checks suggested</span><span>{data.actions.filter(a => a.status==='completed').length} requests completed</span><Button onClick={() => navigate('Findings')}>{data.findings.length} reports</Button><Button onClick={() => navigate('Library')}>{data.sources.length} learning resources</Button></div>
    {data.runs.length>1 && <details className="surface history-details"><summary>Previous investigations ({data.runs.length-1})</summary>{data.runs.slice(1).map(run => <button className="history-entry" key={run.id} onClick={() => setSelected(run)}><span>{run.prompt}</span><Status value={run.status}/></button>)}</details>}
    {selected && <Modal title="Investigation activity" wide onClose={() => setSelected(null)}><DetailItem label="Objective" value={selected.prompt}/><DetailItem label="Summary" value={(data.runs.find(r => r.id===selected.id)||selected).summary}/><Code label="Activity" value={(data.runs.find(r => r.id===selected.id)||selected).events || []}/></Modal>}
  </div>;
}
