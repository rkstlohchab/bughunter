import { useEffect, useState } from 'react';
import { ClipboardCopy, FileText, RefreshCw, Play } from 'lucide-react';
import { asList, rpc } from './api';
import type { RecordData as Row } from './api';
import { Button } from './components';

type ModelChoice = { provider: string; model: string; kind: string; local_only: boolean; ready: boolean };
type Connection = { configured: boolean; origin: string; detail: string; read_only: boolean; ai: ModelChoice };
type Plan = { prompt: string; methodology: { id: string; title: string; reference: string; checks: string[] }[] };

export default function PentagiBridgePanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [projects, setProjects] = useState<Row[]>([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [flows, setFlows] = useState<Row[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [nativeRun, setNativeRun] = useState<Row | null>(null);

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
      <p className="muted">{plan.methodology.length} planning stages, including cart, coupon, shipping, currency and checkout-state validation.</p>
      <textarea readOnly rows={15} value={plan.prompt}
        style={{ width: '100%', resize: 'vertical', boxSizing: 'border-box' }} aria-label="PentAGI handoff plan" />
      <Button icon={ClipboardCopy} onClick={() => void copyPlan()}>Copy plan</Button>
    </>}
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
