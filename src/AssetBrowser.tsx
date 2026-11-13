import { useEffect, useState } from 'react';
import { rpc, asList } from './api';

type Host = { id: string; ip?: string; hostname?: string; os_hint?: string;
              project_id?: string; last_seen?: string };
type Service = { id: string; host_id: string; port: number; proto: string;
                 name?: string; banner?: string; last_seen?: string };
type Credential = { id: string; type: string; user?: string;
                    host_id?: string; service_id?: string; source?: string };
type Loot = { id: string; kind: string; path: string; sha256?: string;
              host_id?: string; notes?: string };

export function AssetBrowser() {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [creds, setCreds] = useState<Credential[]>([]);
  const [loot, setLoot] = useState<Loot[]>([]);
  const [selected, setSelected] = useState<Host | null>(null);
  const [query, setQuery] = useState('');

  async function refresh() {
    setHosts(asList(await rpc<any>('assets.hosts')) as Host[]);
  }

  async function loadDetail(h: Host) {
    setSelected(h);
    setServices(asList(await rpc<any>('assets.services',
                { host_id: h.id })) as Service[]);
    setCreds(asList(await rpc<any>('assets.credentials',
                { host_id: h.id })) as Credential[]);
    setLoot(asList(await rpc<any>('assets.loot',
                { host_id: h.id })) as Loot[]);
  }

  useEffect(() => { refresh(); }, []);

  async function doSearch() {
    if (!query) return refresh();
    const r = await rpc<any>('assets.find', { query });
    setHosts(asList(r?.hosts) as Host[]);
  }

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      <h2>Asset browser</h2>
      <div style={{ display: 'flex', gap: 8 }}>
        <input placeholder="search ip / hostname / banner / notes"
               value={query} onChange={e => setQuery(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && doSearch()}
               style={{ width: 400 }}/>
        <button onClick={doSearch}>Search</button>
        <button onClick={refresh}>Reset</button>
      </div>
      <div style={{ display: 'grid',
                     gridTemplateColumns: '1fr 2fr', gap: 12, marginTop: 12 }}>
        <div>
          <h3>Hosts ({hosts.length})</h3>
          <div style={{ maxHeight: 'calc(100vh - 220px)', overflowY: 'auto' }}>
            {hosts.map(h => (
              <div key={h.id}
                   onClick={() => loadDetail(h)}
                   style={{ padding: 8, cursor: 'pointer',
                            borderBottom: '1px solid #eee',
                            background: selected?.id === h.id
                              ? '#e2e8f0' : 'transparent' }}>
                <div><b>{h.hostname || h.ip || h.id}</b></div>
                <div style={{ fontSize: 12, color: '#777' }}>
                  <code>{h.ip || ''}</code> {h.os_hint && `· ${h.os_hint}`}
                </div>
              </div>
            ))}
            {hosts.length === 0 && <p style={{ color: '#777' }}>No hosts.</p>}
          </div>
        </div>
        <div>
          {selected ? (
            <>
              <h3>{selected.hostname || selected.ip}</h3>
              <h4>Services</h4>
              <table style={{ width: '100%', fontSize: 13 }}>
                <thead><tr><th>Port</th><th>Proto</th><th>Name</th><th>Banner</th></tr></thead>
                <tbody>
                  {services.map(s => (
                    <tr key={s.id}>
                      <td>{s.port}</td><td>{s.proto}</td>
                      <td>{s.name || '—'}</td>
                      <td style={{ maxWidth: 400, overflow: 'hidden',
                                     textOverflow: 'ellipsis',
                                     whiteSpace: 'nowrap' }}>{s.banner}</td>
                    </tr>
                  ))}
                  {services.length === 0 &&
                    <tr><td colSpan={4} style={{ color: '#777' }}>No services.</td></tr>}
                </tbody>
              </table>
              <h4 style={{ marginTop: 12 }}>Credentials ({creds.length})</h4>
              <ul>
                {creds.map(c => (
                  <li key={c.id}>
                    <code>{c.type}</code> {c.user ? `· user: ${c.user}` : ''}
                    {c.source ? ` · via ${c.source}` : ''}
                  </li>
                ))}
              </ul>
              <h4 style={{ marginTop: 12 }}>Loot ({loot.length})</h4>
              <ul>
                {loot.map(l => (
                  <li key={l.id}>
                    <b>{l.kind}</b> · <code>{l.path}</code>
                    {l.sha256 && <span style={{ color: '#888' }}> ({l.sha256.slice(0, 10)})</span>}
                    {l.notes && <div style={{ fontSize: 12, color: '#777' }}>{l.notes}</div>}
                  </li>
                ))}
              </ul>
            </>
          ) : <p style={{ color: '#777' }}>Pick a host.</p>}
        </div>
      </div>
    </div>
  );
}
