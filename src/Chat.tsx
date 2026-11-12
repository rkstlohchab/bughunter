import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, Download, History, LoaderCircle, MessageSquarePlus, Paperclip, Plus, Search, Sparkles, Square, Trash2, X } from 'lucide-react';
import { fileBase64, rpc } from './api';
import { wantsMappingResearch, wantsMacControl } from './taskIntent';
import { mappingActive, useMappingChat } from './useMappingChat';
import MappingChatActivity from './MappingChatActivity';
import MacAutopilotPanel from './MacAutopilotPanel';
import ChatTaskSetup from './ChatTaskSetup';
import ChatModelPicker from './ChatModelPicker';
import type {ProviderInfo} from './ChatModelPicker';
import type { RecordData as Row } from './api';
type RecordData = Row;

type ChatProps = { navigate: (page: 'Settings' | 'Team') => void; projectId?: string; projectName?: string; stopped?:boolean; initialPrompt?:{id:number;text:string}|null; onPromptConsumed?:()=>void; selectProject?:(id:string)=>Promise<void> };
type Message = { role: 'user' | 'assistant'; content: string; run_id?: string; task_id?: string; pending?: boolean; citations?:{title:string;url?:string;source_id?:string}[] };
type Session = RecordData & { id: string; title: string; messages: Message[] };
const MAX_FILE_BYTES = 64 * 1024 * 1024;

export default function Chat({ navigate, projectId, projectName, stopped=false, initialPrompt, onPromptConsumed, selectProject }: ChatProps) {
  const [history, setHistory] = useState<RecordData[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const mapping = useMappingChat(session, setSession);
  const [text, setText] = useState('');
  const [setup,setSetup]=useState<Row|null>(null);
  const [macTask,setMacTask]=useState('');
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
  const historyButton = useRef<HTMLButtonElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scopeVersion = useRef(0);
  const selectionVersion = useRef(0);
  const sendGuard = useRef(false);
  const scopeKey = `bughunter.chat.${projectId || 'library'}`;

  useEffect(()=>{const stop=()=>{scopeVersion.current++;sendGuard.current=false;setSending(false);setLoading(false);setSetup(null);};window.addEventListener('bughunter-stop-all',stop);return()=>window.removeEventListener('bughunter-stop-all',stop);},[]);
  function closeHistory() { setHistoryOpen(false); historyButton.current?.focus(); }

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
    setSetup(null);setMacTask('');setSession(null); setHistory([]); setText(''); setActiveTask(null); setTaskId(''); setAttached([]);
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
  useEffect(()=>{if(initialPrompt){setText(initialPrompt.text);onPromptConsumed?.();}},[initialPrompt?.id]);
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
        if (['completed', 'failed', 'cancelled', 'interrupted', 'blocked', 'limited'].includes(task.status)) {
          if (session?.id && pendingRun) {
            const final=await rpc<{terminal:boolean;session:Session}>('chat.finalize',{id:session.id,run_id:pendingRun});
            if(alive && final.terminal){setSession(final.session);setSending(false);void refreshHistory();}
          } else if(session?.id) {
            const saved=await rpc<Session>('chat.task_result',{id:session.id,task_id:taskId});
            if(alive&&saved?.id)setSession(saved);
          }
          return;
        }
      } catch (err) {
        if (alive) setTrackingError(`Could not load task progress. Retrying… ${err instanceof Error ? err.message : String(err)}`);
      }
      if (alive) timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [taskId,session?.id,pendingRun]);

  const filteredHistory = history.filter(chat => String(chat.title || 'Chat').toLowerCase().includes(filter.toLowerCase()));
  const canSend = !stopped && !setup && !loading && !sending && !importing && !mapping.busy && !(session?.mapping_id && !mapping.data) && !!text.trim();

  function newChat() {
    if (sending) return;
    selectionVersion.current++;
    setSetup(null);setMacTask('');setSession(null); setText(''); setError(''); setActiveTask(null); setTaskId(''); setAttached([]); setHistoryOpen(false);
  }
  async function selectChat(id: string) {
    if (sending) return;
    const version = scopeVersion.current;
    const selection = ++selectionVersion.current;
    setSetup(null);setMacTask('');setError(''); setActiveTask(null); setAttached([]);
    try {
      const loaded = await rpc<Session>('chat.get', { id });
      if (version !== scopeVersion.current || selection !== selectionVersion.current) return;
      setSession(loaded); setHistoryOpen(false);
    } catch (e) { if (version === scopeVersion.current) setError(String(e instanceof Error ? e.message : e)); }
  }
  async function pickModel(provider: string, model: string) {
    try { await rpc('models.select', { provider, model }); await refreshModels(); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }

  async function send() {
    if (!canSend || sendGuard.current) return;
    sendGuard.current = true;
    const version = scopeVersion.current;
    const message = text.trim();
    setSending(true); setError(''); setActiveTask(null);
    try {
      // GameLab mode — route to the guide (OperatorAgent with game-cheat-ops prompt).
      // Entered by a /gamelab, /cheat, /esp, /aim, /bgmi, /dma, /valorant, /cs2
      // slash prefix on any message, or any message once a thread is open
      // (gamelabRunId in session storage). /exit leaves.
      const gamelabPrefixes = ['/gamelab', '/cheat', '/esp', '/aim', '/bgmi',
                                '/dma', '/valorant', '/cs2', '/apex', '/rust',
                                '/fortnite', '/pubg', '/codm'];
      const activeGamelab = sessionStorage.getItem('gamelab.run_id');
      const matchedPrefix = gamelabPrefixes.find(p =>
        message.toLowerCase().startsWith(p + ' ') || message.toLowerCase() === p);
      const isExit = message.toLowerCase() === '/exit' ||
                     message.toLowerCase() === '/leave';
      if (isExit && activeGamelab) {
        sessionStorage.removeItem('gamelab.run_id');
        setError('left GameLab guide thread');
        setText(''); setSending(false);
        sendGuard.current = false;
        return;
      }
      if (matchedPrefix || activeGamelab) {
        const body = matchedPrefix
          ? message.slice(matchedPrefix.length).trim() || 'help me set up a cheat for this game'
          : message;
        try {
          let runId = activeGamelab;
          if (!runId) {
            const started = await rpc<{ run_id: string; status: string }>(
              'gamelab.chat_start', { project_id: projectId || null, message: body });
            runId = started.run_id;
            sessionStorage.setItem('gamelab.run_id', runId);
          } else {
            await rpc('gamelab.chat_send', { run_id: runId, message: body });
          }
          setText(''); setAttached([]); setSending(false);
          // Poll the transcript so the Chat panel shows it
          const poll = async () => {
            try {
              const g = await rpc<{ status: string;
                                    transcript: Array<{ role: string; content: string }> }>(
                'gamelab.chat_get', { run_id: runId });
              // Render as a chat session shape the panel already knows
              setSession({
                id: `gamelab-${runId}`,
                messages: g.transcript.map((t, i) => ({
                  id: `${runId}-${i}`,
                  role: t.role,
                  text: typeof t.content === 'string' ? t.content : JSON.stringify(t.content),
                })),
                status: g.status,
              } as unknown as Session);
              if (g.status === 'running' || g.status === 'awaiting_approval') {
                setTimeout(poll, 1500);
              }
            } catch (e) {
              setError(`gamelab guide error: ${String(e)}`);
            }
          };
          void poll();
        } catch (e) {
          setError(`GameLab guide: ${e instanceof Error ? e.message : String(e)}`);
          setSending(false);
        }
        sendGuard.current = false;
        return;
      }
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
      if(wantsMacControl(message)){setMacTask(message);setText('');setSending(false);return;}
      {
        const plan = await rpc<RecordData>('autopilot.plan', { message, project_id: projectId || null, source_ids: attached.map(a => a.id) });
        if (version !== scopeVersion.current) return;
        if (plan.workflow && plan.workflow !== 'chat') {
          if(plan.requires_approval || plan.missing?.length){setSetup({...plan,message,source_ids:attached.map(a=>a.id)});setSending(false);return;}

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
  return <div className={`chat-shell ${historyOpen ? 'show-chat-history' : ''}`} onKeyDown={event => { if (event.key === 'Escape' && historyOpen) { event.preventDefault(); closeHistory(); } }}>
    {historyOpen && <button className="chat-history-backdrop" type="button" aria-label="Close chat history overlay" tabIndex={-1} onClick={closeHistory}/>}
    <aside id="chat-history" className="chat-side" aria-label="Saved conversations">
      <div className="chat-history-actions"><button type="button" className="chat-new" onClick={newChat} disabled={sending}><MessageSquarePlus size={17}/> New chat</button><button type="button" className="chat-tool-btn chat-history-close" aria-label="Close chat history" onClick={closeHistory}><X size={18}/></button></div>
      <label className="chat-side-search"><Search size={15}/><input aria-label="Search chats" placeholder="Search chats" value={filter} onChange={e => setFilter(e.target.value)}/></label>
      <div className="chat-side-list" aria-busy={loading}>
        {loading ? <p className="chat-side-empty" role="status">Loading conversations…</p> : !filteredHistory.length ? <div className="chat-side-empty" role="status"><p>{filter ? 'No matching chats' : 'No chats yet'}</p>{filter ? <button type="button" className="chat-clear-search" onClick={() => setFilter('')}>Clear search</button> : <small>Your conversations will appear here.</small>}</div> : filteredHistory.map(chat => <div key={chat.id} className={`chat-side-row ${session?.id === chat.id ? 'active' : ''}`}><button onClick={() => selectChat(chat.id)} title={chat.title || 'Chat'}>{chat.title || 'Chat'}</button><button className="chat-side-del" aria-label="Delete chat" onClick={() => deleteChat(chat.id)}><Trash2 size={13}/></button></div>)}
      </div>
    </aside>

    <section className="chat-main">
      <div className="chat-head">
        <button ref={historyButton} className="chat-tool-btn chat-history-toggle" aria-label="Chat history" aria-controls="chat-history" aria-expanded={historyOpen} onClick={() => setHistoryOpen(v => !v)}><History size={18}/></button>
        <ChatModelPicker providers={providers} active={activeModel} onPick={(provider, model) => void pickModel(provider, model)} onConfigure={() => navigate('Settings')}/>
        <span className="chat-scope">{projectName || 'All knowledge'}</span>{session && <button className="chat-tool-btn" aria-label="Download conversation and results" onClick={() => void downloadConversation()}><Download size={17}/></button>}
      </div>

      <div className="conversation-scroll">
        {!messages.length && !sending ? <div className="conversation-welcome"><div className="conversation-welcome-icon"><Sparkles size={28}/></div><h2>What should I do?</h2><p>Describe your goal. I’ll collect the setup and permissions, run the supported steps, and report back here. You can stop work at any time.</p><div className="conversation-starters">
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
          {activeTask?.status === 'awaiting_approval' && <p>Additional action approval is needed. <button className="text-button" onClick={()=>navigate('Team')}>Review the task’s exact request</button></p>}
          <details><summary>Activity and results{activeTask?.event_count ? ` (${activeTask.event_count})` : ''}</summary>
            <ol className="agent-live-steps">{(activeTask?.events || []).map((step: Row, i: number) => <li key={step.id || i}><span className="agent-step-text">{step.agent ? `${step.agent}: ` : ''}{step.text || step.name || step.kind}</span></li>)}</ol>
            {activeTask?.events_truncated && <p className="muted">Showing recent activity. Download the conversation for the full record.</p>}
            {activeTask?.result && <pre className="saved-task-result">{typeof activeTask.result === 'string' ? activeTask.result : JSON.stringify(activeTask.result, null, 2)}</pre>}
          </details>
        </section>}
        {taskIds.length > 1 && <div className="saved-task-picker"><label htmlFor="saved-task">Saved work</label><select id="saved-task" value={taskId} onChange={event => setTaskId(event.target.value)}>{taskIds.map((id, index) => <option key={id} value={id}>Task {index + 1}{id === session?.task_id ? ' (latest)' : ''}</option>)}</select></div>}
        {setup&&<ChatTaskSetup plan={setup} projectId={projectId} sessionId={session?.id} stopped={stopped} onCancel={()=>setSetup(null)} onStarted={async saved=>{setSetup(null);setText('');setAttached([]);setSession(saved as Session);await refreshHistory();}} selectProject={selectProject}/>}
        {macTask&&<MacAutopilotPanel initialTask={macTask} projectId={projectId} onClose={()=>setMacTask('')}/>}
        <div ref={bottom}/>
      </div>

      <div className="chat-compose">
        {stopped&&<p className="chat-stopped" role="status">All tasks are stopped. Use Resume tasks in the top-right corner to start new work.</p>}
        {error && <div role="alert" className="conversation-error">{error}<button type="button" aria-label="Dismiss" onClick={() => setError('')}><X size={15}/></button></div>}
        {attached.length > 0 && <div className="conversation-attachments">{attached.map(a => <div key={a.id} className="conversation-attachment ready"><Paperclip size={15}/><span>{a.title || a.filename || 'File'}</span><button type="button" aria-label="Remove" onClick={() => setAttached(prev => prev.filter(x => x.id !== a.id))}><X size={13}/></button></div>)}</div>}
        <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files); }}>
          <textarea aria-label="Message" rows={1} maxLength={12000} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} placeholder={sending ? 'Working…' : (sessionStorage.getItem('gamelab.run_id') ? 'In GameLab guide — type /exit to leave…' : 'Message the agent… (/gamelab <question> for game-cheat guide)')} disabled={sending || stopped}/>
          <div className="chat-composer-actions">
            <input ref={fileInput} type="file" multiple className="sr-only" aria-label="Attach files" onChange={e => void addFiles(e.target.files || [])}/>
            <button type="button" className="chat-tool-btn" aria-label="Attach a file" title="Attach a file to discuss" onClick={() => fileInput.current?.click()} disabled={sending || importing || stopped}><Plus size={18}/></button>
            <button type="button" className={`chat-tool-btn ${webSearch ? 'on' : ''}`} aria-label="Web search" aria-pressed={webSearch} title="Let the agent search the web" onClick={() => setWebSearch(v => !v)} disabled={sending}><Search size={17}/></button>
            <button className="chat-send" aria-label="Send" type="submit" disabled={!canSend}>{sending || importing ? <LoaderCircle className="spin" size={18}/> : <ArrowUp size={18}/>}</button>
          </div>
        </form>
        <p className="chat-footnote">{!activeModel.model ? 'Pick a model above to begin.' : 'Permitted reads run automatically; changes need review. Enter to send · Shift+Enter for a newline.'}</p>
      </div>
    </section>
  </div>;
}
