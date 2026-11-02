import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, BookOpen, Check, FileText, Hammer, LoaderCircle, MessageSquareText, Paperclip, Plus, Search, Trash2, X } from 'lucide-react';
import { fileBase64, rpc } from './api';
import type { RecordData as Row } from './api';
type RecordData = Row;
import { Button, Modal } from './components';

type ChatProps = { navigate: (page: 'Settings') => void; projectId?: string; projectName?: string };
type Message = { role: 'user' | 'assistant'; content: string; run_id?: string; pending?: boolean };
type Session = RecordData & { id: string; title: string; messages: Message[]; source_ids?: string[] };
type Activity = { type: string; text: string };
const MAX_ATTACHMENTS = 6;
const MAX_FILE_BYTES = 64 * 1024 * 1024;
const ready = (source: RecordData) => (source.chunk_count > 0 && !['queued', 'failed', 'error'].includes(source.status)) || (/\.(?:exe|dll|apk|ipa|so|dylib|bin|jar)$/i.test(source.filename || '') && source.original_retained !== false && !!source.path);
const displayName = (source: RecordData) => source.title || source.filename || 'Resource';

export default function Chat({ navigate, projectId, projectName }: ChatProps) {
  const [history, setHistory] = useState<RecordData[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const [provider, setProvider] = useState<RecordData>({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [sources, setSources] = useState<RecordData[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [showSources, setShowSources] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [activity, setActivity] = useState<Activity[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scopeVersion = useRef(0);
  const loadedScopeKey = useRef<string | null>(null);
  const selectionVersion = useRef(0);
  const polling = useRef(false);
  const scopeKey = `bughunter.chat.${projectId || 'library'}`;

  const refreshHistory = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('chat.list', { project_id: projectId || null });
    if (version === scopeVersion.current) setHistory(rows);
  };
  const refreshSources = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('sources.list', { project_id: projectId || null });
    if (version === scopeVersion.current) setSources(rows);
  };
  useEffect(() => {
    const version = ++scopeVersion.current;
    loadedScopeKey.current = null;
    polling.current = false;
    let alive = true;
    selectionVersion.current++;
    setSession(null); setHistory([]); setSources([]); setText(''); setActivity([]);
    setSelectedSourceIds([]); setWebSearch(false); setLoading(true); setError('');
    setSending(false); setImporting(false); setShowSources(false);
    const load = async () => {
      try {
        const [list, settings, library] = await Promise.all([
          rpc<RecordData[]>('chat.list', { project_id: projectId || null }), rpc<RecordData>('settings.get'),
          rpc<RecordData[]>('sources.list', { project_id: projectId || null }),
        ]);
        if (!alive || version !== scopeVersion.current) return;
        setHistory(list); setProvider(settings); setSources(library);
        const saved = localStorage.getItem(scopeKey);
        if (saved && list.some(item => item.id === saved)) {
          const loaded = await rpc<Session>('chat.get', { id: saved });
          if (!alive || version !== scopeVersion.current) return;
          setSession(loaded); setSelectedSourceIds(loaded.source_ids || []);
        }
      } catch (err) {
        if (alive && version === scopeVersion.current) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive && version === scopeVersion.current) { loadedScopeKey.current = scopeKey; setLoading(false); }
      }
    };
    void load();
    return () => { alive = false; polling.current = false; scopeVersion.current++; };
  }, [projectId]);
  useEffect(() => {
    if (!loading && loadedScopeKey.current === scopeKey) {
      if (session?.id) localStorage.setItem(scopeKey, session.id);
      else localStorage.removeItem(scopeKey);
    }
  }, [session?.id, loading, scopeKey]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end', behavior: 'auto' }); }, [session?.messages?.length, activity.length, sending]);
  useEffect(() => {
    if (!selectedSourceIds.some(id => !ready(sources.find(s => s.id === id) || {}))) return;
    const timer = setInterval(() => { void refreshSources().catch(() => {}); }, 3500);
    return () => clearInterval(timer);
  }, [selectedSourceIds, sources]);

  const selectedSources = useMemo(() => selectedSourceIds.map(id => sources.find(s => s.id === id) || { id, title: 'Unavailable resource', status: 'error' }), [sources, selectedSourceIds]);
  const notIndexed = selectedSources.some(source => !ready(source));
  const filteredHistory = history.filter(chat => String(chat.title || 'Chat').toLowerCase().includes(filter.toLowerCase()));
  const canSend = !loading && !sending && !importing && !notIndexed && !!text.trim();

  function newChat() {
    if (sending) return;
    selectionVersion.current++; setSession(null); setSelectedSourceIds([]); setText(''); setError(''); setActivity([]); setShowHistory(false);
  }
  async function selectChat(id: string) {
    if (sending) return;
    const version = scopeVersion.current;
    const selection = ++selectionVersion.current;
    setError(''); setActivity([]);
    try {
      const loaded = await rpc<Session>('chat.get', { id });
      if (version !== scopeVersion.current || selection !== selectionVersion.current) return;
      setSession(loaded); setSelectedSourceIds(loaded.source_ids || []); setShowHistory(false);
      void refreshSources().catch(() => {});
    } catch (e) { if (version === scopeVersion.current && selection === selectionVersion.current) setError(String(e instanceof Error ? e.message : e)); }
  }

  // One message -> one autonomous agent run. Stream its tool calls and answer live.
  async function send() {
    if (!canSend) return;
    const version = scopeVersion.current;
    const message = text.trim();
    setSending(true); setError(''); setActivity([]);
    try {
      const started = await rpc<{ session: Session; run_id: string; cursor: number }>('chat.agent', {
        id: session?.id, message, project_id: projectId || undefined,
        source_ids: selectedSourceIds,
      });
      if (version !== scopeVersion.current) return;
      setSession(started.session); setText(''); await refreshHistory();
      await streamRun(started.session.id, started.run_id, started.cursor, version);
    } catch (e) {
      if (version !== scopeVersion.current) return;
      setError(String(e instanceof Error ? e.message : e)); setSending(false);
    }
  }

  async function streamRun(sessionId: string, runId: string, cursor: number, version: number) {
    polling.current = true;
    let lastSeq = cursor;
    let webNoted = webSearch; // web_search context is folded into the agent prompt server-side; flag reset per send
    void webNoted;
    const tick = async () => {
      if (!polling.current || version !== scopeVersion.current) { setSending(false); return; }
      try {
        const events = await rpc<RecordData[]>('events.list', { after: lastSeq, limit: 200 });
        const mine: Activity[] = [];
        for (const row of events) {
          if (row.seq > lastSeq) lastSeq = row.seq;
          if (row.type === 'run.event' && row.data?.run_id === runId && row.data?.text) {
            mine.push({ type: String(row.data.type || 'info'), text: String(row.data.text) });
          }
        }
        if (mine.length) setActivity(prev => [...prev, ...mine]);
        const final = await rpc<{ terminal: boolean; session: Session }>('chat.finalize', { id: sessionId, run_id: runId });
        if (version !== scopeVersion.current) { setSending(false); return; }
        if (final.terminal) {
          polling.current = false;
          setSession(final.session); setActivity([]); setSending(false);
          await refreshHistory();
          return;
        }
      } catch {
        // transient poll error; keep trying until the run terminates
      }
      if (polling.current && version === scopeVersion.current) setTimeout(() => void tick(), 1200);
    };
    await tick();
  }

  async function deleteChat(id: string) {
    if (sending) return;
    try {
      await rpc('chat.delete', { id });
      if (session?.id === id) newChat();
      await refreshHistory();
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }
  function toggleSource(id: string) {
    setSelectedSourceIds(current => current.includes(id) ? current.filter(v => v !== id) : current.length >= MAX_ATTACHMENTS ? current : [...current, id]);
  }
  async function addFiles(files: FileList | File[]) {
    const version = scopeVersion.current;
    const chosen = Array.from(files);
    if (!chosen.length) return;
    if (chosen.length + selectedSourceIds.length > MAX_ATTACHMENTS) { setError(`Choose up to ${MAX_ATTACHMENTS} resources per conversation.`); return; }
    if (chosen.some(f => f.size > MAX_FILE_BYTES)) { setError('Choose files smaller than 64 MB, or import large files in Library.'); return; }
    setImporting(true); setError('');
    try {
      const imported: RecordData[] = [];
      for (const file of chosen) {
        const resource = await rpc<RecordData>('sources.import', { filename: file.name, content_base64: await fileBase64(file), project_id: projectId || null });
        imported.push(resource);
      }
      if (version !== scopeVersion.current) return;
      setSources(prev => [...imported.filter(row => !prev.some(old => old.id === row.id)), ...prev]);
      setSelectedSourceIds(prev => [...new Set([...prev, ...imported.map(row => row.id)])].slice(0, MAX_ATTACHMENTS));
    } catch (e) { if (version !== scopeVersion.current) return; setError(String(e instanceof Error ? e.message : e)); }
    finally { if (version === scopeVersion.current) { setImporting(false); if (fileInput.current) fileInput.current.value = ''; } }
  }

  const messages = session?.messages || [];
  return <section className="conversation">
    <div className="conversation-bar">
      <button type="button" className="conversation-new" onClick={newChat} disabled={sending}><Plus size={17}/> New chat</button>
      <div className="conversation-bar-meta">{projectName ? <span className="conversation-project" title="Active project">{projectName}</span> : <span className="conversation-project muted">All knowledge</span>}<button type="button" className="conversation-history-toggle" onClick={() => setShowHistory(v => !v)}>{showHistory ? 'Hide' : 'History'}</button></div>
    </div>
    {showHistory && <div className="conversation-history">
      <label className="conversation-search"><Search size={16}/><input placeholder="Search chats…" value={filter} onChange={e => setFilter(e.target.value)}/></label>
      {!filteredHistory.length ? <p className="muted">No conversations yet.</p> : filteredHistory.map(chat => <div key={chat.id} className={`conversation-history-row ${session?.id === chat.id ? 'active' : ''}`}><button onClick={() => selectChat(chat.id)}>{chat.title || 'Chat'}</button><button className="icon-button" aria-label="Delete chat" onClick={() => deleteChat(chat.id)}><Trash2 size={14}/></button></div>)}
    </div>}

    <div className="conversation-scroll">
      {!messages.length && !sending ? <div className="conversation-welcome"><div className="conversation-welcome-icon"><MessageSquareText size={30}/></div><h2>What should I do?</h2><p>Describe the task. The agent picks the tools, gathers real evidence, acts autonomously, and reports back — you watch it work live.</p><div className="conversation-starters">
        {['Analyze my uploaded game file and draft a trainer', 'Audit my authorized website for business-logic flaws', 'Study my uploaded course and record the techniques', 'Search my knowledge for SSRF testing methods'].map(prompt => <button key={prompt} onClick={() => setText(prompt)}>{prompt}</button>)}
      </div></div> : <div className="conversation-transcript">
        {messages.map((entry, i) => entry.pending ? null : <article className={`conversation-message ${entry.role}`} key={i}><div className="conversation-avatar">{entry.role === 'user' ? 'You' : 'AI'}</div><div className="conversation-content"><span className="conversation-author">{entry.role === 'user' ? 'You' : provider.model || 'Agent'}</span><div className="conversation-text">{entry.content}</div></div></article>)}
      </div>}
      {sending && <div className="agent-live"><div className="agent-live-head"><LoaderCircle className="spin" size={16}/> <strong>Agent working…</strong></div>
        {!activity.length ? <p className="muted">Starting up — reading the request and checking available tools.</p> : <ol className="agent-live-steps">{activity.map((step, i) => <li key={i} className={`agent-step ${step.type}`}>{step.type === 'tool' ? <><Hammer size={13}/> <span className="agent-step-tool">{step.text}</span></> : <span className="agent-step-text">{step.text}</span>}</li>)}</ol>}
      </div>}
      <div ref={bottom}/>
    </div>

    <div className="conversation-compose-wrap">
      {error && <div role="alert" className="conversation-error">{error}<button type="button" aria-label="Dismiss error" onClick={() => setError('')}><X size={15}/></button></div>}
      {selectedSources.length > 0 && <div className="conversation-attachments" aria-label="Attached knowledge resources">{selectedSources.map(source => <div key={source.id} className={`conversation-attachment ${ready(source) ? 'ready' : 'waiting'}`}><FileText size={17}/><span title={displayName(source)}>{displayName(source)}<small>{ready(source) ? 'Indexed' : source.status === 'error' || source.status === 'failed' ? 'Import failed' : 'Indexing…'}</small></span><button type="button" aria-label={`Remove ${displayName(source)}`} onClick={() => toggleSource(source.id)}><X size={14}/></button></div>)}</div>}
      <form className="conversation-composer" onSubmit={e => { e.preventDefault(); void send(); }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files); }}>
        <textarea aria-label="Message the agent" rows={3} maxLength={12000} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }} placeholder={sending ? 'Agent is working…' : 'Tell the agent what to do…'} disabled={sending}/>
        <div className="conversation-actions simple-conversation-actions"><div className="conversation-actions-left">
          <input ref={fileInput} type="file" multiple className="sr-only" aria-label="Upload documents" onChange={e => void addFiles(e.target.files || [])}/>
          <button type="button" title="Attach files from your computer" onClick={() => fileInput.current?.click()} disabled={sending || importing}><Paperclip size={18}/> <span>Attach</span></button>
          <button type="button" title="Choose existing knowledge" onClick={() => { void refreshSources(); setShowSources(true); }} disabled={sending}><BookOpen size={18}/> <span>Knowledge</span></button>
          <button type="button" className={webSearch ? 'chat-web-active' : ''} aria-pressed={webSearch} title="Let the agent search the public web" onClick={() => setWebSearch(v => !v)} disabled={sending}><Search size={18}/> <span>Web {webSearch ? 'on' : 'off'}</span></button>
        </div><button className="conversation-send" aria-label="Send" title={notIndexed ? 'Wait for indexing to finish' : 'Send'} type="submit" disabled={!canSend}>{sending || importing ? <LoaderCircle className="spin" size={18}/> : <ArrowUp size={19}/>}</button></div>
      </form>
      <div className="conversation-footnote">{notIndexed ? 'Attached resources are still indexing. The agent can read them once ready.' : 'Enter to send · Shift+Enter for a newline · The agent runs tools and acts on its own'}{provider.provider && provider.provider !== 'ollama' && provider.provider !== 'remote_ollama' && provider.provider !== 'crucible' ? ' · Sent to your configured AI provider.' : ''}{!provider.model ? ' · Set a model in Settings first.' : ''}</div>
    </div>

    {showSources && <Modal title="Choose from your knowledge" onClose={() => setShowSources(false)} wide><div className="conversation-source-modal"><p className="muted">Select up to six indexed resources for the agent to reference. They stay in your local library.</p><label className="conversation-search"><Search size={17}/><input placeholder="Search file names…" aria-label="Search knowledge files" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}/></label><div className="conversation-source-list">{sources.filter(s => displayName(s).toLowerCase().includes(sourceFilter.toLowerCase())).map(s => <label key={s.id} className="conversation-source-row"><input type="checkbox" checked={selectedSourceIds.includes(s.id)} disabled={!ready(s) || (!selectedSourceIds.includes(s.id) && selectedSourceIds.length >= MAX_ATTACHMENTS)} onChange={() => toggleSource(s.id)}/><FileText size={19}/><span><strong>{displayName(s)}</strong><small>{ready(s) ? 'Ready to read' : `${s.status || 'Processing'} · not ready`}</small></span>{selectedSourceIds.includes(s.id) && <Check size={17}/>}</label>)}{!sources.length && <p className="muted">No sources yet. Use Attach to add a document, or add one from Library.</p>}</div><div className="form-actions"><Button onClick={() => { navigate('Settings'); }}>Open Settings</Button><Button variant="primary" onClick={() => setShowSources(false)}>Done ({selectedSourceIds.length}/{MAX_ATTACHMENTS})</Button></div></div></Modal>}
  </section>;
}
