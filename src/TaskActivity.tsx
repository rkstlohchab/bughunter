import { useEffect, useState } from 'react';
import { Activity, Download, LoaderCircle, Square } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Status } from './components';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'blocked']);
const RUNNING = new Set(['starting', 'running', 'queued', 'cancelling']);
const STOPPABLE = new Set(['coding_agents', 'web_investigation', 'study', 'tor_capture', 'orchestrated', 'cart_lab', 'website_audit']);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const clock = (value: unknown) => {
  if (!value) return '';
  const date = new Date(typeof value === 'number' ? value * 1000 : String(value));
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

export function downloadRecord(filename: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A recorded task is the source of truth, including linked agent/run state. */
export default function TaskActivity({ taskId, onOpenTasks }: { taskId: string; onOpenTasks: () => void }) {
  const [task, setTask] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [revision, setRevision] = useState(0);
  const [archive, setArchive] = useState<Row[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setTask(null); setError(''); setArchive([]); setNextOffset(0);
    const load = async () => {
      let delay = 2500;
      let again = true;
      try {
        const value = await rpc<Row>('tasks.get', { id: taskId });
        if (!alive) return;
        setTask(value); setError('');
        again = !TERMINAL.has(String(value.status));
        delay = RUNNING.has(String(value.status)) ? 1500 : 5000;
      } catch (err) {
        if (!alive) return;
        setError(`Live updates interrupted: ${message(err)}. Retrying…`);
      }
      if (alive && again) timer = setTimeout(load, delay);
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, [taskId, revision]);

  async function stop() {
    setBusy('stop');
    try { await rpc('tasks.cancel', { id: taskId }); setRevision(v => v + 1); }
    catch (err) { setError(`Could not stop task: ${message(err)}`); }
    finally { setBusy(''); }
  }
  async function olderEvents() {
    if (nextOffset === null) return;
    setBusy('history');
    try {
      const page = await rpc<Row>('tasks.events', { id: taskId, offset: nextOffset, limit: 100 });
      setArchive(previous => [...previous, ...(page.events || [])]);
      setNextOffset(page.next_offset ?? null);
    } catch (err) { setError(`Could not read saved activity: ${message(err)}`); }
    finally { setBusy(''); }
  }
  async function exportTask() {
    if (!task) return;
    setBusy('export');
    try {
      let events = task.events || [];
      if (task.events_truncated) {
        events = [];
        let offset: number | null = 0;
        while (offset !== null) {
          const page: Row = await rpc('tasks.events', { id: taskId, offset, limit: 100 });
          events.push(...(page.events || []));
          const next = page.next_offset ?? null;
          if (next !== null && next <= offset) throw new Error('Saved activity cursor did not advance.');
          offset = next;
        }
      }
      downloadRecord(`task-${taskId}.json`, { ...task, events, exported_at: new Date().toISOString() });
    } catch (err) { setError(`Could not export task: ${message(err)}`); }
    finally { setBusy(''); }
  }

  const events: Row[] = task?.events || [];
  const latest = events.at(-1);
  const summary = task?.summary || task?.result?.summary || task?.result?.report || task?.result?.message;
  const working = RUNNING.has(String(task?.status));
  return <section className="chat-task-activity" aria-label="Task progress and results">
    <div className="chat-task-heading" role="status">
      {working || !task ? <LoaderCircle className="spin" size={16}/> : <Activity size={16}/>}
      <strong>{task?.title || 'Task activity'}</strong>
      {task && <Status value={task.status}/>}
    </div>
    {error && <p className="chat-task-error" role="alert">{error}</p>}
    {task?.error && <p className="chat-task-error" role="alert">{task.error}</p>}
    {latest && <p className="chat-task-latest" role="status">{String(latest.text || latest.content || '').slice(0, 400)}</p>}
    {task?.workers && <div className="chat-task-workers">{Object.entries(task.workers).map(([name, worker]) => <span key={name}>{name}: <strong>{String((worker as Row).status || 'unknown').replaceAll('_', ' ')}</strong></span>)}</div>}
    {task?.approvals?.length > 0 && <p className="chat-task-latest">{task?.approvals.length} request(s) need your review.</p>}
    {summary && <div className="chat-task-summary"><strong>Recorded outcome</strong><p>{typeof summary === 'string' ? summary : JSON.stringify(summary, null, 2)}</p></div>}
    <div className="chat-task-actions">
      <Button onClick={onOpenTasks}>{task?.approvals?.length ? 'Review requests' : 'All saved tasks'}</Button>
      {task && <Button icon={Download} disabled={!!busy} onClick={() => void exportTask()}>{busy === 'export' ? 'Saving…' : 'Save report'}</Button>}
      {task && STOPPABLE.has(task.kind) && working && task.status !== 'cancelling' && <Button icon={Square} disabled={!!busy} onClick={() => void stop()}>{busy === 'stop' ? 'Stopping…' : 'Stop task'}</Button>}
    </div>
    {!!events.length && <details className="chat-task-details"><summary>Activity ({task?.event_count ?? events.length})</summary>
      <ol>{events.map((event, index) => <li key={event.id || index}><div><strong>{event.agent || String(event.kind || event.type || 'Update').replaceAll('_', ' ')}</strong><time>{clock(event.time || event.created_at)}</time></div><pre>{String(event.text || event.content || '')}</pre></li>)}</ol>
      {task?.events_truncated && <><p className="muted">Showing recent activity. Earlier work is saved.</p>{archive.length > 0 && <ol aria-label="Earlier saved activity">{archive.map((event, index) => <li key={event.id || index}><strong>{event.agent || event.kind || 'Update'}</strong><pre>{String(event.text || event.content || '')}</pre></li>)}</ol>}{nextOffset !== null && <Button disabled={!!busy} onClick={() => void olderEvents()}>Load saved activity</Button>}</>}
    </details>}
    {task?.result && <details className="chat-task-details"><summary>Evidence and results</summary><pre>{JSON.stringify(task.result, null, 2)}</pre></details>}
  </section>;
}
