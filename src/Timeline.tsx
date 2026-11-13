import { useEffect, useState } from 'react';
import { rpc, asList, pretty } from './api';

type Event = {
  id: string; kind: string; phase: string;
  run_id?: string; tool?: string; payload?: any;
  outcome?: string; duration_ms?: number;
  ts: string; ts_epoch: number;
};

export function Timeline() {
  const [events, setEvents] = useState<Event[]>([]);
  const [kind, setKind] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Event | null>(null);

  async function refresh() {
    const list = asList(await rpc<any>('timeline.list',
                        { kind: kind || undefined, limit: 300 })) as Event[];
    setEvents(list);
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [kind]);

  async function doSearch() {
    if (!search) return refresh();
    const list = asList(await rpc<any>('timeline.search',
                        { query: search, limit: 200 })) as Event[];
    setEvents(list);
  }

  const color = (k: string) => ({
    adapter: '#2b6cb0',
    shell: '#805ad5',
    http: '#2f855a',
    model_turn: '#d69e2e',
    approval: '#dd6b20',
    workflow: '#718096',
    code_exec: '#9b2c2c',
  } as any)[k] || '#4a5568';

  return (
    <div style={{ padding: 16, display: 'grid',
                   gridTemplateColumns: '1fr 400px', gap: 12,
                   fontFamily: 'system-ui, sans-serif' }}>
      <div>
        <h2>Timeline</h2>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <select value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">all kinds</option>
            {['adapter','shell','http','model_turn','approval','workflow','code_exec']
              .map(k => <option key={k}>{k}</option>)}
          </select>
          <input placeholder="search payload" value={search}
                 onChange={e => setSearch(e.target.value)}
                 onKeyDown={e => e.key === 'Enter' && doSearch()}/>
          <button onClick={doSearch}>Search</button>
          <button onClick={refresh}>Latest</button>
        </div>
        <div style={{ maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' }}>
          {events.map(e => (
            <div key={e.id}
                 onClick={() => setSelected(e)}
                 style={{ borderLeft: `4px solid ${color(e.kind)}`,
                          padding: '6px 8px', cursor: 'pointer',
                          background: selected?.id === e.id ? '#edf2f7' : 'transparent',
                          borderBottom: '1px solid #f0f0f0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <b>{e.kind}</b>
                <span style={{ color: '#888', fontSize: 12 }}>{e.ts}</span>
              </div>
              <div style={{ fontSize: 13 }}>
                <span style={{ padding: '0 6px', borderRadius: 3,
                                background: '#e2e8f0', fontSize: 11 }}>{e.phase}</span>
                {' '}
                <code>{e.tool || '—'}</code>
                {' '}
                {e.outcome && <em style={{ color: '#718096' }}>{e.outcome}</em>}
                {e.duration_ms !== undefined && (
                  <span style={{ color: '#aaa' }}> · {e.duration_ms}ms</span>
                )}
              </div>
            </div>
          ))}
          {events.length === 0 && <p style={{ color: '#777' }}>No events.</p>}
        </div>
      </div>
      <div style={{ borderLeft: '1px solid #e2e8f0', paddingLeft: 12 }}>
        <h3>Detail</h3>
        {selected ? (
          <>
            <div><b>ID:</b> <code>{selected.id}</code></div>
            <div><b>Run:</b> <code>{selected.run_id || '—'}</code></div>
            <div><b>Kind/phase:</b> {selected.kind} / {selected.phase}</div>
            <div><b>Tool:</b> <code>{selected.tool || '—'}</code></div>
            <div><b>Outcome:</b> {selected.outcome || '—'}</div>
            <div><b>Timestamp:</b> {selected.ts}</div>
            <h4 style={{ marginTop: 12 }}>Payload</h4>
            <pre style={{ background: '#f5f5f5', padding: 8,
                           maxHeight: 400, overflow: 'auto', fontSize: 12 }}>
              {pretty(selected.payload)}
            </pre>
          </>
        ) : <p style={{ color: '#777' }}>Pick an event.</p>}
      </div>
    </div>
  );
}
