import React, { useEffect, useState } from 'react';
import { rpc, asList, pretty } from './api';

type Entry = { id: string; name: string; kind: string; notes?: string;
               updated_at: string };

export function VaultPanel() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [name, setName] = useState('');
  const [secret, setSecret] = useState('');
  const [kind, setKind] = useState('token');
  const [notes, setNotes] = useState('');
  const [log, setLog] = useState('');

  async function refresh() {
    setEntries(asList(await rpc<any>('vault.list')) as Entry[]);
  }

  useEffect(() => { refresh(); }, []);

  async function save() {
    try {
      await rpc('vault.put', { name, secret, kind, notes });
      setSecret(''); setName(''); setNotes('');
      setLog('Saved.');
    } catch (e: any) { setLog(`Error: ${e?.message || e}`); }
    refresh();
  }

  async function del(entryName: string) {
    if (!confirm(`Delete vault entry "${entryName}"?`)) return;
    await rpc('vault.delete', { name: entryName });
    refresh();
  }

  async function test(entryName: string) {
    const r = await rpc('vault.test', { name: entryName });
    setLog(pretty(r));
  }

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      <h2>Credential vault</h2>
      <p style={{ color: '#777', maxWidth: 800 }}>
        Secrets are stored in the macOS Keychain and never enter model
        context. Reference them in operator-mode tool arguments as
        <code> {'{{VAULT:name}}'}</code>.
      </p>

      <section style={{ marginTop: 12, border: '1px solid #ddd',
                          padding: 12, borderRadius: 6 }}>
        <h3>Add entry</h3>
        <div style={{ display: 'grid', gap: 6, maxWidth: 500 }}>
          <input placeholder="name (a-z 0-9 . _ -)" value={name}
                 onChange={e => setName(e.target.value)}/>
          <input placeholder="secret" type="password" value={secret}
                 onChange={e => setSecret(e.target.value)}/>
          <select value={kind} onChange={e => setKind(e.target.value)}>
            <option value="token">token</option>
            <option value="password">password</option>
            <option value="api_key">api_key</option>
            <option value="ssh_key">ssh_key</option>
          </select>
          <input placeholder="notes (optional)" value={notes}
                 onChange={e => setNotes(e.target.value)}/>
          <button disabled={!name || !secret} onClick={save}>Save</button>
        </div>
      </section>

      <section style={{ marginTop: 12 }}>
        <h3>Entries</h3>
        <table style={{ width: '100%', fontSize: 13 }}>
          <thead><tr><th>Name</th><th>Kind</th><th>Updated</th><th>Notes</th><th></th></tr></thead>
          <tbody>
            {entries.map(e => (
              <tr key={e.id}>
                <td><code>{e.name}</code></td>
                <td>{e.kind}</td>
                <td>{e.updated_at}</td>
                <td>{e.notes || '—'}</td>
                <td>
                  <button onClick={() => test(e.name)}>Test</button>
                  {' '}
                  <button onClick={() => del(e.name)}>Delete</button>
                </td>
              </tr>
            ))}
            {entries.length === 0 &&
              <tr><td colSpan={5} style={{ color: '#777' }}>Empty.</td></tr>}
          </tbody>
        </table>
      </section>

      <section style={{ marginTop: 12 }}>
        <h3>Log</h3>
        <pre style={{ background: '#f5f5f5', padding: 8 }}>{log || '—'}</pre>
      </section>
    </div>
  );
}
