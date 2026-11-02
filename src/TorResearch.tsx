import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Globe2, Monitor, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { rpc } from './api';
import ResearchBrowserP3 from './ResearchBrowserP3';
import type { RecordData as Row } from './api';
import { Button, Empty, Field, Status } from './components';

type Run = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
export default function TorResearch({ perform, revision, projectId }: { perform: Run; revision: number; projectId?: string }) {
  const [sources, setSources] = useState<Row[]>([]);
  const [pages, setPages] = useState<Row[]>([]);
  const [selected, setSelected] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [tags, setTags] = useState('');
  const [port, setPort] = useState('9050');
  const [allowRemoteAI, setAllowRemoteAI] = useState(false);
  const [browserBinary, setBrowserBinary] = useState('');
  const [geckoPath, setGeckoPath] = useState('');
  const [allowScripts, setAllowScripts] = useState(false);
  const [browserState, setBrowserState] = useState<Row | null>(null);
  const [browserPage, setBrowserPage] = useState<Row | null>(null);
  const [browserTarget, setBrowserTarget] = useState('');
  const [torStatus, setTorStatus] = useState<Row | null>(null);
  const [count, setCount] = useState(1);
  const [depth, setDepth] = useState(0);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Row[]>([]);
  const [feedback, setFeedback] = useState('');
  const reload = useCallback(async () => {
    const [list, settings, config, session] = await Promise.all([rpc<Row[]>('tor.sources'), rpc<Row>('tor.settings'), rpc<Row>('tor.browser.config'), rpc<Row>('tor.browser.status')]);
    setSources(list.filter(item=>!projectId || !item.project_id || item.project_id===projectId));
    setBrowserBinary(String(config.binary_path || ''));
    setGeckoPath(String(config.driver_path || ''));
    setAllowScripts(!!config.allow_scripts);
    setBrowserState(session);
    setPort(String(settings.socks_port));
    setAllowRemoteAI(!!settings.allow_remote_ai);
  }, [projectId]);
  useEffect(() => { setSelected(''); }, [projectId]);
  useEffect(() => { reload().catch(e => setFeedback(String(e))); }, [reload, revision]);
  useEffect(() => {
    rpc<Row[]>('tor.pages', selected ? { source_id: selected } : {}).then(setPages).catch(e => setFeedback(String(e)));
  }, [selected, revision]);
  async function refreshPages() {
    const list = await rpc<Row[]>('tor.pages', selected ? { source_id: selected } : {});
    setPages(list);
  }
  async function addSource() {
    const created = await perform('Saving research source', () => rpc<Row>('tor.add', {
      url, title, notes, project_id: projectId || undefined, tags: tags.split(',').map(v => v.trim()).filter(Boolean),
    }), 'Source added.');
    if (created) { setSelected(created.id); setUrl(''); setTitle(''); setNotes(''); setTags(''); await reload(); }
  }
  async function collect() {
    const response = await perform('Starting Tor research', () => rpc<Row>('tasks.start', {
      kind:'tor_capture', source_id: selected, max_pages: count, max_depth: depth,
      project_id: projectId || undefined,
    }));
    if (response) setFeedback('Collection started. Open Agents to watch progress or stop it. Captures will appear in this workspace after completion.');
  }
  async function review(id: string, reviewed: boolean) {
    const done = await perform('Reviewing capture', () => rpc('tor.review', { id, reviewed }));
    if (done) await refreshPages();
  }
  async function publish(id: string) {
    const done = await perform('Publishing reviewed source', () => rpc<Row>('tor.publish', { id }), 'Published to the Knowledge Library.');
    if (done) await refreshPages();
  }
  async function search() {
    const list = await perform('Searching Tor research', () => rpc<Row[]>('tor.search', { query, ...(selected ? { source_id: selected } : {}) }));
    if (list) setResults(list);
  }
  async function openBrowser() {
    const result = await perform('Opening Tor Browser', () => rpc<Row>('tor.browser.open', { source_id: selected }));
    if (result) { setBrowserPage(result); setBrowserTarget(result.url); setBrowserState(await rpc<Row>('tor.browser.status')); }
  }
  async function followBrowser(index: number) {
    const result = await perform('Following Tor Browser link', () => rpc<Row>('tor.browser.follow', { index }));
    if (result) { setBrowserPage(result); setBrowserTarget(result.url); setBrowserState(await rpc<Row>('tor.browser.status')); }
  }
  async function navigateBrowser() {
    const result = await perform('Navigating Tor Browser', () => rpc<Row>('tor.browser.navigate', { url: browserTarget }));
    if (result) { setBrowserPage(result); setBrowserTarget(result.url); setBrowserState(await rpc<Row>('tor.browser.status')); }
  }
  async function captureBrowser() {
    const saved = await perform('Capturing rendered page', () => rpc<Row>('tor.browser.capture'), 'Page saved as an unreviewed research capture.');
    if (saved) await refreshPages();
  }
  async function closeBrowser() {
    const result = await perform('Closing Tor Browser session', () => rpc<Row>('tor.browser.close'));
    if (result) { setBrowserState({ running: false }); setBrowserPage(null); }
  }
  const chosen = sources.find(s => s.id === selected);
  return <div className="training-stack">
    <section className="surface">
      <div className="section-title"><div><h2>Tor research</h2><p>Read saved public forums, onion services and research sites through a local Tor SOCKS5 connection. Content is reference data, not agent instructions.</p></div><Globe2 size={23}/></div>
      <div className="form-grid">
        <Field label="Local Tor SOCKS5 port" hint="A Tor service commonly listens on 9050; Tor Browser often listens on 9150. No direct-network fallback."><input type="number" min="1" max="65535" value={port} onChange={e => setPort(e.target.value)}/></Field>
        <div className="button-group wrap"><Button onClick={async () => { const saved = await perform('Saving SOCKS port', () => rpc('tor.configure', { socks_port: Number(port), allow_remote_ai: allowRemoteAI })); if (saved) setTorStatus(null); }}>Save port</Button><Button icon={RefreshCw} onClick={async () => { const result = await perform('Checking local SOCKS connection', () => rpc<Row>('tor.status')); if (result) setTorStatus(result); }}>Check connection</Button></div>
      </div>
      <label className="check-row"><input type="checkbox" checked={allowRemoteAI} onChange={e => setAllowRemoteAI(e.target.checked)}/><span>Allow remote AI providers to receive Tor research results and saved source URLs. Leave disabled for local-model-only analysis. Save to apply.</span></label>
      {torStatus && <p role="status"><Status value={torStatus.connected ? 'available' : 'unavailable'}/> {torStatus.detail} · Port {torStatus.socks_port}</p>}
      <p className="muted">The lightweight collector reads user-added origins without scripts, login, form submission, or downloads. The separate browser mode has its own JavaScript choice. This checks for a SOCKS5 listener; it does not independently prove that listener is Tor.</p>
    </section>
    <section className="surface">
      <div className="section-title"><div><h2>Add a source or forum</h2><p>Give the app a direct HTTPS or version 3 .onion URL, plus your notes on why it matters.</p></div><Plus size={18}/></div>
      <Field label="URL"><input type="url" value={url} maxLength={2048} onChange={e => setUrl(e.target.value)} placeholder="http://your-v3-onion-address.onion/ or https://public-forum.example"/></Field>
      <div className="form-grid"><Field label="Title"><input value={title} onChange={e => setTitle(e.target.value)} maxLength={160} placeholder="Research forum"/></Field><Field label="Tags, comma-separated"><input value={tags} onChange={e => setTags(e.target.value)} placeholder="topic, forum, research"/></Field></div>
      <Field label="Your instructions or context for this source"><textarea rows={3} maxLength={4000} value={notes} onChange={e => setNotes(e.target.value)} placeholder="What the AI should look for in this resource"/></Field>
      <Button variant="primary" disabled={!url.trim()} onClick={addSource}>Save research source</Button>
    </section>
    <section className="surface">
      <div className="section-title"><div><h2>Interactive Tor Browser</h2><p>Launch your installed Tor Browser through geckodriver. The AI can read rendered forum pages, follow numbered same-site links, and save captures for review. Your normal Tor Browser window is not taken over.</p></div><Monitor size={20}/></div>
      <Field label="Source for interactive browser"><select aria-label="Source for interactive browser" value={selected} onChange={e => setSelected(e.target.value)}><option value="">Select a saved forum/source</option>{sources.map(s => <option key={s.id} value={s.id}>{s.title} · {s.url.slice(0, 65)}</option>)}</select></Field>
      <Field label="Tor Browser executable" hint="Select the installed Tor Browser executable, not ordinary Firefox. macOS: Tor Browser.app/Contents/MacOS/firefox. Windows: Tor Browser/Browser/firefox.exe. Linux: tor-browser/Browser/firefox."><input aria-label="Tor Browser executable" value={browserBinary} onChange={e => setBrowserBinary(e.target.value)} placeholder="Absolute path to Tor Browser's firefox binary" /></Field>
      <Field label="geckodriver path (optional if on PATH)"><input aria-label="geckodriver path" value={geckoPath} onChange={e => setGeckoPath(e.target.value)} placeholder="/absolute/path/to/geckodriver" /></Field>
      <label className="check-row"><input type="checkbox" checked={allowScripts} disabled={!!browserState?.running} onChange={e => setAllowScripts(e.target.checked)}/><span>Allow page JavaScript (disabled by default). Scripts may issue requests on their own; enable only for a trusted forum that needs dynamic rendering.</span></label>
      <div className="button-group wrap"><Button disabled={!!browserState?.running} onClick={async () => { const result = await perform('Saving Tor Browser configuration', () => rpc<Row>('tor.browser.configure', { binary_path: browserBinary, driver_path: geckoPath, allow_scripts: allowScripts }), 'Browser paths saved.'); if (result) { setBrowserBinary(result.binary_path); setGeckoPath(result.driver_path); setAllowScripts(!!result.allow_scripts); } }}>Save browser paths</Button><Button icon={RefreshCw} onClick={async () => { const status = await perform('Checking browser session', () => rpc<Row>('tor.browser.status')); if (status) setBrowserState(status); }}>Refresh session</Button></div>
      <p className="muted">Requires a local Tor SOCKS service on the saved port and the optional Selenium browser dependency. A copied Tor Browser profile is automated; fingerprinting and platform compatibility must be verified on your machine. Pages are never submitted as instructions to the AI.</p>
      <div className="button-group wrap"><Button variant="primary" disabled={!selected || !!browserState?.running} onClick={openBrowser}>Open selected source in Tor Browser</Button><Button disabled={!browserState?.running} onClick={captureBrowser}>Save current page</Button><Button disabled={!browserState?.running} onClick={closeBrowser}>Close browser</Button></div>
      {browserState?.running && <div className="form-grid"><Field label="Same-site page URL"><input aria-label="Same-site page URL" value={browserTarget} onChange={e => setBrowserTarget(e.target.value)} placeholder="Full URL on the current bookmarked site" /></Field><Button disabled={!browserTarget.trim()} onClick={navigateBrowser}>Go to URL</Button></div>}
      {browserState?.running && <p role="status" className="muted">Tor Browser active · {browserState.url}</p>}
      {browserPage && <div className="training-example"><strong>{browserPage.title}</strong><p className="muted">{browserPage.url}</p><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{browserPage.excerpt}</p><details><summary>{browserPage.links?.length || 0} same-site links</summary><div className="training-stack">{(browserPage.links || []).map((link: Row) => <button key={link.url} type="button" onClick={() => followBrowser(link.index)} style={{ textAlign: 'left', overflowWrap: 'anywhere' }}>{link.index}. {link.url}</button>)}</div></details></div>}
    </section>
    <ResearchBrowserP3 projectId={projectId} sourceId={selected} onCaptured={refreshPages} />
    <section className="surface">
      <div className="section-title"><div><h2>Sources and controlled browsing</h2><p>Choose a saved source. A bounded crawl stays on its exact site, using HTTP GET only.</p></div><BookOpen size={19}/></div>
      <div className="form-grid">
        <Field label="Research source"><select aria-label="Research source" value={selected} onChange={e => { setSelected(e.target.value); setResults([]); }}><option value="">All saved sources</option>{sources.map(s => <option key={s.id} value={s.id}>{s.title} · {s.url.slice(0, 65)}</option>)}</select></Field>
        <div className="button-group wrap"><Button variant="danger-quiet" icon={Trash2} disabled={!selected} onClick={async () => { if (!window.confirm('Remove this source bookmark? Captured pages remain saved.')) return; const removed = await perform('Removing source', () => rpc('tor.remove', { id: selected })); if (removed) { setSelected(''); await reload(); } }}>Remove bookmark</Button></div>
      </div>
      {chosen && <><p className="muted"><strong>{chosen.title}</strong> · {chosen.url} · {chosen.tags?.join(', ')}</p><p className="muted">{chosen.notes}</p><div className="form-grid"><Field label="Maximum pages (1–12)"><input type="number" min="1" max="12" value={count} onChange={e => setCount(Number(e.target.value))}/></Field><Field label="Link depth (0–2)"><select value={depth} onChange={e => setDepth(Number(e.target.value))}><option value="0">0 — supplied page only</option><option value="1">1 — follow linked pages</option><option value="2">2 — deeper same-site links</option></select></Field></div><Button variant="primary" onClick={collect}>Read source through Tor</Button></>}
      {feedback && <p role="status" className="muted">{feedback}</p>}
      <div className="form-grid"><Field label="Search collected pages"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Find concepts, methods, forum topics" onKeyDown={e => { if (e.key === 'Enter') search(); }}/></Field><Button icon={Search} onClick={search}>Search captures</Button></div>
      {results.length > 0 && <details open><summary>{results.length} search matches</summary>{results.map(x => <article className="training-example" key={x.id}><strong>{x.title}</strong><p>{x.excerpt}</p><small>{x.url}</small></article>)}</details>}
      {!pages.length ? <Empty title="No captures yet" description="Add a source and read it through your local Tor connection."/> : pages.map(p => <article className="training-example" key={p.id}><div className="row-between"><strong>{p.title}</strong><Status value={p.reviewed ? 'reviewed' : 'draft'}/></div><small>{p.url} · {p.captured_at}</small><details><summary>Read captured text</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{p.text}</p></details><div className="button-group wrap"><Button onClick={() => review(p.id, !p.reviewed)}>{p.reviewed ? 'Return to draft' : 'Mark reviewed'}</Button><Button disabled={!p.reviewed || !!p.library_source_id} onClick={() => publish(p.id)}>{p.library_source_id ? 'Saved to library' : 'Add to Knowledge Library'}</Button></div></article>)}
    </section>
  </div>;
}
