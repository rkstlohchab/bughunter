import { useEffect, useRef, useState } from 'react';
import { Download, Search } from 'lucide-react';
import { rpc } from './api';
import { Button, Field, Status } from './components';
import { ceMcpApi, isDesktop } from './ceMcpApi';
import './mapping-research.css';

type Reference = { id: string; title: string; url?: string; source_id?: string; locator?: string; kind: string };
type Observation = { check_id: string; at: string; value?: number; expected?: number; value_text?: string; expected_text?: string; matched?: boolean; error?: string; address?: string; build_sha256?: string };
type Candidate = { id: string; label: string; status: string; reference_id: string; quote: string; reason: string; verification_note?: string;
  recipe: { kind: string; value_type: string; base?: string; module_name?: string; rva?: string; offsets?: string[] };
  observations: Observation[]; scan_summary?: { candidate_count: number; partial: boolean; truncated: boolean } };
type Research = { id: string; program: string; query: string; status: string; progress: string; warnings: string[]; references: Reference[];
  events: { at: string; text: string }[]; candidates?: Candidate[]; verified_count?: number };
type Context = { pid: number; process_instance_id: string; program: string; build_sha256: string };
const running = (row: Research | null) => !!row && ['researching', 'suggesting'].includes(row.status);
const statuses: Record<string, string> = { value_verified: 'Verified for this process', matched_once: 'Matched once', proposed: 'Candidate',
  mismatch: 'Value mismatch', scan_ready: 'Scan ready', check_failed: 'Check failed', source_changed: 'Citation changed', needs_recheck: 'Needs recheck' };
const safeLink = (url?: string) => url && /^https?:\/\//i.test(url) ? url : undefined;

export default function MappingResearch({ projectId, connected = false, busy = false, native = true, initialQuery = '' }:
  { projectId?: string; connected?: boolean; busy?: boolean; native?: boolean; initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [program, setProgram] = useState('');
  const [links, setLinks] = useState('');
  const [searchWeb, setSearchWeb] = useState(true);
  const [history, setHistory] = useState<Research[]>([]);
  const [selected, setSelected] = useState('');
  const [job, setJob] = useState<Research | null>(null);
  const [context, setContext] = useState<Context | null>(null);
  const [expected, setExpected] = useState('');
  const [operation, setOperation] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const generation = useRef(0);
  const inFlight = useRef(false);
  const scope = `bughunter.mappings.${projectId || 'shared'}`;

  useEffect(() => {
    let alive = true;
    generation.current++;
    setHistory([]); setSelected(''); setJob(null); setError(''); setNotice(''); setContext(null); setExpected('');
    rpc<Research[]>('memory.mappings.list', { project_id: projectId || null }).then(rows => {
      if (!alive) return;
      setHistory(rows);
      const saved = localStorage.getItem(scope);
      setSelected(rows.find(r => r.id === saved)?.id || rows[0]?.id || '');
    }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; generation.current++; };
  }, [projectId, scope]);
  useEffect(() => { setQuery(initialQuery); }, [initialQuery]);
  useEffect(() => { if (!connected) setContext(null); }, [connected]);
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    localStorage.setItem(scope, selected);
    const poll = async () => {
      try {
        const row = await rpc<Research>('memory.mappings.get', { id: selected });
        if (!alive) return;
        setJob(row);
        setHistory(previous => previous.map(item => item.id === row.id ? row : item));
        if (running(row)) timer = setTimeout(() => void poll(), 1200);
      } catch (e) {
        if (alive) { setError(String(e)); timer = setTimeout(() => void poll(), 2500); }
      }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [selected, scope]);

  async function perform(label: string, action: (version: number) => Promise<void>) {
    if (inFlight.current) return;
    const version = generation.current;
    inFlight.current = true; setOperation(label); setError(''); setNotice('');
    try { await action(version); }
    catch (e) { if (version === generation.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { inFlight.current = false; setOperation(''); }
  }
  const useAttached = () => perform('Reading selected program…', async version => {
    const raw = await ceMcpApi.callTool('get_mapping_context');
    if (raw.startsWith('error:')) throw new Error(raw);
    const value = JSON.parse(raw) as Context;
    if (!value.pid || !value.process_instance_id || !value.build_sha256) throw new Error('No verified process identity. Attach a program in the memory helper first.');
    if (version !== generation.current) return;
    setContext(value); setProgram(value.program); setExpected('');
  });
  const start = () => perform('Starting research…', async version => {
    const row = await rpc<Research>('memory.mappings.start', { query, program, project_id: projectId || null,
      search_web: searchWeb, urls: links.split('\n').map(s => s.trim()).filter(Boolean) });
    if (version !== generation.current) return;
    setHistory(previous => [row, ...previous]); setSelected(row.id); setJob(row);
  });
  const check = (candidate: Candidate, resetScan = false) => perform('Checking and saving readback…', async version => {
    if (!context || !job) return;
    const challenge = await rpc<{ id: string }>('memory.mappings.check', { candidate_id: candidate.id,
      pid: context.pid, process_instance_id: context.process_instance_id, expected_value: expected, reset_scan: resetScan });
    if (version !== generation.current) return;
    // The native command loads the saved challenge and writes its receipt. No tool_result upload.
    await ceMcpApi.checkMapping(challenge.id);
    const row = await rpc<Research>('memory.mappings.observe', { check_id: challenge.id });
    if (version !== generation.current) return;
    setJob(row); setNotice('Readback saved with its citation. Change the visible value in the program, then check again.');
  });
  const exportResults = () => perform('Exporting saved evidence…', async version => {
    if (!job) return;
    const result = await rpc<{ content: string; filename: string }>('memory.mappings.export', { id: job.id });
    if (version !== generation.current) return;
    const url = URL.createObjectURL(new Blob([result.content], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = result.filename; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice('Saved evidence exported, including citations and check history.');
  });
  const disabled = !!operation || busy;
  const canCheck = isDesktop() && native && connected && !!context && !!expected.trim() && !disabled && job?.status === 'ready';
  const candidates = job?.candidates || [];
  return <section className="mapping-research" aria-label="Cited mapping research">
    <div className="section-title"><div><h2>Research a mapping</h2><p>References → candidates → program checks → saved evidence</p></div></div>
    <div className="mapping-inputs">
      <Field label="Program and build"><input value={program} maxLength={160} onChange={e => { setProgram(e.target.value); setContext(null); }} placeholder="Program name or version" /></Field>
      <Field label="What should we find?"><input value={query} maxLength={300} onChange={e => setQuery(e.target.value)} placeholder="For example, how the score is stored" /></Field>
    </div>
    <details><summary>Reference options</summary>
      <Field label="Reference links (one per line, up to three)"><textarea value={links} onChange={e => setLinks(e.target.value)} rows={3} /></Field>
      <label className="mapping-checkbox"><input type="checkbox" checked={searchWeb} onChange={e => setSearchWeb(e.target.checked)} />Search public web references as well as this project's library</label>
    </details>
    <div className="button-group wrap">
      <Button variant="primary" icon={Search} disabled={disabled || running(job) || program.trim().length < 2 || query.trim().length < 3} onClick={() => void start()}>Research references</Button>
      {isDesktop() && native && <Button disabled={!connected || disabled} onClick={() => void useAttached()}>Use attached program</Button>}
    </div>
    {!isDesktop() && <p className="muted">Research and saved citations work here. Open the desktop app to check a selected program.</p>}
    {isDesktop() && !native && <p className="muted">Program checks currently require the native Mac helper. Reference research remains available.</p>}
    {isDesktop() && native && !connected && <p className="muted">Start the memory helper and attach your program, then choose Use attached program.</p>}
    {operation && <p role="status">{operation}</p>}
    {error && <p role="alert" className="conversation-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {history.length > 0 && <Field label="Saved mapping research"><select value={selected} disabled={!!operation} onChange={e => { setJob(null); setSelected(e.target.value); setNotice(''); }}>
      {history.map(row => <option key={row.id} value={row.id}>{row.program} · {row.query}</option>)}
    </select></Field>}
    {job && <div className="mapping-results">
      <div className="section-title"><div><h3>{job.program} · {job.query}</h3><p role="status">{job.progress}</p></div><Status value={job.status} /></div>
      <ol className="mapping-steps" aria-label="Research stages">
        <li>1. References <strong>{job.references.length}</strong></li><li>2. Candidates <strong>{candidates.length}</strong></li>
        <li>3. Check program</li><li>4. Verified <strong>{job.verified_count || 0}</strong></li>
      </ol>
      {running(job) && <Button disabled={!!operation} onClick={() => void perform('Stopping research…', async version => {
        await rpc('memory.mappings.stop', { id: job.id });
        const row = await rpc<Research>('memory.mappings.get', { id: job.id });
        if (version === generation.current) setJob(row);
      })}>Stop research</Button>}
      {!!job.warnings.length && <ul className="mapping-warnings">{job.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
      {!!candidates.length && <div className="mapping-check-bar">
        <p>{context ? `Selected: ${context.program} · PID ${context.pid}` : 'Choose Use attached program before checking candidates.'}</p>
        <Field label="Value currently visible in the selected program" hint="Enter the observed number. Then change it in the program and check again.">
          <input value={expected} onChange={e => setExpected(e.target.value)} maxLength={60} inputMode="decimal" placeholder="Observed value" />
        </Field>
        <p className="muted">Verified means two distinct values matched in the same process and executable build. Meaning and stability after restart still require review.</p>
      </div>}
      <div className="mapping-candidates">{candidates.map(candidate => {
        const reference = job.references.find(item => item.id === candidate.reference_id);
        const last = candidate.observations.at(-1);
        return <article className="mapping-candidate" key={candidate.id} aria-label={candidate.label}>
          <div className="section-title"><h3>{candidate.label}</h3><strong className="mapping-verdict">{statuses[candidate.status] || candidate.status}</strong></div>
          <p>{candidate.reason}</p><p className="muted">{candidate.recipe.kind.replaceAll('_', ' ')} · {candidate.recipe.value_type}{candidate.recipe.base && ` · ${candidate.recipe.base}`}
            {candidate.recipe.module_name && ` · ${candidate.recipe.module_name} + ${candidate.recipe.rva}`}</p>
          <blockquote>{candidate.quote}</blockquote>
          <p className="mapping-citation">{safeLink(reference?.url) ? <a href={safeLink(reference?.url)} target="_blank" rel="noreferrer">{reference?.title}</a> : <span>{reference?.title || 'Missing reference'}</span>}
            {reference?.locator && ` · ${reference.locator}`}<small>{reference?.source_id || reference?.url || reference?.id} · {reference?.kind === 'search_excerpt' ? 'Search excerpt' : 'Library source'}</small></p>
          {candidate.scan_summary && <p>{candidate.scan_summary.candidate_count} scan candidates{candidate.scan_summary.partial || candidate.scan_summary.truncated ? ' · Limited scan coverage' : ''}</p>}
          {last && <p>{last.error || `Read ${last.value_text ?? last.value} · expected ${last.expected_text ?? last.expected} · ${last.address}`}</p>}
          {candidate.verification_note && <p className="muted">{candidate.verification_note}</p>}
          <div className="button-group wrap"><Button disabled={!canCheck || candidate.status === 'source_changed'} onClick={() => void check(candidate)}>
            {candidate.recipe.kind === 'value_scan' ? (candidate.scan_summary ? 'Narrow scan' : 'Check by scanning') : 'Check candidate'}</Button>
            {candidate.recipe.kind === 'value_scan' && candidate.scan_summary && <Button disabled={!canCheck} onClick={() => void check(candidate, true)}>Start fresh scan</Button>}</div>
          {!!candidate.observations.length && <details><summary>Saved observations ({candidate.observations.length})</summary><ul>{candidate.observations.map(o => <li key={o.check_id}>
            {new Date(o.at).toLocaleString()} · {o.error || `${o.value_text ?? o.value} / expected ${o.expected_text ?? o.expected} — ${o.matched ? 'matched' : 'mismatch'}`}
            {o.build_sha256 && <small>Executable SHA-256: {o.build_sha256}</small>}</li>)}</ul></details>}
        </article>;
      })}</div>
      <div className="button-group wrap"><Button icon={Download} disabled={!!operation} onClick={() => void exportResults()}>Export cited results</Button></div>
      {!!job.events.length && <details><summary>Research activity ({job.events.length})</summary><ol>{job.events.map((event, i) => <li key={i}>{event.text}</li>)}</ol></details>}
    </div>}
  </section>;
}
