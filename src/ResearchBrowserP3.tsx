import {useCallback, useEffect, useState} from 'react';
import {Button, Status} from './components';
import {rpc} from './api';
import type {RecordData} from './api';

type Session = RecordData & {
  id: string;
  source_title: string;
  status: string;
  blocker?: string | null;
  error?: string | null;
  max_pages: number;
  visited_count: number;
  captured_ids: string[];
  queued: number;
  next_url?: string;
  last_url?: string;
  history?: {at: string; message: string}[];
};

type Props = { projectId?: string; sourceId?: string; onCaptured?: () => Promise<void> };

export default function ResearchBrowserP3({projectId, sourceId, onCaptured}: Props) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [limit, setLimit] = useState(10);
  const [depth, setDepth] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const load = useCallback(async () => {
    const found = await rpc<Session[]>('tor.p3.list', {project_id: projectId || '*'});
    setSessions(found);
  }, [projectId]);
  useEffect(() => { void load().catch(e => setMessage(String(e))); }, [load]);
  useEffect(() => {
    if (!selectedId) return;
    const handle = setInterval(() => { void load().catch(() => undefined); }, 5000);
    return () => clearInterval(handle);
  }, [load, selectedId]);
  async function action(label: string, fn: () => Promise<Session>) {
    setBusy(true); setMessage('');
    try {
      const result = await fn();
      setSelectedId(result.id);
      await load();
      if (result.captured_ids?.length) await onCaptured?.();
      setMessage(label + ' · ' + result.status.replaceAll('_', ' '));
    } catch (error) {
      setMessage(String(error));
    } finally { setBusy(false); }
  }
  const selected = sessions.find(s => s.id === selectedId) || sessions.find(s =>
    s.status === 'waiting_for_human' || s.status === 'running' || s.status === 'interrupted' || s.status === 'ready'
  );
  const active = sessions.some(s => ['ready','running','waiting_for_human','interrupted'].includes(s.status));
  const completed = Number(selected?.visited_count || 0);
  const total = Number(selected?.max_pages || 1);
  return <section className="surface">
    <div className="section-title"><div><h2>P3 · Research browser</h2><p>Capture public-web or onion pages from a saved source through Tor. Review each capture before it enters Knowledge.</p></div></div>
    <div className="form-grid">
      <label>Pages to read (1–25) <input type="number" min="1" max="25" value={limit} onChange={e => setLimit(Number(e.target.value))}/></label>
      <label>Same-site link depth (0–2) <select value={depth} onChange={e => setDepth(Number(e.target.value))}><option value="0">Only this page</option><option value="1">Linked pages</option><option value="2">Two levels</option></select></label>
    </div>
    <div className="button-group wrap">
      <Button variant="primary" disabled={busy || active || !sourceId || limit < 1 || limit > 25} onClick={() => action('Research started', () => rpc<Session>('tor.p3.start', {source_id: sourceId, max_pages: limit, max_depth: depth}))}>Create research session</Button>
      <Button disabled={busy} onClick={() => void load().catch(e => setMessage(String(e)))}>Refresh</Button>
    </div>
    {!sourceId && <p className="muted">Select a saved research source above to start. Browser paths and Tor connection are configured separately.</p>}
    {sessions.length > 0 && <label className="field">Research session <select aria-label="Research session" value={selected?.id || ''} onChange={e => setSelectedId(e.target.value)}>{sessions.map(s => <option key={s.id} value={s.id}>{s.source_title || 'Research source'} · {s.status.replaceAll('_', ' ')} · {s.visited_count}/{s.max_pages}</option>)}</select></label>}
    {selected && <div className="training-example">
      <div className="row-between"><strong>{selected.source_title || 'Research session'}</strong><Status value={selected.status === 'completed' ? 'completed' : selected.status === 'waiting_for_human' ? 'needs_review' : selected.status}/></div>
      <p className="muted">{completed} captured · up to {total} pages · {selected.queued} queued</p>
      <progress style={{width: '100%'}} max={total} value={completed}/>
      {selected.next_url && <p className="muted">Next: {selected.next_url}</p>}
      {selected.status === 'waiting_for_human' && <p role="status"><strong>Needs you:</strong> The browser encountered {selected.blocker || 'an interaction'}. Complete any login or CAPTCHA yourself in the visible browser, then select Resume. BugHunter does not solve or submit challenges.</p>}
      {selected.status === 'interrupted' && <p role="status">Research paused. Check the Tor/Browser connection and resume. {selected.error || selected.blocker}</p>}
      <div className="button-group wrap">
        <Button disabled={busy || !['ready','running'].includes(selected.status)} onClick={() => action('Captured next page', () => rpc<Session>('tor.p3.advance',{id:selected.id,steps:1}))}>Read next page</Button>
        <Button disabled={busy || !['ready','running'].includes(selected.status)} onClick={() => action('Processed up to 3 pages', () => rpc<Session>('tor.p3.advance',{id:selected.id,steps:3}))}>Read up to 3 pages</Button>
        <Button disabled={busy || !['waiting_for_human','interrupted'].includes(selected.status)} onClick={() => action('Resumed research', () => rpc<Session>('tor.p3.resume',{id:selected.id}))}>Resume after intervention</Button>
        <Button disabled={busy || ['completed','stopped'].includes(selected.status)} onClick={() => action('Stopped', () => rpc<Session>('tor.p3.stop',{id:selected.id}))}>Stop</Button>
      </div>
      {(selected.history || []).length > 0 && <details><summary>Recent actions</summary><div className="training-stack">{(selected.history || []).slice(-8).map((event,i) => <p className="muted" key={i}>{event.at} · {event.message}</p>)}</div></details>}
    </div>}
    {message && <p role="status" className="muted">{message}</p>}
  </section>;
}

export function ResearchP3Overview({projectId, onOpen}: {projectId?: string; onOpen: () => void}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  useEffect(() => {
    let live = true;
    const refresh = async () => {
      try {
        const list = await rpc<Session[]>('tor.p3.list', {project_id: projectId || '*'});
        if (live) setSessions(list);
      } catch { /* Report issues in the full research panel. */ }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 30000);
    return () => { live = false; clearInterval(timer); };
  }, [projectId]);
  const waiting = sessions.filter(s => s.status === 'waiting_for_human');
  const inProgress = sessions.filter(s => ['ready','running','interrupted'].includes(s.status));
  return <details className="surface course-experiment-overview">
    <summary><strong>Research browser</strong> · {waiting.length} need attention · {inProgress.length} in progress</summary>
    <p className="muted">Public-web and onion research collections. Pages are unreviewed until you approve each captured source.</p>
    {waiting.map(s => <p key={s.id}>Needs you: {s.source_title} · {s.blocker}</p>)}
    {inProgress.map(s => <p key={s.id}>{s.source_title} · {s.visited_count} pages captured · {s.status.replaceAll('_',' ')}</p>)}
    <Button onClick={onOpen}>Open research browser</Button>
  </details>;
}
