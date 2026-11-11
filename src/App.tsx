import SettingsView from './SettingsView';
import PromptEditor from './PromptEditor';
import { Status, Button, Field, Empty, Modal, Code, DetailItem } from './components';
import SimpleHome from './SimpleHome';
import CartLabPanel from './CartLabPanel';
import WebsiteAuditPanel from './WebsiteAuditPanel';
import Chat from './Chat';
import Team from './Team';
import SkillsView from './SkillsView';
import CeMcpPanel from './CeMcpPanel';
import RedTeam from './RedTeam';
import TrainingLab from './TrainingLab';
import TorResearch from './TorResearch';
import MacAutopilotPanel from './MacAutopilotPanel';
import { KnowledgeTransfer, SourceLearning } from './Learning';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { AppWindow, Cpu, Activity, ArrowDownToLine, ArrowUpRight, BookOpen, Check, CircleHelp, Code2, FileText, FlaskConical, FolderOpen, GitFork, Hexagon, ListChecks, LoaderCircle, MessageSquare, Moon, Network, Play, Plus, RefreshCw, Search, Settings2, ShieldCheck, Sun, Target, Trash2, Upload, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { asList, fileBase64, httpText, pretty, rpc } from './api';
import type { RecordData as Row } from './api';

type Page = 'Chat' | 'Overview' | 'Library' | 'Methods' | 'Knowledge graph' | 'Targets' | 'Traffic' | 'Tests' | 'Approvals' | 'Findings' | 'Settings' | 'Team' | 'CE bridge' | 'AI red team' | 'Training lab' | 'Tor research' | 'Prompt editor' | 'Mac control';
const WEB_PAGES: Page[] = ['Overview','Targets','Traffic','Tests','Approvals','Findings'];
// Chat is home. Everything else is reachable from the top-bar quick menu or lives
// as a background capability the AI can invoke directly.
const MAIN_PAGES: {label: 'Chat'|'Library'|'Settings'; page: Page; icon: LucideIcon}[] = [
 {label:'Chat',page:'Chat',icon:MessageSquare},
 {label:'Library',page:'Library',icon:BookOpen},
 {label:'Settings',page:'Settings',icon:Settings2},
];
// Reachable again from the sidebar "Advanced tools" group. Chat is still home and the
// agent can open the right workspace on its own, but every page has a manual entry so
// the app is one unified surface rather than orphaned routes.
const TOOL_PAGES: {label: string; page: Page; icon: LucideIcon}[] = [
 {label:'Tasks',page:'Team',icon:ListChecks},
 {label:'Web testing',page:'Overview',icon:Target},
 {label:'Approvals',page:'Approvals',icon:ShieldCheck},
 {label:'Methods',page:'Methods',icon:FileText},
 {label:'Knowledge graph',page:'Knowledge graph',icon:GitFork},
 {label:'Cheat Engine',page:'CE bridge',icon:Cpu},
 {label:'AI testing',page:'AI red team',icon:FlaskConical},
 {label:'Training lab',page:'Training lab',icon:Activity},
 {label:'Tor browser',page:'Tor research',icon:Network},
 {label:'Mac control',page:'Mac control',icon:AppWindow},
 {label:'Prompt editor',page:'Prompt editor',icon:Code2},
];
function pageTitle(page: Page): string {
 if (page==='Chat') return 'Chat';
 if (page==='Library') return 'Library';
 if (page==='Settings') return 'Settings';
 if (WEB_PAGES.includes(page)) return 'Web testing';
 return page;
}
function sidebarSelection(page: Page): 'Chat'|'Library'|'Settings'|null {
 if (page==='Chat') return 'Chat';
 if (page==='Library') return 'Library';
 if (page==='Settings') return 'Settings';
 return null;
}
type Execute = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
type Collections = { projects: Row[]; sources: Row[]; traffic: Row[]; tests: Row[]; actions: Row[]; findings: Row[]; runs: Row[] };
const EMPTY: Collections = { projects: [], sources: [], traffic: [], tests: [], actions: [], findings: [], runs: [] };
const pending = (a: Row) => ['pending', 'pending_approval', 'awaiting_approval', 'requires_approval', 'queued'].includes(a.status);
const sentence = (text: string) => (text || '').replaceAll('_', ' ');
const lineList = (value: string) => value.split(/\n|,/).map(v => v.trim()).filter(Boolean);
const shortDate = (value?: string) => value ? new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }

export default function App() {
  const [toolsOpen, setToolsOpen] = useState(false);
  const [page, setPage] = useState<Page>('Chat');
  const [memoryHandoff, setMemoryHandoff] = useState<{ id: number; text: string } | null>(null);
  const [data, setData] = useState<Collections>(EMPTY);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const [projectTitle, setProjectTitle] = useState('');
  const [projectUrl, setProjectUrl] = useState('');
  const [projectPermission, setProjectPermission] = useState('');
  const [projectId, setProjectId] = useState(localStorage.getItem('bughunter.project') || '');
  const [system, setSystem] = useState<Row>({});
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [busy, setBusy] = useState('');
  const operations = useRef(new Map<symbol, string>());
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [revision, setRevision] = useState(0);
  const [dark, setDark] = useState(localStorage.getItem('bughunter.theme') !== 'light');
  const refresh = useCallback(async () => {
    const entries = Object.keys(EMPTY) as (keyof Collections)[];
    const results = await Promise.allSettled(entries.map(key => rpc(`${key}.list`)));
    const projectsResult = results[0];
    if (projectsResult.status === 'rejected') setConnectionError(errorMessage(projectsResult.reason)); else setConnectionError('');
    setData(prev => { const next = { ...prev }; results.forEach((result, index) => { if (result.status === 'fulfilled') next[entries[index]] = asList(result.value); }); return next; });
    setLoading(false);
  }, []);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, [refresh, revision]);
  useEffect(() => { rpc('system.status').then(setSystem).catch(() => {}); }, [revision]);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('bughunter.theme', dark ? 'dark' : 'light'); }, [dark]);
  useEffect(() => { const update = (event: Event) => setDark((event as CustomEvent<'dark' | 'light'>).detail === 'dark'); window.addEventListener('bughunter-theme-change', update); return () => window.removeEventListener('bughunter-theme-change', update); }, []);
  useEffect(() => { if (!loading && projectId && !data.projects.some(p => p.id === projectId)) setProjectId(''); }, [data.projects, projectId, loading]);
  useEffect(() => { localStorage.setItem('bughunter.project', projectId); }, [projectId]);
  useEffect(() => { if (notice && !notice.error) { const timer = setTimeout(() => setNotice(null), 6500); return () => clearTimeout(timer); } }, [notice]);
  const perform: Execute = useCallback(async (label, fn, success) => {
    const operation = Symbol(label); operations.current.set(operation, label); setBusy(label); setNotice(null);
    try { const result = await fn(); await refresh(); if (success) setNotice({ text: success, error: false }); setRevision(v => v + 1); return result; }
    catch (error) { setNotice({ text: errorMessage(error), error: true }); return undefined; }
    finally { operations.current.delete(operation); setBusy([...operations.current.values()].at(-1) || ''); }
  }, [refresh]);
  const scoped = useMemo(() => ({ ...data, sources: data.sources.filter(r => !projectId || !r.project_id || r.project_id === projectId), traffic: data.traffic.filter(r => !projectId || r.project_id === projectId), tests: data.tests.filter(r => !projectId || r.project_id === projectId), actions: data.actions.filter(r => !projectId || r.project_id === projectId), findings: data.findings.filter(r => !projectId || r.project_id === projectId), runs: data.runs.filter(r => r.kind === 'study' || !projectId || r.project_id === projectId) }), [data, projectId]);
  const selectExistingOrCreatedProject = async (id: string) => { await refresh(); setProjectId(id); };
  const approvals = scoped.actions.filter(pending).length;
  const shared = { data: scoped, projectId, perform, busy, revision, navigate: setPage };
  return <div className={`app-shell ${page === 'Chat' ? 'chat-home' : ''}`}>
    <a href="#main-content" className="skip-link">Skip to workspace</a>
    <aside className="sidebar"><div className="brand"><div className="brand-mark"><Hexagon size={30} /><Network size={15} /></div><div><strong>BugHunter<span>Claudex</span></strong><small>Research workbench</small></div></div>
      <nav aria-label="Main navigation" className="simple-nav">
        {MAIN_PAGES.map(({label,page:destination,icon:Icon}) => { const selected = sidebarSelection(page) === label; return <button key={label} aria-label={label} className={`nav-item ${selected ? 'selected' : ''}`} aria-current={selected ? 'page' : undefined} onClick={() => setPage(destination)}><Icon size={19}/><span>{label}</span></button>; })}
        <details className="nav-advanced" open={sidebarSelection(page)===null}><summary>Advanced tools{approvals>0 && <b className="tab-count">{approvals}</b>}</summary>
          <div className="nav-advanced-list">{TOOL_PAGES.map(({label,page:destination,icon:Icon}) => <button key={label} type="button" aria-label={label} className={`nav-item nav-sub ${page===destination ? 'selected' : ''}`} aria-current={page===destination ? 'page' : undefined} onClick={() => setPage(destination)}><Icon size={17}/><span>{label}</span>{label==='Approvals' && approvals>0 && <b className="tab-count">{approvals}</b>}</button>)}</div>
        </details>
      </nav>
      <div className="sidebar-bottom"><div className="local-note"><span className={`connection-dot ${connectionError ? 'offline' : ''}`} /><div><strong>{connectionError ? 'Backend disconnected' : 'Local workspace'}</strong><small>{connectionError ? 'Check the backend connection' : 'Sources and evidence stay here'}</small></div></div><div className="sidebar-footer"><span>v0.1.0</span><button className="icon-button" onClick={() => setDark(!dark)} aria-label={`Switch to ${dark ? 'light' : 'dark'} theme`}>{dark ? <Sun size={17}/> : <Moon size={17}/>}</button></div></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb"><strong>{pageTitle(page)}</strong></div><div className="topbar-controls"><button className="icon-button" aria-label="Tools" title="Tools" aria-haspopup="dialog" onClick={() => setToolsOpen(true)}><Settings2 size={17}/></button>{(page !== 'Chat' || system.emergency_stop) && <Button variant={system.emergency_stop ? '' : 'danger'} onClick={async () => { const stopped = !system.emergency_stop; const value = await perform(stopped ? 'Stopping tasks' : 'Resuming requests', () => rpc(stopped ? 'system.stop' : 'system.resume')); if (value) setSystem(old => ({...old, emergency_stop: stopped})); }}>{system.emergency_stop ? 'Resume requests' : 'Stop all'}</Button>}<div className="global-project"><label htmlFor="active-project">Project</label><select id="active-project" value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">All knowledge</option>{data.projects.map(p=><option value={p.id} key={p.id}>{p.name}</option>)}</select><button className="icon-button" aria-label="Create a project" title="New project" onClick={()=>setShowProjectPicker(true)}><Plus size={18}/></button></div>{busy && <span className="activity-label"><LoaderCircle size={14} className="spin"/>{busy}</span>}<button className="icon-button" onClick={() => { refresh(); setRevision(v => v + 1); }} aria-label="Refresh workspace"><RefreshCw size={16}/></button></div></header>
      <main id="main-content" tabIndex={-1}>
        {sidebarSelection(page) === null && <div className="tool-page-return"><Button onClick={()=>setPage('Chat')}>← Back to chat</Button></div>}
        {WEB_PAGES.includes(page) && <div className="workspace-navigation" aria-label="Web testing"><div className="secondary-tabs web-tool-tabs" role="navigation" aria-label="Web testing sections">
          {([{target:'Overview',name:'Start'},{target:'Targets',name:'Targets & scope'},{target:'Traffic',name:'Captured requests'},{target:'Tests',name:'Suggested checks'},{target:'Approvals',name:'Approvals'},{target:'Findings',name:'Reports'}] as const).map(({target,name})=><button type="button" key={target} aria-label={name} className={page===target?'active':''} aria-current={page===target?'page':undefined} onClick={()=>setPage(target)}>{name}{target==='Approvals' && approvals>0 && <span className="tab-count">{approvals}</span>}</button>)}
        </div></div>}
        {connectionError && <div className="connection-banner" role="alert"><CircleHelp size={20}/><div><strong>The local backend is not available</strong><p>{connectionError}</p><small>Start the desktop app, or run the development backend with the matching RPC session token.</small></div></div>}
        {notice && <div className={`notice app-toast ${notice.error ? 'error' : 'success'}`} role={notice.error ? 'alert' : 'status'}>{notice.error ? <CircleHelp size={17}/> : <Check size={17}/>}<span>{notice.text}</span><button aria-label="Dismiss notification" className="icon-button" onClick={() => setNotice(null)}><X size={16}/></button></div>}
        {loading ? <div className="loading"><LoaderCircle className="spin"/>Opening your workspace…</div> : <>
          {page === 'Chat' && <Chat navigate={setPage} projectId={projectId} projectName={data.projects.find(p=>p.id===projectId)?.name}/>}
          {page === 'Overview' && <><SimpleHome {...shared} system={system} selectProject={setProjectId}/><WebsiteAuditPanel projectId={projectId} navigate={setPage}/>{scoped.actions.some(a => ['pending_approval','approved'].includes(a.status)) && <section className="home-approvals"><h2>Needs your review</h2><p>Review the exact request below. Approve it, then choose Send once.</p><Approvals {...shared}/></section>}</>}
          {page === 'Team' && <Team perform={perform} busy={busy} projectId={projectId} onOpenAITesting={()=>setPage('AI red team')} onOpenMemory={()=>setPage('CE bridge')}/>}
          {page === 'CE bridge' && <CeMcpPanel projectId={projectId} handoff={memoryHandoff} onHandoffConsumed={() => setMemoryHandoff(null)}/>}
          {page === 'AI red team' && <RedTeam perform={perform} busy={busy} revision={revision} projectId={projectId}/>}
          {page === 'Training lab' && <TrainingLab perform={perform} revision={revision} projectId={projectId}/>}
          {page === 'Prompt editor' && <PromptEditor/>}
          {page === 'Tor research' && <TorResearch perform={perform} revision={revision} projectId={projectId}/>}
          {page === 'Mac control' && <MacAutopilotPanel initialTask="" projectId={projectId} onClose={()=>setPage('Chat')}/>}
          {page === 'Library' && <><Library {...shared}/><details className="surface history-details"><summary>Export and storage</summary><KnowledgeTransfer perform={perform} busy={busy}/></details></>}
          {page === 'Knowledge graph' && <KnowledgeGraph perform={perform} revision={revision} projectId={projectId}/>}
          {page === 'Methods' && <SkillsView projectId={projectId}/>}
          {page === 'Targets' && <Targets {...shared} selectProject={setProjectId}/>}
          {page === 'Traffic' && <Traffic {...shared}/>}
          {page === 'Tests' && <CartLabPanel navigate={setPage} selectProject={selectExistingOrCreatedProject}/>}
          {page === 'Tests' && <Tests {...shared}/>}
          {page === 'Approvals' && <Approvals {...shared}/>}
          {page === 'Findings' && <Findings {...shared}/>}
          {page === 'Settings' && <Settings perform={perform} busy={busy} revision={revision} projectId={projectId} projects={data.projects} system={system} onSystemChange={setSystem}/>}
        </>}
      </main>
      {toolsOpen && <Modal title="Tools" onClose={() => setToolsOpen(false)}><div className="tool-menu-list">{TOOL_PAGES.map(tool => <button type="button" key={tool.page} onClick={() => {setPage(tool.page);setToolsOpen(false);}}>{tool.label}</button>)}</div></Modal>}
      {showProjectPicker && <Modal title="New project" onClose={()=>setShowProjectPicker(false)}><form onSubmit={async e=>{e.preventDefault();const saved=await perform('Creating project',()=>rpc('projects.save',{name:projectTitle.trim(),scope:projectUrl.trim()?[projectUrl.trim()]:[],authorization:projectPermission.trim(),workspace_only:!projectUrl.trim(),exclusions:[],read_only_paths:[],requests_per_second:1,max_concurrency:2,max_requests:1000,allow_private:false}), 'Project created.');if(saved?.id){setProjectId(saved.id);setShowProjectPicker(false);setProjectTitle('');setProjectUrl('');setProjectPermission('');}}}><p className="muted">Use one project for conversations and all workspaces. A website and authorization are needed only when you want to run web tests.</p><Field label="Project name"><input required value={projectTitle} onChange={e=>setProjectTitle(e.target.value)} placeholder="My test application"/></Field><Field label="Website or API (optional)"><input type="url" value={projectUrl} onChange={e=>setProjectUrl(e.target.value)} placeholder="https://your-app.example"/></Field><Field label="Permission / scope notes (needed for web testing)"><textarea required={!!projectUrl.trim()} rows={3} value={projectPermission} onChange={e=>setProjectPermission(e.target.value)} placeholder="What is permitted to test?"/></Field><div className="form-actions"><Button onClick={()=>setShowProjectPicker(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={!!busy}>Create project</Button></div></form></Modal>}
      <footer className="workspace-footer"><span><ShieldCheck size={13}/>Authorized research • Individual request approvals</span><span>Knowledge → hypothesis → evidence</span></footer>
    </div>
  </div>;
}

export type Shared = { data: Collections; projectId: string; perform: Execute; busy: string; revision: number; navigate: (page: Page) => void };

function Library({ data, perform, busy, revision, projectId }: Shared) {
  const [showImport, setShowImport] = useState(false); const [importMode, setImportMode] = useState('files'); const [path, setPath] = useState(''); const [websiteUrl, setWebsiteUrl] = useState(''); const [downloadMedia, setDownloadMedia] = useState(false); const [chunkCorrection, setChunkCorrection] = useState<Row | null>(null); const [chunkText, setChunkText] = useState(''); const [files, setFiles] = useState<File[]>([]); const [selected, setSelected] = useState<Row | null>(null); const [query, setQuery] = useState(''); const [results, setResults] = useState<Row[] | null>(null); const [deleteId, setDeleteId] = useState('');
  const openSource = (id: string) => perform('Opening source', () => rpc('sources.get', { id })).then(value => value && setSelected(value));
  function chooseFiles(selected: File[]) { setFiles(selected); setPath(''); setWebsiteUrl(''); }
  const youtube = /^https:\/\/(?:www\.|m\.)?(?:youtube\.com|youtu\.be)\//i.test(websiteUrl);
  async function importSource(e: FormEvent) {
    e.preventDefault();
    const value = await perform('Importing sources', async () => {
      if (websiteUrl) return rpc('sources.import', { url: websiteUrl, download_media: downloadMedia, language: 'en', project_id: projectId || null });
      if (files.length) {
        const imported = [];
        for (const file of files) {
          if (file.size > 64 * 1024 * 1024) throw new Error(`${file.name} exceeds the 64 MiB upload limit. Use its absolute file path instead; any earlier files are already queued.`);
          imported.push(await rpc('sources.import', { filename: file.name, content_base64: await fileBase64(file), project_id: projectId || null }));
        }
        return imported;
      }
      if (path.toLowerCase().endsWith('.zip')) return rpc('courses.import', {path,project_id:projectId||null});
      return rpc('sources.import', { path, copy_original: false, project_id: projectId || null });
    }, 'Resources queued for ingestion. The library will show parsed content and any unsupported files.');
    if (value !== undefined) { setShowImport(false); setFiles([]); setPath(''); setWebsiteUrl(''); }
  }

  return <><div className="knowledge-intro"><div><h2>Library</h2><p>What your models have learned, and the sources behind it. Everything stays on this Mac.</p></div></div>
    <Learnings revision={revision} onOpenSource={openSource}/>
    <div className="toolbar"><form className="search" onSubmit={e => { e.preventDefault(); perform('Searching knowledge', () => rpc('knowledge.search', { query, limit: 20, project_id: projectId || null })).then(v => v && setResults(asList(v))); }}><Search size={17}/><input aria-label="Search knowledge" placeholder="Ask your library or search a technique…" value={query} onChange={e => setQuery(e.target.value)}/>{results && <button type="button" aria-label="Clear search" onClick={() => { setResults(null); setQuery(''); }}><X size={15}/></button>}<button type="submit" disabled={!query.trim()}>Search</button></form><Button variant="primary" icon={Upload} onClick={() => setShowImport(true)}>Upload / add link</Button></div>
    {results !== null ? <section className="surface"><div className="section-title"><h2>{results.length} matching passages</h2><Button onClick={() => setResults(null)}>Back to library</Button></div>{results.length ? <div className="passages">{results.map((hit, i) => <article key={hit.id || i} className="passage"><button className="source-citation" onClick={() => hit.source_id && openSource(hit.source_id)}><BookOpen size={14}/>{hit.title || hit.source_title || 'Source'}<span>{pretty(hit.locator || hit.citation)}</span></button><p>{hit.text || hit.content}</p>{hit.score != null && <small>Relevance {Number(hit.score).toFixed(3)}</small>}</article>)}</div> : <Empty icon={Search} title="No matching passages" description="Try a different term, or import a resource that covers this topic."/>}</section> : <section className="surface"><div className="section-title"><div><h2>Your files</h2><p>{data.sources.length} imported sources. Ready means extraction finished; Partial means a step needs attention. Open a resource for details.</p></div><span className="quiet-badge"><FolderOpen size={14}/>Stored locally</span></div>{!data.sources.length ? <Empty icon={BookOpen} title="Build your research memory" description="Import a book, lesson, or exploit write-up. The workbench preserves citations and connects related concepts."><Button icon={Upload} onClick={() => setShowImport(true)}>Import your first resource</Button></Empty> : <div className="table-scroll"><table><thead><tr><th>Resource</th><th>Type</th><th>Status</th><th>Added</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{data.sources.map(source => <tr key={source.id}><td><button className="table-title" onClick={() => openSource(source.id)}><FileText size={18}/><div><strong>{source.title || source.filename}</strong>{source.error && <small className="error-text">{source.error}</small>}</div></button></td><td>{source.media_type || source.type || 'Document'}</td><td><Status value={source.status}/></td><td className="muted nowrap">{shortDate(source.created_at)}</td><td><div className="row-actions">{['ready', 'error', 'failed', 'partial', 'incomplete'].includes(source.status) && <button aria-label={source.url ? 'Refresh website source' : 'Reprocess resource'} className="icon-button" onClick={() => perform('Retrying ingestion', () => rpc('sources.retry', { id: source.id }))}><RefreshCw size={15}/></button>}<button aria-label={`Delete ${source.title || source.filename}`} className="icon-button" onClick={() => setDeleteId(source.id)}><Trash2 size={15}/></button></div></td></tr>)}</tbody></table></div>}</section>}
    {showImport && <Modal title="Add to knowledge" onClose={() => setShowImport(false)}><form onSubmit={importSource}><div className="import-tabs" role="group" aria-label="Import method">{[['files','Upload files'],['url','Paste a link'],['path','Large course / folder']].map(([value,label]) => <Button key={value} variant={importMode === value ? 'primary' : ''} onClick={() => {setImportMode(value);setFiles([]);setPath('');setWebsiteUrl('');}}>{label}</Button>)}</div>{importMode === 'files' && <label className="file-drop" onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault();chooseFiles(Array.from(e.dataTransfer.files));}}><Upload size={25}/><strong>{files.length ? `${files.length} files selected` : 'Choose files or drop them here'}</strong><span>Documents, ZIPs, images, text/code, audio, or video · up to 64 MiB each</span><input type="file" multiple onChange={e => chooseFiles(Array.from(e.target.files || []))}/></label>}{importMode === 'path' && <><Field label="Absolute file path" hint="A file or folder up to 500 files, 32 GiB per file. Large course ZIPs are processed progressively and can resume after interruption. Keep the original ZIP in place until import finishes."><input value={path} onChange={e => setPath(e.target.value)} placeholder="/Users/you/Courses"/></Field><p className="muted">Keep these files in place until extraction finishes. Check the extracted text and warnings before deleting your originals.</p></>}{importMode === 'url' && <><Field label="Public link" hint="Supports public YouTube videos, playlists and channels (first 50 videos per import), web pages, and direct files. Instagram profiles and restricted sites cannot be reliably bulk imported."><input type="url" value={websiteUrl} onChange={e => setWebsiteUrl(e.target.value)} placeholder="https://youtube.com/watch?v=... or https://example.com/article"/></Field>{youtube && <label className="checkbox"><input type="checkbox" checked={downloadMedia} onChange={e => setDownloadMedia(e.target.checked)}/><span>Also download the video to extract audio and sample screen images.</span></label>}</>}<div className="form-actions"><Button onClick={() => setShowImport(false)}>Cancel</Button><Button type="submit" variant="primary" icon={Upload} disabled={!!busy || (!path && !files.length && !websiteUrl)}>Import resource</Button></div></form></Modal>}
    {selected && <Modal title={selected.source?.title || selected.title || 'Source details'} wide onClose={() => setSelected(null)}><Status value={selected.source?.status || selected.status}/><SourceLearning source={selected} perform={perform} busy={busy} onChange={() => openSource(selected.id)}/><DetailItem label="Import error" value={selected.error || selected.source?.error}/><DetailItem label="Needs attention" value={selected.warnings}/><DetailItem label="Extraction notes" value={selected.extraction_notes}/><DetailItem label="Detected format" value={selected.detected_format}/><DetailItem label="Source URL" value={selected.downloaded_url || selected.url}/><DetailItem label="Retrieved at" value={selected.fetched_at}/>{selected.import_items?.length > 0 && <details className="import-results"><summary>Archive results ({selected.import_items.length} entries)</summary><Code value={selected.import_items}/></details>}{asList(selected.chunks || selected.passages).length > 0 && <section className="knowledge-reader"><h3>Document content</h3><p className="muted">This is one imported source. The app divides it into passages internally for search; those passages aren't separate documents.</p><div className="knowledge-reader-text">{asList(selected.chunks || selected.passages).map((chunk) => String(chunk.text || chunk.content || '')).join('\n\n')}</div><details className="knowledge-reader-edits"><summary>Edit a specific passage ({asList(selected.chunks || selected.passages).length})</summary>{asList(selected.chunks || selected.passages).map((chunk, i) => <article className="passage" key={chunk.id || i}><small>Part {i+1}</small><p>{chunk.text || chunk.content}</p><Button onClick={() => { setChunkCorrection(chunk); setChunkText(chunk.text || chunk.content || ''); }}>Correct this part</Button></article>)}</details></section>}{!selected.chunks && !selected.passages && <Code value={selected}/>}</Modal>}
    {chunkCorrection && <Modal title="Correct source passage" wide onClose={() => setChunkCorrection(null)}><Field label="Corrected text" hint="The source and correction history remain traceable."><textarea rows={12} value={chunkText} onChange={e => setChunkText(e.target.value)}/></Field><div className="form-actions"><Button onClick={() => setChunkCorrection(null)}>Cancel</Button><Button variant="primary" onClick={() => perform('Saving correction', () => rpc('knowledge.correct', { id: chunkCorrection.id, text: chunkText }), 'Passage corrected.').then(value => { if (value) { setChunkCorrection(null); setSelected(null); } })}>Save correction</Button></div></Modal>}
    {deleteId && <Modal title="Delete this resource?" onClose={() => setDeleteId('')}><p>Remove the source, its searchable passages, and knowledge assertions that no longer have supporting sources.</p><div className="form-actions"><Button onClick={() => setDeleteId('')}>Keep resource</Button><Button variant="danger" onClick={() => perform('Deleting source', () => rpc('sources.delete', { id: deleteId }), 'Resource deleted.').then(() => setDeleteId(''))}>Delete resource</Button></div></Modal>}
  </>;
}

const LEARNING_GROUPS: { key: string; label: string }[] = [
  { key: 'technique', label: 'Techniques' },
  { key: 'vulnerability', label: 'Vulnerability classes' },
  { key: 'tool', label: 'Tools' },
  { key: 'prerequisite', label: 'Prerequisites' },
  { key: 'example', label: 'Worked examples' },
];
function Learnings({ revision, onOpenSource }: { revision: number; onOpenSource: (id: string) => void }) {
  const [data, setData] = useState<{ groups: Record<string, Row[]>; models: Record<string, number>; total: number } | null>(null);
  const [open, setOpen] = useState<string>('technique');
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { let alive = true; rpc<any>('knowledge.learnings').then(v => { if (alive) setData(v); }).catch(() => {}); return () => { alive = false; }; }, [revision]);
  if (!data) return null;
  const models = Object.entries(data.models || {}).sort((a, b) => b[1] - a[1]);
  if (!data.total) return <section className="surface learnings-empty"><div className="section-title"><div><h2>What your models have learned</h2><p>Nothing recorded yet. Ask the agent in Chat to study a source or run a task — durable techniques, vulnerability classes and tools it establishes will collect here, tagged by the model that learned them.</p></div><GitFork size={20}/></div></section>;
  const groupsWithItems = LEARNING_GROUPS.filter(g => (data.groups[g.key] || []).length);
  const active = data.groups[open]?.length ? open : (groupsWithItems[0]?.key || 'technique');
  const items = (data.groups[active] || []);
  const shown = expanded ? items : items.slice(0, 12);
  return <section className="surface learnings">
    <div className="section-title"><div><h2>What your models have learned</h2><p>{data.total} source-backed assertions. Each keeps a verbatim quote, its source, and the model that recorded it.</p></div><GitFork size={20}/></div>
    {models.length > 0 && <div className="learnings-models">{models.map(([name, count]) => <span key={name} className="learnings-model-chip"><Cpu size={13}/>{name}<b>{count}</b></span>)}</div>}
    <div className="learnings-tabs" role="tablist">{groupsWithItems.map(g => <button key={g.key} role="tab" aria-selected={active === g.key} className={active === g.key ? 'active' : ''} onClick={() => { setOpen(g.key); setExpanded(false); }}>{g.label} <span>{(data.groups[g.key] || []).length}</span></button>)}</div>
    <div className="learnings-list">{shown.map(item => <article key={item.id} className="learnings-card">
      <div className="learnings-card-head"><strong>{item.label}</strong>{item.model ? <span className="learnings-badge" title="Model that recorded this">{item.model}</span> : <span className="learnings-badge quiet">{item.status || 'inferred'}</span>}</div>
      {item.quote && <blockquote className="learnings-quote">"{String(item.quote).slice(0, 320)}"</blockquote>}
      <div className="learnings-card-foot"><button className="source-citation" onClick={() => item.source_id && onOpenSource(item.source_id)}><BookOpen size={13}/>{item.source_title || 'Source'}{item.locator ? ` · ${item.locator}` : ''}</button></div>
    </article>)}</div>
    {items.length > 12 && <Button onClick={() => setExpanded(v => !v)}>{expanded ? 'Show less' : `Show all ${items.length}`}</Button>}
  </section>;
}

function KnowledgeGraph({ perform, revision, projectId }: { perform: Execute; revision: number; projectId: string }) {
  const [graph, setGraph] = useState<{ nodes: Row[]; edges: Row[] }>({ nodes: [], edges: [] }); const [selected, setSelected] = useState<Row | null>(null); const [filter, setFilter] = useState(''); const [correction, setCorrection] = useState('');
  useEffect(() => {
    let active = true; setSelected(null); setGraph({nodes: [], edges: []});
    const timer = setTimeout(() => { rpc('knowledge.graph', {project_id: projectId || '*', query: filter, limit: 80}).then(value => { if (active) setGraph({nodes: value.nodes || [], edges: value.edges || []}); }).catch(() => {}); }, 150);
    return () => { active = false; clearTimeout(timer); };
  }, [revision, projectId, filter]);
  const filtered = graph.nodes.filter(n => pretty(n).toLowerCase().includes(filter.toLowerCase())).slice(0, 80);
  const points = useMemo(() => new Map(filtered.map((n, i) => { const angle = i * Math.PI * (3 - Math.sqrt(5)); const radius = 36 + Math.sqrt(i / Math.max(filtered.length, 1)) * 225; return [n.id, { x: 400 + Math.cos(angle) * radius * 1.25, y: 290 + Math.sin(angle) * radius }]; })), [filtered]);
  const linked = selected ? graph.edges.filter(e => (e.source || e.source_id) === selected.id || (e.target || e.target_id) === selected.id) : [];
  return <><div className="toolbar"><div className="search"><Search size={17}/><input aria-label="Filter knowledge graph" placeholder="Find a technique, tool, or source…" value={filter} onChange={e => setFilter(e.target.value)}/></div><span className="muted">{graph.nodes.length} nodes · {graph.edges.length} connections</span></div><section className="surface graph-layout"><div className="graph-canvas">{!graph.nodes.length ? <Empty icon={GitFork} title="Your learning map starts with your files" description="Import learning materials to connect techniques, prerequisites, tools, and examples. Every assertion keeps its origin."/> : <><svg viewBox="0 0 800 580" role="img" aria-label="Knowledge graph"><defs><pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="var(--graph-dot)"/></pattern></defs><rect width="800" height="580" fill="url(#dots)"/>{graph.edges.map((edge, i) => { const a = points.get(edge.source || edge.source_id), b = points.get(edge.target || edge.target_id); return a && b ? <line key={edge.id || i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`graph-edge ${selected && (selected.id === (edge.source || edge.source_id) || selected.id === (edge.target || edge.target_id)) ? 'highlight' : ''}`}/> : null; })}{filtered.map(node => { const pos = points.get(node.id)!; const type = node.type || node.kind || 'concept'; return <g key={node.id} transform={`translate(${pos.x},${pos.y})`} className={`graph-node type-${type} ${selected?.id === node.id ? 'selected' : ''}`} tabIndex={0} role="button" aria-label={node.label || node.name || node.title} onClick={() => { setSelected(node); setCorrection(node.text || node.label || node.name || ''); }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(node); setCorrection(node.text || node.label || node.name || ''); } }}><circle r={type === 'source' ? 13 : 9}/><text textAnchor="middle" y={28}>{String(node.label || node.name || node.title || node.id).slice(0, 29)}</text></g>; })}</svg><div className="graph-legend"><span><i/>Concept</span><span><i className="source"/>Source</span><span><i className="technique"/>Technique</span><small>Showing up to 80 matching nodes</small></div></>}</div><aside className="graph-detail">{!selected ? <div className="detail-placeholder"><GitFork size={25}/><h3>Follow a connection</h3><p>Select a node to see its source and related knowledge.</p></div> : <><div className="detail-top"><span className="quiet-badge">{selected.type || selected.kind || 'Concept'}</span><button className="icon-button" aria-label="Clear selected node" onClick={() => setSelected(null)}><X size={16}/></button></div><h2>{selected.label || selected.name || selected.title}</h2><DetailItem label="Assertion" value={selected.text || selected.description}/><DetailItem label="Provenance" value={selected.citations || selected.locator || selected.source_id}/><DetailItem label="Review status" value={selected.status || selected.confidence || 'Extracted from source'}/><h4>Connections</h4>{linked.map((edge, i) => { const other = graph.nodes.find(n => n.id === ((edge.source || edge.source_id) === selected.id ? (edge.target || edge.target_id) : (edge.source || edge.source_id))); return <button className="connection-row" key={edge.id || i} onClick={() => other && setSelected(other)}><GitFork size={14}/><div><small>{edge.relation || edge.label || edge.type || 'Related to'}</small><strong>{other?.label || other?.name || other?.title || 'Source'}</strong></div></button>; })}{(selected.chunk_id || selected.kind === 'example') && <><Field label="Correct this assertion" hint="Corrections preserve the original assertion and its source history."><textarea rows={4} value={correction} onChange={e => setCorrection(e.target.value)}/></Field><Button icon={Check} onClick={() => perform('Saving correction', () => rpc('knowledge.correct', { id: selected.chunk_id || selected.id, text: correction }), 'Correction saved.')}>Save correction</Button></>}</>}</aside></section></>;
}

function Targets({ data, projectId, perform, busy, selectProject }: Shared & { selectProject: (id: string) => void }) {
  const [editing, setEditing] = useState<Row | null>(null); const [deleting, setDeleting] = useState<Row | null>(null);
  const [name, setName] = useState(''); const [scope, setScope] = useState(''); const [authorization, setAuthorization] = useState('');
  const [exclusions, setExclusions] = useState(''); const [reads, setReads] = useState(''); const [privateAllowed, setPrivateAllowed] = useState(false);
  const [rps, setRps] = useState(1); const [concurrency, setConcurrency] = useState(2); const [budget, setBudget] = useState(1000);
  const [stagingEnabled, setStagingEnabled] = useState(false); const [stagingOrigin, setStagingOrigin] = useState('');
  const [stagingChallenge, setStagingChallenge] = useState(''); const [stagingSku, setStagingSku] = useState('TEST-ITEM');
  const [stagingUnitPrice, setStagingUnitPrice] = useState(10000); const [stagingAcknowledged, setStagingAcknowledged] = useState(false);
  function edit(value: Row) {
    setEditing(value); setName(value.name || ''); setScope((value.scope || []).join('\n')); setAuthorization(value.authorization || ''); setExclusions((value.exclusions || []).join('\n'));
    setReads((value.read_only_paths || []).some((v: any) => typeof v === 'object') ? JSON.stringify(value.read_only_paths, null, 2) : (value.read_only_paths || []).join('\n'));
    setPrivateAllowed(value.allow_private || false); setRps(value.requests_per_second || 1); setConcurrency(value.max_concurrency || 2); setBudget(value.max_requests || 1000);
    setStagingEnabled(Boolean(value.staging_quote)); setStagingOrigin(value.staging_quote?.origin || '');
    setStagingChallenge(value.staging_quote?.challenge || ''); setStagingSku(value.staging_quote?.test_sku || 'TEST-ITEM');
    setStagingUnitPrice(value.staging_quote?.expected_unit_price_minor || 10000);
    setStagingAcknowledged(value.staging_quote?.acknowledge_no_real_orders === true);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const value = await perform('Saving project', async () => {
      const parsed = reads.trim().startsWith('[') ? JSON.parse(reads) : lineList(reads);
      if (!Array.isArray(parsed)) throw new Error('Read-only path rules must be a list.');
      const special = ['/.well-known/bughunter-price-audit.json','/__bughunter__/quote'];
      const readRules = parsed.filter((r: any) => typeof r !== 'object' || !special.includes(r.path));
      if (stagingEnabled) {
        if (!stagingOrigin || stagingChallenge.length < 24 || !stagingAcknowledged) throw new Error('Configure the staging origin, a deployment challenge, and non-transactional confirmation.');
        readRules.push({path:'/.well-known/bughunter-price-audit.json',methods:['GET']}, {path:'/__bughunter__/quote',methods:['POST']});
      }
      return rpc('projects.save', { ...(editing?.id ? { id: editing.id } : {}), name, scope: lineList(scope), authorization, exclusions: lineList(exclusions), read_only_paths: readRules, allow_private: privateAllowed, requests_per_second: rps, max_concurrency: concurrency, max_requests: budget, staging_quote: stagingEnabled ? { environment:'staging', origin:stagingOrigin, challenge:stagingChallenge, test_sku:stagingSku, expected_unit_price_minor:stagingUnitPrice, acknowledge_no_real_orders:stagingAcknowledged } : null });
    }, 'Project saved.');
    if (value) { selectProject(value.id); setEditing(null); }
  }
  return <>
    <div className="toolbar"><span className="muted">{data.projects.length} projects</span><Button variant="primary" icon={Plus} onClick={() => edit({})}>New project</Button></div>
    <section className="surface">{!data.projects.length ? <Empty icon={Target} title="Which website are you working on?" description="Add a website or API and record your permission to test it. You can also try the practice shop from Investigation."><Button icon={Plus} onClick={() => edit({})}>Create project</Button></Empty> : data.projects.map(project => <article className="project-row" key={project.id}>
      <div className="project-heading"><div className="project-icon"><Target size={23}/></div><div><h2>{project.name}</h2><p>{(project.scope || []).join(', ')}</p></div>{project.id === projectId && <span className="quiet-badge">Current project</span>}</div>
      <p className="project-authorization">{project.authorization}</p>
      <div className="project-actions"><Button variant={project.id === projectId ? '' : 'primary'} onClick={() => selectProject(project.id)} disabled={project.id === projectId}>{project.id === projectId ? 'Selected' : 'Use this project'}</Button><Button icon={Settings2} onClick={() => edit(project)}>Edit project</Button><Button variant="danger-quiet" icon={Trash2} onClick={() => setDeleting(project)}>Delete project</Button></div>
    </article>)}</section>
    {editing && <Modal title={editing.id ? 'Edit project' : 'Create a project'} onClose={() => setEditing(null)}><form onSubmit={save}>
      <Field label="Project name"><input required value={name} onChange={e => setName(e.target.value)} placeholder="My shop research"/></Field>
      <Field label="Website or API URLs" hint="Include https://. Add one allowed website or URL prefix per line."><textarea required rows={2} value={scope} onChange={e => setScope(e.target.value)} placeholder="https://shop.example.com"/></Field>
      <Field label="Permission and program rules" hint="Add the bug bounty program link, allowed accounts, and any restrictions."><textarea required rows={3} value={authorization} onChange={e => setAuthorization(e.target.value)} placeholder="I own this test shop, or this program permits these tests…"/></Field>
      <details className="advanced-options"><summary>Staging price integrity tests (no purchases)</summary>
        <p className="muted">Only for a staging deployment you operate. Your server must expose a test manifest and a quote-only endpoint. BugHunter never calls order or payment endpoints.</p>
        <label className="checkbox"><input type="checkbox" checked={stagingEnabled} onChange={e=>setStagingEnabled(e.target.checked)}/><span>Enable staging quote tests for this project</span></label>
        {stagingEnabled && <>
          <Field label="Staging origin"><input type="url" required value={stagingOrigin} onChange={e=>setStagingOrigin(e.target.value)} placeholder="https://staging.myshop.example"/></Field>
          <Field label="Deployment challenge (copy into staging manifest)"><input required minLength={24} maxLength={80} value={stagingChallenge} onChange={e=>setStagingChallenge(e.target.value)} placeholder="Generate a 32-character random token"/></Field>
          <Button onClick={()=>setStagingChallenge(Array.from(crypto.getRandomValues(new Uint8Array(24)),x=>x.toString(16).padStart(2,'0')).join(''))}>Generate challenge</Button>
          <Field label="Synthetic test SKU"><input required value={stagingSku} onChange={e=>setStagingSku(e.target.value)} placeholder="TEST-ITEM"/></Field>
          <Field label="Expected price in minor currency units (e.g. cents)"><input type="number" required min={1} max={10000000} value={stagingUnitPrice} onChange={e=>setStagingUnitPrice(Number(e.target.value))}/></Field>
          <label className="checkbox"><input type="checkbox" checked={stagingAcknowledged} onChange={e=>setStagingAcknowledged(e.target.checked)}/><span>I operate this staging deployment; its test SKU cannot create real orders or charge payments.</span></label>
          <p className="muted">Saving adds two exact read-only contract paths to project permissions. A separately approved Chat task is required to send the test requests.</p>
        </>}
      </details>
      <details className="advanced-options"><summary>Advanced limits and permissions</summary><p className="muted">Defaults: 1 request per second, 2 at a time, and 1,000 per run. Uncertain requests need your approval.</p>
        <Field label="Excluded URLs"><textarea rows={2} value={exclusions} onChange={e => setExclusions(e.target.value)} placeholder="https://shop.example.com/logout"/></Field>
        <Field label="Known read-only paths" hint="Only these paths may run automatically. Leave empty to review all requests."><textarea rows={2} value={reads} onChange={e => setReads(e.target.value)} placeholder="/catalog\n/products"/></Field>
        <div className="form-grid three"><Field label="Requests per second"><input type="number" min="0.1" max="20" step="0.1" value={rps} onChange={e => setRps(Number(e.target.value))}/></Field><Field label="Concurrent requests"><input type="number" min="1" max="10" value={concurrency} onChange={e => setConcurrency(Number(e.target.value))}/></Field><Field label="Requests per run"><input type="number" min="1" max="100000" value={budget} onChange={e => setBudget(Number(e.target.value))}/></Field></div>
        <label className="checkbox"><input type="checkbox" checked={privateAllowed} onChange={e => setPrivateAllowed(e.target.checked)}/><span>Allow the private or local addresses listed above for my test environment</span></label>
      </details><div className="form-actions"><Button onClick={() => setEditing(null)}>Cancel</Button><Button type="submit" variant="primary" disabled={!!busy}>Save project</Button></div>
    </form></Modal>}
    {deleting && <Modal title={`Delete ${deleting.name}?`} onClose={() => setDeleting(null)}><p>This removes this project's captured requests, suggested checks, approvals, reports, and run history. Running work for this project will stop.</p><p className="delete-note">Your learning library, other projects, and report files already exported are kept. This cannot be undone. Requests already sent cannot be recalled.</p><div className="form-actions"><Button onClick={() => setDeleting(null)}>Keep project</Button><Button variant="danger" icon={Trash2} disabled={!!busy} onClick={() => perform('Deleting project', () => rpc('projects.delete', { id: deleting.id }), 'Project deleted. Your learning library is unchanged.').then(value => { if (value) { if (projectId === deleting.id) selectProject(data.projects.find(p => p.id !== deleting.id)?.id || ''); setDeleting(null); } })}>Delete project</Button></div></Modal>}
  </>;
}

function Traffic({ data, projectId, perform, busy, navigate }: Shared) {
  const [selected, setSelected] = useState<Row | null>(null); const [showImport, setShowImport] = useState(false); const [content, setContent] = useState(''); const [format, setFormat] = useState('raw'); const [baseUrl, setBaseUrl] = useState(''); const [sessionLabel, setSessionLabel] = useState('default'); const [filter, setFilter] = useState(''); const [tab, setTab] = useState<'Request' | 'Response'>('Request');
  const rows = data.traffic.filter(r => `${r.request?.method} ${r.request?.url}`.toLowerCase().includes(filter.toLowerCase()));
  const chosen = data.traffic.find(t => t.id === selected?.id) || selected;
  return <><div className="toolbar"><div className="search"><Search size={17}/><input aria-label="Filter traffic" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter by URL or method…"/></div><div className="button-group"><Button icon={RefreshCw} disabled={!projectId || !!busy} onClick={() => perform('Reading Burp history', () => rpc('tools.burp_history', { project_id: projectId }), 'Burp history imported.')}>Read Burp history</Button><Button variant="primary" icon={Upload} onClick={() => setShowImport(true)} disabled={!projectId}>Import requests</Button></div></div><section className="surface split-view"><div className="list-pane">{!rows.length ? <Empty icon={Activity} title="No captured traffic yet" description="Import a HAR, Burp XML, or raw request, or read history from your connected Burp instance."/> : rows.map(row => <button className={`traffic-row ${chosen?.id === row.id ? 'selected' : ''}`} key={row.id} onClick={() => setSelected(row)}><div><span className={`http-method ${row.request?.method?.toLowerCase()}`}>{row.request?.method}</span><span className="http-status">{row.response?.status || row.response?.status_code || '—'}</span></div><strong>{row.request?.url}</strong><small>{row.session_id || 'No session label'} · {shortDate(row.created_at)}</small></button>)}</div><div className="inspector">{!chosen ? <Empty icon={Code2} title="Inspect an exchange" description="Select a captured request to examine the message and propose tests."/> : <><div className="inspector-header"><div><span className="http-method">{chosen.request?.method}</span><h3>{chosen.request?.url}</h3></div><Button icon={FlaskConical} variant="primary" disabled={!!busy} onClick={() => perform('Proposing tests', () => rpc('tests.propose', { traffic_id: chosen.id }), 'Test hypotheses created.').then(value => value && navigate('Tests'))}>Suggest checks</Button></div><div className="tabs" role="tablist">{(['Request', 'Response'] as const).map(t => <button role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)} key={t}>{t}</button>)}<Button icon={ArrowUpRight} onClick={() => perform('Opening Repeater tab', () => rpc('tools.repeater', { traffic_id: chosen.id }), 'Request sent to a Burp Repeater tab.')}>Open in Repeater</Button></div><Code value={httpText(tab === 'Request' ? chosen.request : chosen.response)}/><div className="inspector-meta"><DetailItem label="Evidence ID" value={chosen.id}/><DetailItem label="Captured identity" value={chosen.session_id || 'Not labeled'}/><DetailItem label="Source" value={chosen.source || chosen.import_format || 'Imported traffic'}/></div></>}</div></section>
    {showImport && <Modal title="Import captured requests" wide onClose={() => setShowImport(false)}><form onSubmit={e => { e.preventDefault(); perform('Importing traffic', () => rpc('traffic.import', { project_id: projectId, format, content, base_url: baseUrl, session_id: sessionLabel }), 'Traffic imported.').then(value => { if (value !== undefined) { setShowImport(false); setContent(''); } }); }}><div className="form-grid"><Field label="Format"><select value={format} onChange={e => setFormat(e.target.value)}><option value="raw">Raw HTTP</option><option value="har">HAR</option><option value="burp_xml">Burp XML</option></select></Field><Field label="Load from file"><input type="file" accept=".har,.json,.xml,.txt,.http" onChange={async e => { const file = e.target.files?.[0]; if (file) { setContent(await file.text()); if (file.name.endsWith('.har') || file.name.endsWith('.json')) setFormat('har'); if (file.name.endsWith('.xml')) setFormat('burp_xml'); } }}/></Field></div>{format === 'raw' && <Field label="Base URL" hint="Used for raw request lines with relative paths."><input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="https://shop.example.com"/></Field>}<Field label="Account / session label" hint="Use a distinct label for each authorized test account."><input required value={sessionLabel} onChange={e => setSessionLabel(e.target.value)} placeholder="test-account-a"/></Field><Field label="Captured content"><textarea className="mono" rows={12} required value={content} onChange={e => setContent(e.target.value)} placeholder={'GET /catalog HTTP/1.1\nHost: shop.example.com\n\n'}/></Field><div className="form-actions"><Button onClick={() => setShowImport(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={!content || !!busy}>Import requests</Button></div></form></Modal>}
  </>;
}

function Tests({ data, projectId, revision, perform, busy, navigate }: Shared) {
  const [coverage, setCoverage] = useState<Row[]>([]);
  useEffect(() => { if (projectId) rpc('tests.coverage', { project_id: projectId }).then(asList).then(setCoverage).catch(() => {}); }, [projectId, revision, data.actions]);
  const [selected, setSelected] = useState<Row | null>(null); const [category, setCategory] = useState('all'); const [queueing, setQueueing] = useState<Row | null>(null); const [requestDraft, setRequestDraft] = useState(''); const [requestError, setRequestError] = useState('');
  const categories = [...new Set(data.tests.map(t => t.category).filter(Boolean))]; const rows = data.tests.filter(t => category === 'all' || t.category === category); const chosen = data.tests.find(t => t.id === selected?.id) || selected;
  return <><div className="coverage-summary" aria-label="Test coverage">{coverage.map(c => <div key={c.category} title={c.description}><strong>{sentence(c.category)}</strong><Status value={c.status}/><small>{c.executed} tested · {c.blocked || 0} blocked · {c.untested || 0} untested</small></div>)}</div><div className="toolbar"><div className="filter-chips"><button className={category === 'all' ? 'active' : ''} onClick={() => setCategory('all')}>All suggested checks <span>{data.tests.length}</span></button>{categories.map(c => <button className={category === c ? 'active' : ''} onClick={() => setCategory(c)} key={c}>{sentence(c)}</button>)}</div></div><section className="surface split-view"><div className="list-pane">{!rows.length ? <Empty icon={FlaskConical} title="Choose a captured request" description="Open Captured requests, select one, and choose Suggest checks. Each suggestion explains what to change and how to verify it."><Button onClick={() => navigate('Traffic')}>Open captured requests</Button></Empty> : rows.map(test => <button key={test.id} className={`test-row ${chosen?.id === test.id ? 'selected' : ''}`} onClick={() => setSelected(test)}><div className="row-between"><span className="category">{sentence(test.category)}</span><Status value={test.status || 'hypothesis'}/></div><strong>{test.title}</strong><p>{test.rationale}</p><small>{test.field || 'Workflow-level test'}</small></button>)}</div><div className="inspector">{!chosen ? <Empty icon={ListChecks} title="A suggestion needs evidence" description="Inspect the prerequisites and confirmation criteria before queuing a request."/> : <div className="test-detail"><div className="row-between"><span className="category">{sentence(chosen.category)}</span><Status value={chosen.status || 'hypothesis'}/></div><h2>{chosen.title}</h2><DetailItem label="Why test this" value={chosen.rationale}/><DetailItem label="Field or workflow" value={chosen.field}/><DetailItem label="Proposed change" value={chosen.proposed_change}/><DetailItem label="Prerequisites" value={chosen.prerequisites}/><DetailItem label="Expected secure behavior" value={chosen.expected_secure_behavior}/><DetailItem label="Evidence needed to confirm" value={chosen.confirmation_criteria}/><DetailItem label="Supporting lessons" value={chosen.citations?.length ? chosen.citations : 'No matching lesson yet. This hypothesis needs independent verification.'}/>{(chosen.request || chosen.proposed_request) && <Code label="Proposed request" value={httpText(chosen.request || chosen.proposed_request)}/>}<div className="sticky-actions"><Button variant="primary" icon={ListChecks} disabled={!!busy} onClick={() => { setQueueing(chosen); setRequestDraft(pretty(chosen.proposed_request || chosen.request)); setRequestError(''); }}>Review this check</Button></div></div>}</div></section>{queueing && <Modal title="Prepare the test request" wide onClose={() => setQueueing(null)}><DetailItem label="Prerequisites" value={queueing.prerequisites}/><Field label="Proposed HTTP request (JSON)" hint="Replace placeholders with your authorized test values. The next step reviews the normalized exact request before sending."><textarea className="mono" rows={15} value={requestDraft} onChange={e => { setRequestDraft(e.target.value); setRequestError(''); }}/></Field>{requestError && <p className="error-text" role="alert">{requestError}</p>}<div className="form-actions"><Button onClick={() => setQueueing(null)}>Cancel</Button><Button variant="primary" disabled={!!busy} onClick={() => { try { const request = JSON.parse(requestDraft); const changed = JSON.stringify(request) !== JSON.stringify(queueing.proposed_request || queueing.request); if (queueing.requires_input && !changed) throw new Error('Complete the prerequisites and edit this request with your test values before queuing.'); perform('Queuing proposed test', () => rpc('tests.queue', { id: queueing.id, ...(changed ? { request } : {}) }), 'Added to Request approvals. Review the exact request before sending.').then(value => { if (value) { setQueueing(null); navigate('Approvals'); } }); } catch (error) { setRequestError(errorMessage(error)); } }}>Add to approvals</Button></div></Modal>}</>;
}

function Approvals({ data, perform, busy, navigate }: Shared) {
  const actionBusy = ['Denying request', 'Approving exact request', 'Sending approved request', 'Canceling action', 'Assessing evidence'].includes(busy);
  const [selected, setSelected] = useState<Row | null>(null); const [showAll, setShowAll] = useState(false);
  const rows = data.actions.filter(a => showAll || pending(a) || a.status === 'approved' || a.status === 'ready'); const chosen = data.actions.find(a => a.id === selected?.id) || selected;
  return <><div className="toolbar"><span className="muted">{data.actions.filter(pending).length} requests awaiting review</span><label className="checkbox compact"><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)}/>Show action history</label></div><section className="surface split-view"><div className="list-pane">{!rows.length ? <Empty icon={ShieldCheck} title="Nothing awaiting your review" description="Requests that may change state will appear here with their exact destination, identity, and content."/> : rows.map(action => <button className={`test-row ${chosen?.id === action.id ? 'selected' : ''}`} key={action.id} onClick={() => setSelected(action)}><div className="row-between"><span className="http-method">{action.request?.method}</span><Status value={action.status}/></div><strong>{action.request?.url || action.title}</strong><p>{action.reason || action.effect}</p><small>{action.session_id || 'No session label'}</small></button>)}</div><div className="inspector">{!chosen ? <Empty icon={ShieldCheck} title="You decide what gets sent" description="An approval permits one exact request and identity. A changed request requires a new review."/> : <div className="test-detail"><div className="row-between"><h2>Request review</h2><Status value={chosen.status}/></div><div className="approval-note"><ShieldCheck size={19}/><p>{chosen.reason || 'Review the request and expected effects before permitting this action.'}</p></div><div className="form-grid"><DetailItem label="Identity / session" value={chosen.session_id || 'No session label'}/><DetailItem label="Expected effect" value={chosen.effect || 'Unknown; review required'}/></div><Code label="Exact outbound request" value={httpText(chosen.request)}/><DetailItem label="Request fingerprint" value={chosen.request_hash || chosen.fingerprint}/><DetailItem label="Action ID" value={chosen.id}/><DetailItem label="Evidence IDs" value={chosen.evidence_ids}/><DetailItem label="Execution result" value={chosen.error || chosen.result || chosen.response}/><div className="sticky-actions">{chosen.status === 'completed' && <Button variant="primary" icon={FileText} disabled={actionBusy} onClick={() => perform('Assessing evidence', () => rpc('tests.assess', { action_id: chosen.id }), 'Evidence assessed and finding saved.').then(value => value && navigate('Findings'))}>Assess evidence</Button>}{pending(chosen) && <><Button variant="danger-quiet" icon={X} disabled={actionBusy} onClick={() => perform('Denying request', () => rpc('actions.deny', { id: chosen.id }), 'Request denied.')}>Deny</Button><Button variant="primary" icon={Check} disabled={actionBusy} onClick={() => perform('Approving exact request', () => rpc('actions.approve', { id: chosen.id }), 'Approved for one send. Review remains bound to this request.')}>Approve exact request</Button></>}{['approved', 'ready', 'queued_read_only'].includes(chosen.status) && <Button variant="primary" icon={Play} disabled={actionBusy} onClick={() => perform('Sending approved request', () => rpc('actions.execute', { id: chosen.id }), 'Request released. Inspect action history for the recorded outcome.')}>Send once</Button>}{!['completed', 'denied', 'cancelled', 'canceled', 'failed'].includes(chosen.status) && <Button onClick={() => perform('Canceling action', () => rpc('actions.cancel', { id: chosen.id }), 'Action canceled.')} disabled={actionBusy}>Cancel action</Button>}</div></div>}</div></section></>;
}

function Findings({ data, projectId, perform, busy }: Shared) {
  const [editing, setEditing] = useState<Row | null>(null); const [title, setTitle] = useState(''); const [status, setStatus] = useState('hypothesis'); const [summary, setSummary] = useState(''); const [impact, setImpact] = useState(''); const [steps, setSteps] = useState(''); const [evidence, setEvidence] = useState(''); const [confirmation, setConfirmation] = useState(''); const [controls, setControls] = useState(''); const [reviewed, setReviewed] = useState(false); const [exported, setExported] = useState<Row | null>(null);
  function edit(row: Row) { setEditing(row); setTitle(row.title || ''); setStatus(row.status || 'hypothesis'); setSummary(row.summary || ''); setImpact(row.impact || ''); setSteps(Array.isArray(row.steps) ? row.steps.join('\n') : row.steps || ''); setEvidence((row.evidence_ids || []).join('\n')); setConfirmation(row.confirmation || ''); setControls(row.controls || ''); setReviewed(row.reviewed || false); }
  async function save(e: FormEvent) { e.preventDefault(); const value = await perform('Saving finding', () => rpc('findings.save', { ...editing, project_id: editing?.project_id || projectId, title, status, summary, impact, steps: steps.split('\n').filter(Boolean), evidence_ids: lineList(evidence), confirmation, controls, reviewed }), 'Finding saved.'); if (value) setEditing(null); }
  return <><div className="toolbar"><span className="muted">{data.findings.length} findings · {data.findings.filter(f => f.status === 'confirmed').length} confirmed</span><Button variant="primary" icon={Plus} disabled={!projectId} onClick={() => edit({})}>Write a report</Button></div><section className="surface">{!data.findings.length ? <Empty icon={FileText} title="Build a report from evidence" description="Keep hypotheses separate from confirmed defects. A useful report explains impact, reproduction steps, and what the server actually accepted."/> : data.findings.map(finding => <article className="finding-row" key={finding.id}><div className="row-between"><div><Status value={finding.status}/><h2><button onClick={() => edit(finding)}>{finding.title}</button></h2></div><div className="button-group"><Button onClick={() => edit(finding)}>Edit</Button>{['confirmed', 'false_positive'].includes(finding.status) && <Button disabled={!!busy} onClick={() => perform('Saving reviewed lesson', () => rpc('findings.learn', { id: finding.id }), 'Redacted finding saved to the learning library.')}>Save as lesson</Button>}<select aria-label={`Export ${finding.title}`} value="" onChange={e => { const format = e.target.value; if (format) perform('Exporting report', () => rpc('findings.export', { id: finding.id, format }), 'Redacted report exported.').then(value => value && setExported(value)); }}><option value="">Export report…</option><option value="markdown">Markdown</option><option value="html">HTML</option><option value="json">JSON evidence</option></select></div></div><p>{finding.summary}</p><div className="finding-meta"><span><FileText size={14}/>{finding.evidence_ids?.length || 0} evidence records</span><span>{shortDate(finding.updated_at)}</span></div></article>)}</section>
    {editing && <Modal title={editing.id ? 'Edit finding' : 'Write a report'} wide onClose={() => setEditing(null)}><form onSubmit={save}><Field label="Report title"><input required value={title} onChange={e => setTitle(e.target.value)} placeholder="Excluded product receives an ineligible coupon discount"/></Field><Field label="Status" hint="Confirmed findings require supporting execution evidence."><select value={status} onChange={e => setStatus(e.target.value)}>{['hypothesis','observed','confirmed','false_positive','inconclusive'].map(s => <option key={s} value={s}>{sentence(s)}</option>)}</select></Field><Field label="Summary"><textarea required rows={3} value={summary} onChange={e => setSummary(e.target.value)}/></Field><Field label="Impact"><textarea rows={2} value={impact} onChange={e => setImpact(e.target.value)}/></Field><Field label="Reproduction steps" hint="One step per line; include prerequisites and expected versus actual behavior."><textarea rows={5} value={steps} onChange={e => setSteps(e.target.value)}/></Field><Field label="Evidence IDs" hint="Reference captured observations or execution records, one per line."><textarea className="mono" rows={3} value={evidence} onChange={e => setEvidence(e.target.value)}/></Field>{status === 'confirmed' && <><Field label="Confirmation evidence" hint="Explain the server-accepted outcome and why it violates the expected rule."><textarea required rows={4} value={confirmation} onChange={e => setConfirmation(e.target.value)}/></Field><Field label="Control comparison" hint="Explain the baseline, fixed behavior, or second-account control."><textarea required rows={3} value={controls} onChange={e => setControls(e.target.value)}/></Field><label className="checkbox"><input type="checkbox" required checked={reviewed} onChange={e => setReviewed(e.target.checked)}/><span>I reviewed the referenced evidence and verified the reported impact.</span></label></>}<div className="form-actions"><Button onClick={() => setEditing(null)}>Cancel</Button><Button type="submit" variant="primary" disabled={!!busy}>Save report</Button></div></form></Modal>}
    {exported && <Modal title="Exported report" wide onClose={() => setExported(null)}><p>{exported.path || exported.filename}</p><Code value={exported.content}/><div className="form-actions"><Button icon={ArrowDownToLine} onClick={() => { const blob = new Blob([exported.content], { type: 'text/plain;charset=utf-8' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = exported.filename || 'finding.txt'; link.click(); URL.revokeObjectURL(link.href); }}>Download copy</Button><Button onClick={() => setExported(null)}>Done</Button></div></Modal>}
  </>;
}

function Settings({perform,busy,revision,projectId,system,onSystemChange}: {perform:Execute;busy:string;revision:number;projectId:string;projects:Row[];system:Row;onSystemChange:(value:Row)=>void}) {
  return <SettingsView perform={perform} busy={busy} revision={revision} projectId={projectId} system={system} onSystemChange={onSystemChange}/>;
}
