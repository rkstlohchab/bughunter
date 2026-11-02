import { useEffect, useState } from 'react';
import { ArrowRight, FlaskConical, Play, Square, ShieldCheck } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Status } from './components';

type Props = { navigate: (page: any) => void; selectProject?: (id: string) => Promise<void> };
const active = (s: string) => ['starting', 'running'].includes(s);

export default function CartLabPanel({ navigate, selectProject }: Props) {
  const [job, setJob] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!job?.id || !active(String(job.status))) return;
    const interval = setInterval(() => {
      void rpc<Row>('tasks.get', { id: job.id }).then(setJob).catch(e => setError(String(e)));
    }, 1300);
    return () => clearInterval(interval);
  }, [job?.id, job?.status]);
  async function run() {
    setBusy(true); setError('');
    try {
      const started = await rpc<Row>('commerce.lab.run', {approved: true});
      setJob(started);
      if (started.project_id) await selectProject?.(started.project_id);
    }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!job?.id) return;
    setBusy(true); setError('');
    try { setJob(await rpc<Row>('commerce.lab.stop', {id: job.id})); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  const result: Row = job?.result || {};
  const cases: Row[] = result.cases || [];
  return <section className="surface cart-audit">
    <div className="section-title">
      <div><h2><FlaskConical size={18}/> Automated cart security lab</h2>
        <p>One click runs real HTTP tests against the application's own disposable shop. It compares a vulnerable example against a fixed control, captures evidence, and creates draft findings.</p></div>
      {job && <Status value={job.status}/>}
    </div>
    <div className="cart-audit-actions">
      <div className="cart-audit-contract"><ShieldCheck size={20}/><span>Runs only on <strong>127.0.0.1</strong> using 10 predefined test requests. No live websites, payments, or external transactions.</span></div>
      {!job || !active(String(job.status)) ? <Button variant="primary" icon={Play} disabled={busy} onClick={() => void run()}>{busy ? 'Starting…' : 'Run automatic lab audit'}</Button> : <Button icon={Square} disabled={busy} onClick={() => void stop()}>Stop</Button>}
    </div>
    {error && <p role="alert" className="conversation-error">{error}</p>}
    {job && <div className="cart-audit-results" aria-live="polite">
      <div className="cart-audit-stat"><b>{cases.length} / 10</b><small>Requests observed</small></div>
      <div className="cart-audit-stat"><b>{result.observed_failures ?? 0}</b><small>Lab failures reproduced</small></div>
      <div className="cart-audit-stat"><b>{result.passed_controls ?? 0}</b><small>Fixed controls passed</small></div>
      <Button icon={ArrowRight} onClick={() => navigate('Team')}>Open task history</Button>
      {job.status === 'completed' && <Button onClick={() => navigate('Findings')}>Review draft findings</Button>}
    </div>}
    {job?.error && <p role="alert" className="conversation-error">{job.error}</p>}
    <p className="muted">For a real store, use a project with documented authorization, captured staging requests, and per-request approval. This lab runner cannot be redirected to arbitrary websites.</p>
  </section>;
}
