import { useEffect, useState } from 'react';
import { rpc, asList, pretty } from './api';

type Run = { id: string; status: string; prompt: string; objective: string;
             turns_done?: number; updated_at: string };
type Approval = { id: string; run_id: string; tool: string; args: any;
                  status: string; created_at: string };

export function OperatorMode() {
  const [enabled, setEnabled] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [prompt, setPrompt] = useState('');
  const [objective, setObjective] = useState('');
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [transcript, setTranscript] = useState<any[]>([]);
  const [busy, setBusy] = useState('');
  const [log, setLog] = useState('');

  async function refresh() {
    setEnabled(!!(await rpc('operator.enabled')));
    setRuns(asList(await rpc('operator.list')) as Run[]);
    // approvals are typed entities; list via dispatch-free fallback
    try {
      const data = await rpc<any>('operator.pending_approvals');
      setApprovals(asList(data) as Approval[]);
    } catch { /* method is optional */ }
  }

  useEffect(() => { refresh(); const t = setInterval(refresh, 4000);
                    return () => clearInterval(t); }, []);

  async function act<T>(label: string, fn: () => Promise<T>) {
    setBusy(label); setLog('');
    try { const r = await fn(); setLog(pretty(r)); return r; }
    catch (e: any) { setLog(`Error: ${e?.message || e}`); return null; }
    finally { setBusy(''); refresh(); }
  }

  async function openRun(r: Run) {
    setSelectedRun(r);
    const full = await rpc<any>('operator.get', { id: r.id });
    setTranscript(full?.transcript || []);
  }

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ margin: 0 }}>Operator mode</h2>
        <span style={{ padding: '2px 8px', borderRadius: 4,
                       background: enabled ? '#b30000' : '#666',
                       color: 'white', fontSize: 12 }}>
          {enabled ? 'ACTIVE' : 'off'}
        </span>
        <button onClick={() => enabled
          ? act('disable', () => rpc('operator.disable'))
          : act('enable', () => rpc('operator.enable', { approved: true }))}>
          {enabled ? 'Disable' : 'Enable (approved)'}
        </button>
      </div>

      <p style={{ color: '#777', maxWidth: 800 }}>
        Operator mode gives the agent the unbounded tool surface
        (nmap, httpx, ffuf, nuclei, frida, ghidra, radare2, hashcat,
        hydra, impacket, bloodhound, adb, shell, http, file I/O,
        code-exec sandbox). Every tool call is logged in the timeline
        and parks for approval unless it's on the auto-readonly list.
        The investigation agent is untouched.
      </p>

      {enabled && (
        <section style={{ marginTop: 12, border: '1px solid #ddd',
                          padding: 12, borderRadius: 6 }}>
          <h3>Start an operator run</h3>
          <textarea placeholder="Prompt — what the operator should do"
                    value={prompt} onChange={e => setPrompt(e.target.value)}
                    rows={3} style={{ width: '100%' }}/>
          <input placeholder="Objective (short label)"
                 value={objective} onChange={e => setObjective(e.target.value)}
                 style={{ width: 300, marginTop: 6 }}/>
          <div style={{ marginTop: 6 }}>
            <button disabled={!prompt || !!busy}
                    onClick={() => act('start', () =>
                      rpc('operator.start',
                        { prompt, objective, approved: true }))}>
              Start operator run
            </button>
          </div>
        </section>
      )}

      <section style={{ marginTop: 12 }}>
        <h3>Pending approvals</h3>
        {approvals.length === 0
          ? <p style={{ color: '#777' }}>No approvals pending.</p>
          : approvals.map(a => (
              <div key={a.id} style={{ border: '1px solid #e6a800',
                                         borderRadius: 6, padding: 8, marginBottom: 6 }}>
                <div><b>{a.tool}</b> on run <code>{a.run_id.slice(-6)}</code></div>
                <pre style={{ background: '#fff8e1', padding: 6,
                               overflow: 'auto', maxHeight: 180 }}>
                  {pretty(a.args)}
                </pre>
                <button onClick={() => act('approve',
                  () => rpc('operator.approve_call',
                    { id: a.id, approved: true }))}>Approve</button>
                {' '}
                <button onClick={() => act('deny',
                  () => rpc('operator.approve_call',
                    { id: a.id, approved: false }))}>Deny</button>
              </div>
            ))}
      </section>

      <section style={{ marginTop: 12 }}>
        <h3>Runs</h3>
        <table style={{ width: '100%', fontSize: 13 }}>
          <thead><tr><th>ID</th><th>Status</th><th>Turns</th><th>Prompt</th><th></th></tr></thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.id}>
                <td><code>{r.id.slice(-8)}</code></td>
                <td>{r.status}</td>
                <td>{r.turns_done ?? 0}</td>
                <td style={{ maxWidth: 400, overflow: 'hidden',
                              textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.prompt}
                </td>
                <td>
                  <button onClick={() => openRun(r)}>Open</button>
                  {' '}
                  <button onClick={() => act('stop',
                    () => rpc('operator.stop', { id: r.id }))}>Stop</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {selectedRun && (
        <section style={{ marginTop: 12, border: '1px solid #ddd',
                            padding: 12, borderRadius: 6 }}>
          <h3>Transcript — {selectedRun.id.slice(-8)}</h3>
          <div style={{ maxHeight: 400, overflow: 'auto',
                         background: '#fafafa', padding: 8, fontFamily: 'monospace' }}>
            {transcript.map((m, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <b>{m.role}:</b>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                  {typeof m.content === 'string' ? m.content : pretty(m.content)}
                </pre>
              </div>
            ))}
          </div>
        </section>
      )}

      <section style={{ marginTop: 12 }}>
        <h3>Last action</h3>
        <pre style={{ background: '#f5f5f5', padding: 8,
                       maxHeight: 200, overflow: 'auto' }}>
          {busy ? `Running ${busy}…` : (log || '—')}
        </pre>
      </section>
    </div>
  );
}
