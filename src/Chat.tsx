import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronDown, Download, History, LoaderCircle, MessageSquarePlus, Paperclip, Plus, Search, Sparkles, Square, Trash2, X } from 'lucide-react';
import { fileBase64, rpc } from './api';
import { wantsMappingResearch } from './taskIntent';
import { mappingActive, useMappingChat } from './useMappingChat';
import MappingChatActivity from './MappingChatActivity';
import type { RecordData as Row } from './api';
type RecordData = Row;

type ChatProps = { navigate: (page: 'Settings' | 'Team') => void; projectId?: string; projectName?: string };
type Message = { role: 'user' | 'assistant'; content: string; run_id?: string; task_id?: string; pending?: boolean; citations?:{title:string;url?:string;source_id?:string}[] };
type Session = RecordData & { id: string; title: string; messages: Message[] };
type ProviderInfo = { provider: string; label: string; kind: string; ready: boolean; needs: string; models: string[]; active: boolean };
const MAX_FILE_BYTES = 64 * 1024 * 1024;

export default function Chat({ navigate, projectId, projectName }: ChatProps) {
  const [history, setHistory] = useState<RecordData[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const mapping = useMappingChat(session, setSession);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [activeTask, setActiveTask] = useState<RecordData | null>(null);
  const [taskId, setTaskId] = useState('');
  const [trackingError, setTrackingError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [attached, setAttached] = useState<RecordData[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activeModel, setActiveModel] = useState<{ provider: string; model: string }>({ provider: '', model: '' });
  const [pickerOpen, setPickerOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scopeVersion = useRef(0);
  const selectionVersion = useRef(0);
  const sendGuard = useRef(false);
  const scopeKey = `bughunter.chat.${projectId || 'library'}`;

  const refreshHistory = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('chat.list', { project_id: projectId || null });
    if (version === scopeVersion.current) setHistory(rows);
  };
  const refreshModels = async () => {
    try {
      const r = await rpc<{ providers: ProviderInfo[]; active: { provider: string; model: string } }>('models.available');
      setProviders(r.providers || []); setActiveModel(r.active || { provider: '', model: '' });
    } catch { /* settings not ready */ }
  };
  useEffect(() => {
    const version = ++scopeVersion.current;
    selectionVersion.current++;
    sendGuard.current = false;
    let alive = true;
    setSession(null); setHistory([]); setText(''); setActiveTask(null); setTaskId(''); setAttached([]);
    setWebSearch(false); setLoading(true); setError(''); setSending(false); setImporting(false);
    const load = async () => {
      try {
        const list = await rpc<RecordData[]>('chat.list', { project_id: projectId || null });
        if (!alive || version !== scopeVersion.current) return;
        setHistory(list);
        void refreshModels();
        const saved = localStorage.getItem(scopeKey);
        if (saved && list.some(item => item.id === saved)) {
          const loaded = await rpc<Session>('chat.get', { id: saved });
          if (!alive || version !== scopeVersion.current) return;
          setSession(loaded);
        }
      } catch (err) {
        if (alive && version === scopeVersion.current) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive && version === scopeVersion.current) setLoading(false);
      }
    };
    void load();
    return () => { alive = false; scopeVersion.current++; };
  }, [projectId]);
  useEffect(() => { if (!loading) { if (session?.id) localStorage.setItem(scopeKey, session.id); else localStorage.removeItem(scopeKey); } }, [session?.id, loading, scopeKey]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end', behavior: 'auto' }); }, [session?.messages?.length, sending]);
  useEffect(() => { setTaskId(session?.task_id || session?.run_id || ''); }, [session?.id, session?.task_id, session?.run_id]);
  const pendingRun = session?.messages.find(message => message.pending)?.run_id;
  useEffect(() => {
    if (!pendingRun || !session?.id) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const sessionId = session.id;
    setSending(true);
    const poll = async () => {
      try {
        const final = await rpc<{ terminal: boolean; session: Session }>('chat.finalize', { id: sessionId, run_id: pendingRun });
        if (!alive) return;
        setTrackingError('');
        if (final.terminal) {
          setSession(final.session); setSending(false); void refreshHistory(); return;
        }
      } catch (err) {
        if (alive) setTrackingError(`Progress connection interrupted. Retrying… ${err instanceof Error ? err.message : String(err)}`);
      }
      if (alive) timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [pendingRun, session?.id]);
  useEffect(() => {
    setActiveTask(null);
    if (!taskId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const task = await rpc<RecordData>('tasks.get', { id: taskId });
        if (!alive) return;
        setActiveTask(task); setTrackingError('');
        if (['completed', 'failed', 'cancelled', 'interrupted', 'blocked', 'limited'].includes(task.status)) return;
      } catch (err) {
        if (alive) setTrackingError(`Could not load task progress. Retrying… ${err instanceof Error ? err.message : String(err)}`);
      }
      if (alive) timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [taskId]);

  const filteredHistory = history.filter(chat => String(chat.title || 'Chat').toLowerCase().includes(filter.toLowerCase()));
  const canSend = !loading && !sending && !importing && !mapping.busy && !(session?.mapping_id && !mapping.data) && !!text.trim();
  const activeLabel = providers.find(p => p.provider === activeModel.provider)?.label || 'No model';

  function newChat() {
    if (sending) return;
    selectionVersion.current++;
    setSession(null); setText(''); setError(''); setActiveTask(null); setTaskId(''); setAttached([]); setHistoryOpen(false);
  }
  async function selectChat(id: string) {
    if (sending) return;
    const version = scopeVersion.current;
    const selection = ++selectionVersion.current;
    setError(''); setActiveTask(null); setAttached([]);
    try {
      const loaded = await rpc<Session>('chat.get', { id });
      if (version !== scopeVersion.current || selection !== selectionVersion.current) return;
      setSession(loaded); setHistoryOpen(false);
    } catch (e) { if (version === scopeVersion.current) setError(String(e instanceof Error ? e.message : e)); }
  }
  async function pickModel(provider: string, model: string) {
    try { await rpc('models.select', { provider, model }); setPickerOpen(false); await refreshModels(); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }

  async function send() {
    if (!canSend || sendGuard.current) return;
    sendGuard.current = true;
    const version = scopeVersion.current;
    const message = text.trim();
    setSending(true); setError(''); setActiveTask(null);
    try {
      if (session?.mapping_id && mappingActive(mapping.data?.workflow.status)) {
        const accepted = await mapping.reply(message, attached.map(a=>a.id));
        if (version !== scopeVersion.current) return;
        if (accepted) {setText('');setAttached([]);}
        setSending(false); await refreshHistory(); return;
      }
      if (wantsMappingResearch(message)) {
        const result = await rpc<{session:Session}>('chat.mapping.start', {id:session?.id,message,
          project_id:projectId || null,source_ids:attached.map(a=>a.id),search_web:webSearch});
        if (version !== scopeVersion.current) return;
        setSession(result.session);setText('');setAttached([]);setSending(false);await refreshHistory();return;
      }
      if (/^\s*(?:please\s+)?(?:run|check|audit|verify|test)\b/i.test(message)) {
        const plan = await rpc<RecordData>('autopilot.plan', { message, project_id: projectId || null, source_ids: attached.map(a => a.id) });
        if (version !== scopeVersion.current) return;
        if (['app_checks', 'knowledge_check'].includes(plan.workflow)) {
          if (plan.missing?.length) throw new Error(plan.missing.join(' '));
          const saved = await rpc<Session>('chat.autopilot', { id: session?.id, message, project_id: projectId || undefined, source_ids: attached.map(a => a.id) });
          if (version !== scopeVersion.current) return;
          setSession(saved); setText(''); setAttached([]); setSending(false); await refreshHistory(); return;
        }
      }
      const started = await rpc<{ session: Session; run_id: string; cursor: number }>('chat.agent', {
        id: session?.id, message, project_id: projectId || undefined,
        source_ids: attached.map(a => a.id), web_search: webSearch,
      });
      if (version !== scopeVersion.current) return;
      setSession(started.session); setText(''); setAttached([]); await refreshHistory();
    } catch (e) {
      if (version !== scopeVersion.current) return;
      setError(String(e instanceof Error ? e.message : e)); setSending(false);
    } finally { if (version === scopeVersion.current) sendGuard.current = false; }
  }
  async function stopTask() {
    try {
      if (session?.mapping_id && mappingActive(mapping.data?.workflow.status)) {
        await mapping.stop(); setSending(false); return;
      } else if (pendingRun && session) {
        const final = await rpc<{session: Session}>('chat.stop', {id: session.id, run_id: pendingRun});
        setSession(final.session); setSending(false);
      } else if (activeTask) {
        await rpc('tasks.cancel', {id: activeTask.id});
      }
      if (taskId) setActiveTask(await rpc<RecordData>('tasks.get', {id: taskId}));
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }
  async function downloadConversation() {
    if (!session) return;
    try {
      const report = await rpc<{filename: string; content: string}>('chat.export', {id: session.id});
      const url = URL.createObjectURL(new Blob([report.content], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = report.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }
  async function deleteChat(id: string) {
    if (sending) return;
    try { await rpc('chat.delete', { id }); if (session?.id === id) newChat(); await refreshHistory(); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }
  async function addFiles(files: FileList | File[]) {
    const version = scopeVersion.current;
    const chosen = Array.from(files);
    if (!chosen.length) return;
    if (chosen.length + attached.length > 6) { setError('Attach up to six files per message.'); return; }
    if (chosen.some(f => f.size > MAX_FILE_BYTES)) { setError('Attach files under 64 MB; add larger courses from Library.'); return; }
    setImporting(true); setError('');
    try {
      const imported: RecordData[] = [];
      for (const file of chosen) imported.push(await rpc<RecordData>('sources.import', { filename: file.name, content_base64: await fileBase64(file), project_id: projectId || null }));
      if (version !== scopeVersion.current) return;
      setAttached(prev => [...prev, ...imported]);
    } catch (e) { if (version !== scopeVersion.current) return; setError(String(e instanceof Error ? e.message : e)); }
    finally { if (version === scopeVersion.current) { setImporting(false); if (fileInput.current) fileInput.current.value = ''; } }
  }

  const messages = session?.messages || [];
  const taskIds: string[] = [...new Set<string>([...(session?.task_ids || []), ...(session?.task_id ? [session.task_id] : []), ...(session?.run_id ? [session.run_id] : [])])];
  const working = activeTask && ['starting','running','queued','cancelling','awaiting_approval'].includes(activeTask.status);
  return <div className={`chat-shell ${historyOpen ? 'show-chat-history' : ''}`}>
    <aside className="chat-side" aria-label="Saved conversations">
      <button type="button" className="chat-new" onClick={newChat} disabled={sending}><MessageSquarePlus size={17}/> New chat</button>
      <label className="chat-side-search"><Search size={15}/><input placeholder="Search chats" value={filter} onChange={e => setFilter(e.target.value)}/></label>
      <div className="chat-side-list">
        {!filteredHistory.length ? <p className="chat-side-empty">No chats yet</p> : filteredHistory.map(chat => <div key={chat.id} className={`chat-side-row ${session?.id === chat.id ? 'active' : ''}`}><button onClick={() => selectChat(chat.id)} title={chat.title || 'Chat'}>{chat.title || 'Chat'}</button><button className="chat-side-del" aria-label="Delete chat" onClick={() => deleteChat(chat.id)}><Trash2 size={13}/></button></div>)}
      </div>
    </aside>

    <section className="chat-main">
      <div className="chat-head">
        <button className="chat-tool-btn chat-history-toggle" aria-label="Chat history" aria-expanded={historyOpen} onClick={() => setHistoryOpen(v => !v)}><History size={18}/></button>
        <div className="model-picker">
          <button type="button" className="model-picker-btn" onClick={() => setPickerOpen(v => !v)} aria-haspopup="listbox" aria-expanded={pickerOpen}>
            <Sparkles size={15}/><span>{activeLabel}{activeModel.model ? <em>{activeModel.model}</em> : null}</span><ChevronDown size={15}/>
          </button>
          {pickerOpen && <><div className="model-picker-backdrop" onClick={() => setPickerOpen(false)}/><div className="model-picker-menu" role="listbox">
            {providers.map(p => <div key={p.provider} className={`model-group ${p.ready ? '' : 'disabled'}`}>
              <div className="model-group-head">{p.label}{!p.ready && <span className="model-group-add" onClick={() => { setPickerOpen(false); navigate('Settings'); }}>Add</span>}</div>
              {p.ready ? (p.models.length ? p.models : ['default']).map(m => { const on = activeModel.provider === p.provider && activeModel.model === m; return <button key={m} className={`model-opt ${on ? 'on' : ''}`} onClick={() => pickModel(p.provider, m)}>{m}{on && <Check size={15}/>}</button>; }) : <div className="model-need">{p.needs}</div>}
            </div>)}
          </div></>}
        </div>
        <span className="chat-scope">{projectName || 'All knowledge'}</span>{session && <button className="chat-tool-btn" aria-label="Download conversation and results" onClick={() => void downloadConversation()}><Download size={17}/></button>}
      </div>

      <div className="conversation-scroll">
        {!messages.length && !sending ? <div className="conversation-welcome"><div className="conversation-welcome-icon"><Sparkles size={28}/></div><h2>What should I do?</h2><p>Describe the task. Follow the investigation here, review proposed changes, and keep the results.</p><div className="conversation-starters">
          {['Run app checks', 'Check my knowledge base', 'Research mappings for my program', 'Study a course I upload', 'Audit my authorized website'].map(prompt => <button key={prompt} onClick={() => {setText(prompt);if(prompt==='Research mappings for my program') setWebSearch(true);}}>{prompt}</button>)}
        </div></div> : <div className="conversation-transcript">
          {messages.map((entry, i) => entry.pending ? null : <article className={`conversation-message ${entry.role}`} key={i}><div className="conversation-avatar">{entry.role === 'user' ? 'You' : 'AI'}</div><div className="conversation-content"><span className="conversation-author">{entry.role === 'user' ? 'You' : activeModel.model || 'Agent'}</span><div className="conversation-text">{entry.content}</div>{!!entry.citations?.length && <div className="conversation-citations">{entry.citations.map((cite,j)=>/^https?:\/\//i.test(cite.url||'')?<a key={j} href={cite.url} target="_blank" rel="noreferrer">[{j+1}] {cite.title}</a>:<span key={j}>[{j+1}] {cite.title}{cite.source_id?` · ${cite.source_id}`:''}</span>)}</div>}</div></article>)}
        </div>}
        {session?.mapping_id && <MappingChatActivity controller={mapping}/>}
        {!session?.mapping_id && (sending || activeTask || trackingError) && <section className="agent-live" aria-label="Task activity">
          <div className="agent-live-head" role="status">{working || sending ? <LoaderCircle className="spin" size={16}/> : <Check size={16}/>}<strong>{activeTask ? String(activeTask.status).replaceAll('_',' ') : 'Starting…'}</strong>{(working || pendingRun) && <button className="chat-tool-btn" aria-label="Stop task" onClick={() => void stopTask()}><Square size={15}/></button>}</div>
          {trackingError && <p role="status">{trackingError}</p>}
          {activeTask?.error && <p role="alert">{activeTask.error}</p>}
          {activeTask?.summary && <p className="conversation-text">{activeTask.summary}</p>}
          {activeTask?.status === 'awaiting_approval' && <button type="button" onClick={() => navigate('Team')}>Review pending actions</button>}
          <details><summary>Activity and results{activeTask?.event_count ? ` (${activeTask.event_count})` : ''}</summary>
            <ol className="agent-live-steps">{(activeTask?.events || []).map((step: Row, i: number) => <li key={step.id || i}><span className="agent-step-text">{step.agent ? `${step.agent}: ` : ''}{step.text || step.name || step.kind}</span></li>)}</ol>
            {activeTask?.events_truncated && <p className="muted">Showing recent activity. Download the conversation for the full record.</p>}
            {activeTask?.result && <pre className="saved-task-result">{typeof activeTask.result === 'string' ? activeTask.result : JSON.stringify(activeTask.result, null, 2)}</pre>}
          </details>
        </section>}
        {taskIds.length > 1 && <div className="saved-task-picker"><label htmlFor="saved-task">Saved work</label><select id="saved-task" value={taskId} onChange={event => setTaskId(event.target.value)}>{taskIds.map((id, index) => <option key={id} value={id}>Task {index + 1}{id === session?.task_id ? ' (latest)' : ''}</option>)}</select></div>}
        <div ref={bottom}/>
      </div>

      <div className="chat-compose">
        {error && <div role="alert" className="conversation-error">{error}<button type="button" aria-label="Dismiss" onClick={() => setError('')}><X size={15}/></button></div>}
        {attached.length > 0 && <div className="conversation-attachments">{attached.map(a => <div key={a.id} className="conversation-attachment ready"><Paperclip size={15}/><span>{a.title || a.filename || 'File'}</span><button type="button" aria-label="Remove" onClick={() => setAttached(prev => prev.filter(x => x.id !== a.id))}><X size={13}/></button></div>)}</div>}
        <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files); }}>
          <textarea aria-label="Message" rows={1} maxLength={12000} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} placeholder={sending ? 'Working…' : 'Message the agent…'} disabled={sending}/>
          <div className="chat-composer-actions">
            <input ref={fileInput} type="file" multiple className="sr-only" aria-label="Attach files" onChange={e => void addFiles(e.target.files || [])}/>
            <button type="button" className="chat-tool-btn" title="Attach a file to discuss" onClick={() => fileInput.current?.click()} disabled={sending || importing}><Plus size={18}/></button>
            <button type="button" className={`chat-tool-btn ${webSearch ? 'on' : ''}`} aria-pressed={webSearch} title="Let the agent search the web" onClick={() => setWebSearch(v => !v)} disabled={sending}><Search size={17}/></button>
            <button className="chat-send" aria-label="Send" type="submit" disabled={!canSend}>{sending || importing ? <LoaderCircle className="spin" size={18}/> : <ArrowUp size={18}/>}</button>
          </div>
        </form>
        <p className="chat-footnote">{!activeModel.model ? 'Pick a model above to begin.' : 'Permitted reads run automatically; changes need review. Enter to send · Shift+Enter for a newline.'}</p>
      </div>
    </section>
  </div>;
}
