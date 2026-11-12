import React, { useEffect, useState } from 'react';
import { rpc, asList, pretty } from './api';

type Bundle = {
  id: string; name: string; data_dir: string;
  counts?: Record<string, number>;
  domains?: Record<string, number>;
  include?: string[];
  created_at: string;
};

type Adapter = {
  id: string; name: string; domain: string;
  base_model: string; path: string; corpus_id?: string;
  kaggle_run?: string; status: string; created_at: string;
};

type KaggleRun = {
  id: string; corpus_id: string; status: string;
  base_model?: string; adapter_id?: string;
  last_status?: string; created_at: string;
};

export function ForgePanel() {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [adapters, setAdapters] = useState<Adapter[]>([]);
  const [kaggleRuns, setKaggleRuns] = useState<KaggleRun[]>([]);
  const [activeIds, setActiveIds] = useState<string[]>([]);
  const [busy, setBusy] = useState<string>('');
  const [log, setLog] = useState<string>('');

  // build form
  const [includeTraces, setIncludeTraces] = useState(true);
  const [includeErrors, setIncludeErrors] = useState(true);
  const [includeRepo, setIncludeRepo] = useState(true);
  const [includeDistill, setIncludeDistill] = useState(false);
  const [repoRoot, setRepoRoot] = useState('');
  const [bundleName, setBundleName] = useState('');

  // kaggle form
  const [kaggleUsername, setKaggleUsername] = useState('');
  const [kaggleKey, setKaggleKey] = useState('');
  const [kaggleNotebook, setKaggleNotebook] = useState('');
  const [kaggleDataset, setKaggleDataset] = useState('');
  const [kaggleOutDataset, setKaggleOutDataset] = useState('');
  const [kaggleConfigured, setKaggleConfigured] = useState(false);

  // training config
  const [selectedBundle, setSelectedBundle] = useState('');
  const [baseModel, setBaseModel] = useState('Qwen/Qwen2.5-Coder-7B-Instruct');
  const [rank, setRank] = useState(16);
  const [epochs, setEpochs] = useState(1);

  async function refresh() {
    setBundles(asList(await rpc<any>('forge.list')) as Bundle[]);
    setAdapters(asList(await rpc<any>('adapters.list')) as Adapter[]);
    const active = asList(await rpc<any>('adapters.active'));
    setActiveIds(active.map((a: any) => a.id));
    setKaggleRuns(asList(await rpc<any>('kaggle.list_runs')) as KaggleRun[]);
    const kcfg = await rpc<any>('kaggle.config');
    setKaggleConfigured(!!kcfg);
    if (kcfg) {
      setKaggleUsername(kcfg.username || '');
      setKaggleNotebook(kcfg.notebook_slug || '');
      setKaggleDataset(kcfg.dataset_slug || '');
      setKaggleOutDataset(kcfg.output_dataset_slug || '');
    }
  }

  useEffect(() => { refresh(); }, []);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(label); setLog('');
    try { const r = await fn(); setLog(pretty(r)); return r; }
    catch (e: any) { setLog(`Error: ${e?.message || e}`); return null; }
    finally { setBusy(''); refresh(); }
  }

  const includeList = () => [
    includeTraces && 'traces',
    includeErrors && 'errors',
    includeRepo && 'repo',
  ].filter(Boolean) as string[];

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      <h2>Learning Forge</h2>
      <p style={{ color: '#777' }}>
        Assemble a training corpus from past transcripts, errors, your source tree, and
        optional distilled Q/A. Training runs on Kaggle; adapters come back here.
      </p>

      <section style={{ marginTop: 16, border: '1px solid #ddd', padding: 12, borderRadius: 6 }}>
        <h3>1. Build corpus bundle</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <label><input type="checkbox" checked={includeTraces} onChange={e => setIncludeTraces(e.target.checked)} /> Past transcripts</label>
          <label><input type="checkbox" checked={includeErrors} onChange={e => setIncludeErrors(e.target.checked)} /> Error pairs (DPO)</label>
          <label><input type="checkbox" checked={includeRepo} onChange={e => setIncludeRepo(e.target.checked)} /> Your source tree</label>
          <label><input type="checkbox" checked={includeDistill} onChange={e => setIncludeDistill(e.target.checked)} /> Distilled Q/A</label>
        </div>
        {includeRepo && (
          <div style={{ marginTop: 8 }}>
            <input placeholder="Repo root path" value={repoRoot}
                   onChange={e => setRepoRoot(e.target.value)}
                   style={{ width: 400 }} />
          </div>
        )}
        <div style={{ marginTop: 8 }}>
          <input placeholder="Bundle name (optional)" value={bundleName}
                 onChange={e => setBundleName(e.target.value)} />
          <button disabled={!!busy}
                  onClick={() => run('build', () => rpc<any>('forge.build', {
                    include: includeList(), repo_root: repoRoot || undefined,
                    distill: includeDistill, name: bundleName || undefined,
                  }))}>Build bundle</button>
        </div>
      </section>

      <section style={{ marginTop: 16, border: '1px solid #ddd', padding: 12, borderRadius: 6 }}>
        <h3>2. Kaggle finetune</h3>
        {!kaggleConfigured && (
          <div style={{ display: 'grid', gap: 6, maxWidth: 600 }}>
            <input placeholder="Kaggle username" value={kaggleUsername}
                   onChange={e => setKaggleUsername(e.target.value)} />
            <input placeholder="Kaggle API key" type="password" value={kaggleKey}
                   onChange={e => setKaggleKey(e.target.value)} />
            <input placeholder="Notebook slug (e.g. you/bughunter-trainer)"
                   value={kaggleNotebook} onChange={e => setKaggleNotebook(e.target.value)} />
            <input placeholder="Corpus dataset slug (e.g. you/bughunter-corpus)"
                   value={kaggleDataset} onChange={e => setKaggleDataset(e.target.value)} />
            <input placeholder="Output dataset slug (e.g. you/bughunter-adapters)"
                   value={kaggleOutDataset} onChange={e => setKaggleOutDataset(e.target.value)} />
            <button disabled={!!busy}
                    onClick={() => run('configure', () => rpc<any>('kaggle.configure', {
                      username: kaggleUsername, api_key: kaggleKey,
                      notebook_slug: kaggleNotebook, dataset_slug: kaggleDataset,
                      output_dataset_slug: kaggleOutDataset,
                    }))}>Save Kaggle config</button>
          </div>
        )}
        {kaggleConfigured && (
          <>
            <p>Configured as <code>{kaggleUsername}</code>. Corpus → <code>{kaggleDataset}</code>, output → <code>{kaggleOutDataset}</code>.</p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
              <select value={selectedBundle} onChange={e => setSelectedBundle(e.target.value)}>
                <option value="">— choose bundle —</option>
                {bundles.map(b => (<option key={b.id} value={b.id}>{b.name} ({pretty(b.counts)})</option>))}
              </select>
              <button disabled={!selectedBundle || !!busy}
                      onClick={() => run('push', () => rpc<any>('kaggle.push_corpus',
                        { corpus_id: selectedBundle, approved: true }))}>Push corpus</button>
            </div>
            <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input placeholder="base model (hf id)" value={baseModel}
                     onChange={e => setBaseModel(e.target.value)} style={{ width: 300 }}/>
              <label>rank <input type="number" value={rank}
                                  onChange={e => setRank(Number(e.target.value))}
                                  style={{ width: 60 }}/></label>
              <label>epochs <input type="number" value={epochs}
                                   onChange={e => setEpochs(Number(e.target.value))}
                                   style={{ width: 60 }}/></label>
            </div>
            <table style={{ marginTop: 8, width: '100%', fontSize: 13 }}>
              <thead><tr><th>Run</th><th>Corpus</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>
                {kaggleRuns.map(r => (
                  <tr key={r.id}>
                    <td><code>{r.id.slice(-8)}</code></td>
                    <td><code>{r.corpus_id.slice(-8)}</code></td>
                    <td>{r.status} {r.last_status && `(${r.last_status})`}</td>
                    <td>
                      {r.status === 'uploaded' && (
                        <button disabled={!!busy}
                                onClick={() => run('launch', () => rpc<any>('kaggle.launch_training',
                                  { run_id: r.id, base_model: baseModel,
                                    rank, epochs, approved: true }))}>Launch</button>
                      )}
                      {' '}
                      <button disabled={!!busy}
                              onClick={() => run('status', () => rpc<any>('kaggle.status',
                                { run_id: r.id }))}>Poll</button>
                      {' '}
                      {r.status !== 'pulled' && (
                        <button disabled={!!busy}
                                onClick={() => {
                                  const name = prompt('Adapter name?', `adapter-${r.id.slice(-6)}`);
                                  if (!name) return;
                                  const dom = prompt('Domain?', 'web-audit') || 'web-audit';
                                  run('pull', () => rpc<any>('kaggle.pull_adapter',
                                    { run_id: r.id, name, domain: dom }));
                                }}>Pull adapter</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section style={{ marginTop: 16, border: '1px solid #ddd', padding: 12, borderRadius: 6 }}>
        <h3>3. Adapter registry</h3>
        <table style={{ width: '100%', fontSize: 13 }}>
          <thead>
            <tr><th>Active</th><th>Name</th><th>Domain</th><th>Base model</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {adapters.map(a => (
              <tr key={a.id}>
                <td>
                  <input type="checkbox"
                         checked={activeIds.includes(a.id)}
                         onChange={e => {
                           const next = e.target.checked
                             ? [...activeIds, a.id]
                             : activeIds.filter(x => x !== a.id);
                           setActiveIds(next);
                           rpc('adapters.activate', { ids: next }).then(refresh);
                         }}/>
                </td>
                <td>{a.name}</td>
                <td>{a.domain}</td>
                <td><code>{a.base_model}</code></td>
                <td>
                  <button disabled={!!busy}
                          onClick={() => run('delete-adapter',
                            () => rpc<any>('adapters.delete', { id: a.id }))}>Delete</button>
                </td>
              </tr>
            ))}
            {adapters.length === 0 && (
              <tr><td colSpan={5} style={{ color: '#777' }}>No adapters trained yet.</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <section style={{ marginTop: 16 }}>
        <h3>Last output</h3>
        <pre style={{ background: '#f5f5f5', padding: 8, maxHeight: 240, overflow: 'auto' }}>
          {busy ? `Running ${busy}…` : (log || '—')}
        </pre>
      </section>
    </div>
  );
}
