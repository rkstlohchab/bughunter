import { useEffect, useState } from 'react';
import { ClipboardCopy, FileText, RefreshCw, Play } from 'lucide-react';
import { asList, rpc } from './api';
import type { RecordData as Row } from './api';
import { Button } from './components';

type ModelChoice = { provider: string; model: string; kind: string; local_only: boolean; ready: boolean };
type Connection = { configured: boolean; origin: string; detail: string; read_only: boolean; ai: ModelChoice };
type Plan = { prompt: string; methodology: { id: string; title: string; reference: string; checks: string[]; tbhm_sections?: { id: string }[] }[] };
type TbhmSection = { id: string; title: string; traffic: string; status?: string };
type TbhmOutline = { phases: { phase: string; title: string; sections: TbhmSection[] }[]; sources: Record<string, { title: string; passages: number }> };
const TBHM_STATUS: Record<string, string> = { todo: 'to do', in_progress: 'in progress', no_findings: 'no findings', findings: 'findings', not_applicable: 'not applicable' };
const TBHM_TONE: Record<string, string> = { in_progress: 'rt-busy', no_findings: 'rt-ok', findings: 'rt-bad' };

export default function PentagiBridgePanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [projects, setProjects] = useState<Row[]>([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [flows, setFlows] = useState<Row[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [nativeRun, setNativeRun] = useState<Row | null>(null);
  const [tbhm, setTbhm] = useState<TbhmOutline | null>(null);

  useEffect(() => {
    void Promise.all([rpc<Connection>('pentagi.status'), rpc<Row[]>('projects.list')])
      .then(([status, list]) => {
        setConnection(status);
        const available = asList(list).filter(item => Boolean(item.authorization) && Array.isArray(item.scope) && item.scope.length > 0);
        setProjects(available);
        setSelectedProject(current => current || String(available[0]?.id || ''));
      })
      .catch(err => setError(String(err)));
  }, []);

  useEffect(() => {
    let live = true;
    void rpc<TbhmOutline>('tbhm.outline', selectedProject ? { project_id: selectedProject } : {})
      .then(value => { if (live) setTbhm(value); })
      .catch(() => { if (live) setTbhm(null); });
    return () => { live = false; };
  }, [selectedProject]);

  const tbhmSections = tbhm?.phases.flatMap(phase => phase.sections) || [];
  const tbhmDone = tbhmSections.filter(section => section.status && !['todo', 'in_progress'].includes(section.status)).length;
  const tbhmDecks = ['deck:day1', 'deck:day2'].filter(key => tbhm?.sources[key]).length;
  const tbhmTalks = ['transcript:day1', 'transcript:day2'].filter(key => tbhm?.sources[key]).length;

  async function loadFlows() {
    setError(''); setBusy(true);
    try { setFlows(await rpc<Row[]>('pentagi.flows')); }
    catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }

  async function buildPlan() {
    if (!selectedProject) return;
    setError(''); setBusy(true);
    try { setPlan(await rpc<Plan>('pentagi.plan', { project_id: selectedProject })); }
    catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }

  async function startNativeInvestigation() {
    if (!selectedProject || busy) return;
    setError(''); setBusy(true);
    try {
      setNativeRun(await rpc<Row>('pentagi.run_native', { project_id: selectedProject, approved: true }));
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }

  async function copyPlan() {
    if (!plan) return;
    try { await navigator.clipboard.writeText(plan.prompt); }
    catch (err) { setError('Unable to copy the plan: ' + String(err)); }
  }

  return <section className="surface cart-audit" aria-label="PentAGI methodology bridge">
    <div className="section-title">
      <div>
        <h2><FileText size={18} /> BugHunter AI + PentAGI + Haddix</h2>
        <p>Run a Haddix and checkout investigation using your selected BugHunter model. External PentAGI flow review is optional.</p>
      </div>
    </div>
    <p className="muted">
      {connection?.ai?.ready
        ? 'Selected BugHunter AI: ' + connection.ai.provider + ' / ' + connection.ai.model +
          ' (' + connection.ai.kind + (connection.ai.local_only ? ', local-only' : '') + ').'
        : 'Select a model in BugHunter Settings first.'}
      {' '}Uses the existing subscription login, local inference server, or saved cloud API key.
    </p>
    <p className="muted">
      {connection?.configured
        ? 'External PentAGI flow history connected at ' + connection.origin + '.'
        : 'External PentAGI is optional; flow review requires its own API connection.'}
      {' '}BugHunter-native investigation uses the existing request approval gateway.
    </p>
    <div className="cart-audit-actions">
      <label htmlFor="pentagi-project">Project</label>
      <select id="pentagi-project" value={selectedProject}
        onChange={event => { setSelectedProject(event.target.value); setPlan(null); }}>
        {!projects.length && <option value="">No scoped project available</option>}
        {projects.map(item => <option key={String(item.id)} value={String(item.id)}>{String(item.name || item.id)}</option>)}
      </select>
      <Button icon={Play} disabled={!selectedProject || !connection?.ai?.ready || busy || Boolean(nativeRun)} onClick={() => void startNativeInvestigation()}>Run with selected BugHunter AI</Button>
      <Button icon={FileText} disabled={!selectedProject || busy} onClick={() => void buildPlan()}>Preview Haddix + commerce plan</Button>
      <Button icon={RefreshCw} disabled={!connection?.configured || busy} onClick={() => void loadFlows()}>Read PentAGI flows</Button>
    </div>
    {error && <p role="alert" className="conversation-error">{error}</p>}
    {nativeRun && <p role="status">BugHunter investigation {String(nativeRun.id)} started with {String(nativeRun.provider)} / {String(nativeRun.model)}. Monitor it in the existing Runs/Approvals workspace.</p>}
    {plan && <>
      <p className="muted">{plan.methodology.length} planning stages linked to {new Set(plan.methodology.flatMap(stage => (stage.tbhm_sections || []).map(section => section.id))).size} TBHM sections, including cart, coupon, shipping, currency and checkout-state validation.</p>
      <textarea readOnly rows={15} value={plan.prompt}
        style={{ width: '100%', resize: 'vertical', boxSizing: 'border-box' }} aria-label="PentAGI handoff plan" />
      <Button icon={ClipboardCopy} onClick={() => void copyPlan()}>Copy plan</Button>
    </>}
    {tbhm && <div aria-label="TBHM coverage">
      <p className="muted">
        TBHM evidence in the Library: {tbhmDecks}/2 Live decks, {tbhmTalks}/2 talk recordings.
        {tbhmDecks < 2 && ' Missing decks fall back to the authored outline without citations.'}
      </p>
      <details className="rt-fold">
        <summary>TBHM coverage for this project ({tbhmDone}/{tbhmSections.length} sections closed)</summary>
        {tbhm.phases.map(phase => <div key={phase.phase}>
          <strong>{phase.title}</strong>
          <ul className="rt-list">{phase.sections.map(section => <li key={section.id}>
            <span className={`rt-dot ${TBHM_TONE[section.status || ''] || 'rt-idle'}`} /> {section.title}
            <span className="rt-muted"> · {TBHM_STATUS[section.status || 'todo'] || section.status} · {section.traffic} traffic</span>
          </li>)}</ul>
        </div>)}
      </details>
    </div>}
    {flows.length > 0 && <div aria-label="Existing PentAGI flows">
      <h3>Recent PentAGI flows</h3>
      <ul>{flows.map((flow, index) =>
        <li key={String(flow.id || index)}>
          {String(flow.title || 'Untitled flow')} — {String(flow.status || 'unknown')}
        </li>)}</ul>
    </div>}
    <p className="muted">
      Reference: <a href="https://github.com/jhaddix/tbhm" target="_blank" rel="noreferrer">The Bug Hunter's Methodology</a>.
      The native investigation reuses BugHunter settings. External PentAGI is a separate engine and requires independent model configuration.
    </p>
  </section>;
}
