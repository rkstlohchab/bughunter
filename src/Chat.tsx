import { ChevronDown, Download, History } from 'lucide-react';
import TaskActivity, { downloadRecord } from './TaskActivity';
import WebResearchPanel from './WebResearchPanel';
import MacAutopilotPanel from './MacAutopilotPanel';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, BookOpen, Check, Clock3, Cpu, FileText, LoaderCircle, MessageSquareText, Paperclip, Plus, Search, Settings2, Trash2, Users, X } from 'lucide-react';
import { fileBase64, rpc } from './api';
import type { RecordData as Row } from './api';
type RecordData = Row;
import { Button, Modal } from './components';
import { classifyTask, wantsMacControl, type TaskIntent } from './taskIntent';

type ChatProps = { navigate: (page: 'Settings' | 'Team' | 'Overview') => void; onMemoryTask: (task: string) => void; projectId?: string; projectName?: string; selectProject?: (id: string) => Promise<void> };
type Message = { role: 'user' | 'assistant'; content: string };
type Session = RecordData & { id: string; title: string; messages: Message[]; source_ids?: string[] };
const MAX_ATTACHMENTS = 6;
const MAX_FILE_BYTES = 64 * 1024 * 1024;
const ready = (source: RecordData) => (source.chunk_count > 0 && !['queued', 'failed', 'error'].includes(source.status)) || (/\.(?:exe|dll|apk|ipa|so|dylib|bin|jar)$/i.test(source.filename || '') && source.original_retained !== false && !!source.path);
const displayName = (source: RecordData) => source.title || source.filename || 'Resource';

export default function Chat({ navigate, onMemoryTask, projectId, projectName, selectProject }: ChatProps) {
  const [history, setHistory] = useState<RecordData[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [viewedTaskId, setViewedTaskId] = useState('');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const [provider, setProvider] = useState<RecordData>({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [sources, setSources] = useState<RecordData[]>([]);
  const [videoMissing, setVideoMissing] = useState<string>('');
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [showSources, setShowSources] = useState(false);
  const [showWebResearch,setShowWebResearch] = useState(false);
  const [showMacAutopilot,setShowMacAutopilot] = useState(false);
  const [allowFallback,setAllowFallback] = useState(false);
  const [webSearch,setWebSearch] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [showCartAudit, setShowCartAudit] = useState(false);
  const [showWebsiteAudit, setShowWebsiteAudit] = useState(false);
  const [webPages, setWebPages] = useState(12);
  const [webConsent, setWebConsent] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [planMode, setPlanMode] = useState('research');
  const [showAgents, setShowAgents] = useState(false);
  const [readiness, setReadiness] = useState<RecordData | null>(null);
  const [autoPlan, setAutoPlan] = useState<RecordData | null>(null);
  const [showReadiness, setShowReadiness] = useState(false);
  const [pendingIntent, setPendingIntent] = useState<TaskIntent | null>(null);
  const [readinessBusy, setReadinessBusy] = useState(false);
  const [runClaude, setRunClaude] = useState(true);
  const [runCodex, setRunCodex] = useState(true);
  const [workFolder, setWorkFolder] = useState('');
  const [allowEdits, setAllowEdits] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scopeVersion = useRef(0);
  const loadedScopeKey = useRef<string | null>(null);
  const selectionVersion = useRef(0);
  const scopeKey = `bughunter.chat.${projectId || 'library'}`;
  const taskIds = [...new Set<string>([...(session?.task_ids || []), ...(session?.task_id ? [session.task_id] : [])])];
  const currentTaskId = taskIds.includes(viewedTaskId) ? viewedTaskId : taskIds.at(-1);

  const refreshHistory = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('chat.list', {project_id: projectId || null});
    if (version === scopeVersion.current) setHistory(rows);
  };
  const refreshSources = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('sources.list', {project_id: projectId || null});
    if (version === scopeVersion.current) setSources(rows);
  };
  useEffect(() => {
    const version = ++scopeVersion.current;
    loadedScopeKey.current = null;
    let alive = true;
    // Clear project-bound state immediately; ignore all late responses.
    selectionVersion.current++;
    setSession(null); setViewedTaskId(''); setHistory([]); setSources([]); setText('');
    setSelectedSourceIds([]); setWebSearch(false); setLoading(true); setError('');
    setSending(false); setImporting(false); setReadinessBusy(false);
    setShowWebsiteAudit(false); setShowCartAudit(false); setShowAgents(false);
    setShowReadiness(false); setShowMemory(false); setShowPlan(false); setAutoPlan(null);
    setShowSources(false); setShowMacAutopilot(false); setShowWebResearch(false);
    const load = async () => {
      try {
        const [list, settings, library] = await Promise.all([
          rpc<RecordData[]>('chat.list', {project_id: projectId || null}), rpc<RecordData>('settings.get'),
          rpc<RecordData[]>('sources.list', {project_id: projectId || null}),
        ]);
        if (!alive || version !== scopeVersion.current) return;
        setHistory(list); setProvider(settings); setSources(library);
        const saved = localStorage.getItem(scopeKey);
        if (saved && list.some(item => item.id === saved)) {
          const loaded = await rpc<Session>('chat.get', {id: saved});
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
    return () => { alive = false; scopeVersion.current++; };
  }, [projectId]);
  useEffect(() => {
    if (!loading && loadedScopeKey.current === scopeKey) {
      if (session?.id) localStorage.setItem(scopeKey, session.id);
      else localStorage.removeItem(scopeKey);
    }
  }, [session?.id, loading, scopeKey]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end', behavior: 'auto' }); }, [session?.messages?.length, sending]);

  useEffect(() => {
    if (!selectedSourceIds.some(id => !ready(sources.find(s => s.id === id) || {}))) return;
    const timer = setInterval(() => { void refreshSources().catch(() => {}); }, 3500);
    return () => clearInterval(timer);
  }, [selectedSourceIds, sources]);

  const selectedSources = useMemo(() => selectedSourceIds.map(id => sources.find(s => s.id === id) || { id, title: 'Unavailable resource', status: 'error' }), [sources, selectedSourceIds]);
  const notIndexed = selectedSources.some(source => !ready(source));
  useEffect(() => {
    const hasVideo = selectedSources.some(s=>/video|audio|mp4|mov|mkv|webm|wav|mp3/i.test(`${s.media_type||''} ${s.detected_format||''} ${s.filename||''}`));
    if (!hasVideo) { setVideoMissing(''); return; }
    let active = true;
    rpc<any>('media.status').then(s=>{
      if (!active) return;
      const missing = [!s.ffmpeg?'FFmpeg':null, !s.vision_ready?'Qwen3-VL':null, (!s.whisper||!s.speech_model)?'Whisper':null].filter(Boolean);
      setVideoMissing(missing.length ? `To study video with both speech and screen descriptions, set up ${missing.join(', ')} in Settings.` : '');
    }).catch(()=>{});
    return ()=>{active=false;};
  },[selectedSourceIds.join(','),sources.length]);
  const filteredHistory = history.filter(chat => String(chat.title || 'Chat').toLowerCase().includes(filter.toLowerCase()));
  const memoryTask = (text.trim() || [...(session?.messages || [])].reverse().find(m => m.role === 'user')?.content || '').trim();
  const canSend = !loading && !sending && !importing && !readinessBusy && !notIndexed && !!text.trim();

  function newChat() {
    if (sending) return;
    selectionVersion.current++; setSession(null); setViewedTaskId(''); setSelectedSourceIds([]); setText(''); setError(''); setShowHistory(false);
  }
  async function selectChat(id: string) {
    if (sending) return;
    const version = scopeVersion.current;
    const selection = ++selectionVersion.current;
    setError('');
    try {
      const loaded = await rpc<Session>('chat.get', { id });
      if (version !== scopeVersion.current || selection !== selectionVersion.current) return;
      setViewedTaskId(''); setSession(loaded); setSelectedSourceIds(loaded.source_ids || []); setShowHistory(false);
      void refreshSources().catch(() => {});
    } catch (e) { if (version === scopeVersion.current && selection === selectionVersion.current) setError(String(e instanceof Error ? e.message : e)); }
  }
  const inferredTask = classifyTask(text);
  function requestSend() {
    if (!canSend) return;
    // Ordinary questions remain conversational. Action requests are handled by
    // deterministic backend planning, not by trusting instructions from a model.
    const action = inferredTask;
    if (/\b(?:run|test|check|audit|verify)\b.{0,80}\b(?:app|application|testing suite|test suite|knowledge base|knowledge library)\b/i.test(text)) { void prepareAutopilot(); return; }
    if (wantsMacControl(text)) {setShowMacAutopilot(true);return;}
    if (/\b(?:test|run|check|audit|verify)\b.*\b(?:staging|pre-production|preprod)\b.*\b(?:price|quote|pricing)\b/i.test(text)) {
      void prepareAutopilot(); return;
    }
    // Price integrity on an authorized live site is passive Burp triage,
    // distinct from the bundled (writable, disposable) cart regression lab.
    if (/\b(?:price manipulation|price tamper(?:ing)?|checkout tamper(?:ing)?|payment security|business.logic|web penetration test|website pentest)\b/i.test(text)
        && /\b(?:web|website|site|burp|shop|store|checkout|price|payment|ecommerce|e-commerce)\b/i.test(text)) {
      void prepareAutopilot(); return;
    }
    if (action === 'agents' || action === 'cart' || (action === 'memory' && !selectedSourceIds.length)) {
      void reviewConnections(action); return;
    }
    const burpRequest = /\bburp(?: suite)?\b.{0,80}\b(?:history|traffic|requests?|responses?|proxy logs?|repeater|scanner findings|scanner issues|vulnerabilities|findings|scan|audit)\b/i.test(text);
    const fileRequest = /\b(?:game|apk|\.exe|binary|dll|reverse.engineer|cheat|memory|esp|modif(?:y|ication))\b/i.test(text);
    const websiteRequest = /\b(?:scan|audit|inspect|test|find|review|analy[sz]e|investigate|assess)\b.{0,120}\b(?:website|web app|site|burp|https?:\/\/)/i.test(text);
    if (action !== 'chat' || burpRequest || websiteRequest || fileRequest || (selectedSourceIds.length && /\b(?:analy[sz]e|inspect|review)\b/i.test(text))) {
      void prepareAutopilot(); return;
    }
    void send();
  }
  async function prepareAutopilot() {
    const version = scopeVersion.current;
    setReadinessBusy(true); setError('');
    try {
      const plan = await rpc<RecordData>('autopilot.plan', {message:text.trim(),project_id:projectId || null,source_ids:selectedSourceIds});
      if (version !== scopeVersion.current) return;
      setAutoPlan(plan);
      if (plan.workflow === 'chat') { await send(); return; }
      if (plan.missing?.length) { setError(`To start: ${plan.missing.join(' ')}`); return; }
      if (plan.requires_approval) { setShowWebsiteAudit(true); return; }
      await runAutopilot(false);
    } catch (e) { if (version !== scopeVersion.current) return;setError(e instanceof Error ? e.message : String(e));}
    finally { if (version === scopeVersion.current) {setReadinessBusy(false);}
    }
  }
  async function runAutopilot(approved: boolean) {
    const version = scopeVersion.current;
    if (loading || sending || importing || notIndexed || !text.trim()) return;
    setSending(true);setError('');
    try {
      const updated = await rpc<Session>('chat.autopilot', {
        id:session?.id,message:text.trim(),project_id:projectId || undefined,
        source_ids:selectedSourceIds,approved,max_pages:approved ? webPages : 12,
      });
      if (version !== scopeVersion.current) return;
      setSession(updated);setText('');setShowWebsiteAudit(false);setWebConsent(false);setAutoPlan(null);
      await refreshHistory();
    } catch(e) { if (version !== scopeVersion.current) return;setError(e instanceof Error ? e.message : String(e));}
    finally { if (version === scopeVersion.current) {setSending(false);}
    }
  }
  async function reviewConnections(action: TaskIntent) {
    const version = scopeVersion.current;
    setError(''); setReadinessBusy(true); setPendingIntent(action);
    try {
      const result = await rpc<RecordData>('system.task_readiness', {kind: action, project_id: projectId || null});
      if (version !== scopeVersion.current) return;
      setReadiness(result);
      if (result.needs_setup) setShowReadiness(true);
      else if (action !== 'chat') proceedWithTask(action);
    } catch (err) { if (version !== scopeVersion.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally { if (version === scopeVersion.current) {setReadinessBusy(false);}
    }
  }
  function proceedWithTask(action: TaskIntent) {
    setShowReadiness(false);
    if (action === 'memory') { setShowMemory(true); return; }
    if (action === 'cart') { setShowCartAudit(true); return; }
    if (action === 'website') { setShowWebsiteAudit(true); return; }
    if (action === 'agents') {
      const low = text.toLowerCase();
      setRunClaude(/claude/.test(low) || !/codex/.test(low));
      setRunCodex(/codex/.test(low) || !/claude/.test(low));
      setShowAgents(true); return;
    }
    if (action === 'ai_test' || action === 'research') {
      setPlanMode(action === 'ai_test' ? 'model_evaluation' : 'research');
      setShowPlan(true); return;
    }
    void send();
  }
  async function createPlan() {
    if (!memoryTask || sending) return;
    setSending(true); setError('');
    try {
      const planned = await rpc<RecordData>('plans.create', {
        objective: memoryTask, project_id: projectId || undefined, mode: planMode, source_ids: selectedSourceIds,
      });
      await rpc('plans.run', {id: planned.id});
      setShowPlan(false); setText(''); navigate('Team');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSending(false); }
  }
  async function launchCartLab() {
    setSending(true); setError('');
    try {
      const started = await rpc<Row>('commerce.lab.run', { approved: true });
      if (started.project_id) await selectProject?.(started.project_id);
      setShowCartAudit(false); setText(''); navigate('Team');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSending(false); }
  }
  async function planAuthorizedCartReview() {
    if (!projectId) { setError('Select an authorized web project first.'); return; }
    setSending(true); setError('');
    try {
      const plan = await rpc<RecordData>('plans.create', { objective: memoryTask.slice(0, 5000), project_id: projectId, mode: 'web_review' });
      await rpc('plans.run', { id: plan.id });
      setShowCartAudit(false); setText(''); navigate('Team');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSending(false); }
  }
  async function delegate() {
    const version = scopeVersion.current;
    if (!canSend || (!runClaude && !runCodex)) return;
    setSending(true); setError('');
    try {
      const updated = await rpc<Session>('chat.delegate', {
        id: session?.id, message: text.trim(), project_id: projectId || undefined,
        agents: [...(runClaude ? ['claude'] : []), ...(runCodex ? ['codex'] : [])],
        working_dir: workFolder.trim(),
        mode: allowEdits ? 'workspace-write' : 'read-only',
      });
      if (version !== scopeVersion.current) return;
      setSession(updated); setText(''); setShowAgents(false); await refreshHistory();
    } catch (e) { if (version !== scopeVersion.current) return; setError(String(e instanceof Error ? e.message : e)); }
    finally { if (version === scopeVersion.current) { setSending(false); }
    }
  }
  async function send() {
    const version = scopeVersion.current;
    if (!canSend) return;
    setSending(true); setError('');
    try {
      const updated = await rpc<Session>('chat.send', {
        id: session?.id, message: text.trim(), project_id: projectId || undefined,
        source_ids: selectedSourceIds, allow_fallback:allowFallback, web_search:webSearch || /\b(?:search the web|search online|web search|look up online|search the internet)\b/i.test(text),
      });
      if (version !== scopeVersion.current) return;
      setSession(updated); setText(''); await refreshHistory();
    } catch (e) { if (version !== scopeVersion.current) return; setError(String(e instanceof Error ? e.message : e)); }
    finally { if (version === scopeVersion.current) { setSending(false); }
    }
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
    if (chosen.some(f => f.size > MAX_FILE_BYTES)) { setError('Choose files smaller than 64 MB, or import large files in Knowledge.'); return; }
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
    finally { if (version === scopeVersion.current) { setImporting(false); if (fileInput.current) fileInput.current.value = ''; }
    }
  }

  return <div className="conversation-shell">
    {showMacAutopilot&&<MacAutopilotPanel initialTask={text || memoryTask} projectId={projectId} onClose={()=>setShowMacAutopilot(false)}/>}
    {showWebResearch&&<WebResearchPanel projectId={projectId} onClose={()=>setShowWebResearch(false)} onSaved={id=>{setSelectedSourceIds(prev=>[...new Set([...prev,id])].slice(0,MAX_ATTACHMENTS));void refreshSources();}}/>}
    {showHistory && <Modal title="Saved conversations" onClose={() => setShowHistory(false)}><aside className="conversation-history-panel" aria-label="Conversation history">
      <div className="conversation-sidebar-head"><strong>Recent chats</strong><button type="button" className="conversation-new" onClick={newChat} aria-label="Start new chat"><Plus size={16}/> New chat</button></div>
      <label className="conversation-search"><Search size={16}/><input aria-label="Search conversations" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search chats" /></label>
      <div className="conversation-list">
        {loading && <p className="muted">Loading conversations…</p>}
        {!loading && !filteredHistory.length && <p className="conversation-empty">{filter ? 'No matching conversations.' : 'Your conversations will appear here.'}</p>}
        {filteredHistory.map(item => <div key={item.id} className={`conversation-item ${session?.id === item.id ? 'selected' : ''}`}>
          <button className="conversation-item-title" onClick={() => selectChat(item.id)} title={item.title}><MessageSquareText size={16}/><span>{item.title || 'Conversation'}</span></button>
          <button className="conversation-delete" aria-label={`Delete ${item.title || 'chat'}`} onClick={() => deleteChat(item.id)}><Trash2 size={15}/></button>
        </div>)}
      </div>
      <div className="conversation-sidebar-foot"><Clock3 size={14}/> Saved in your local library</div>
    </aside></Modal>}
    <section className="conversation-main" aria-label="AI Chat">
      <header className="conversation-header">
        <div className="conversation-model"><button className="conversation-icon-button" aria-label="Saved conversations" title="Saved conversations" onClick={() => setShowHistory(true)}><History size={19}/></button><span className="online-dot"/><div><strong>{session?.model_used?.model || provider.model || 'No model selected'}</strong><small>{session?.model_used?.fallback ? `Backup AI: ${session.model_used.provider}` : provider.model ? `${provider.provider === 'openai_compatible' ? 'Custom / Kaggle model' : provider.provider}${projectName ? ' · ' + projectName : ''}` : 'Choose a model in Settings'}</small></div></div>
        <div className="conversation-header-actions">{session && <button className="conversation-icon-button" title="Save conversation" aria-label="Save conversation" onClick={() => downloadRecord(`conversation-${session.id}.json`, session)}><Download size={18}/></button>}<button className="conversation-icon-button" title="Control a Mac app" aria-label="Control a Mac app" onClick={()=>setShowMacAutopilot(true)}><Cpu size={18}/></button><button className="conversation-icon-button" title="New chat" aria-label="New chat" onClick={newChat}><Plus size={19}/></button><button className="conversation-icon-button" title="AI settings" aria-label="AI settings" onClick={() => navigate('Settings')}><Settings2 size={19}/></button></div>
      </header>

      <div className="conversation-stream" aria-label="Conversation">
        {currentTaskId && <div className="conversation-task-area">
          {taskIds.length > 1 && <label className="chat-task-picker">Saved work<select aria-label="Saved conversation tasks" value={currentTaskId} onChange={event => setViewedTaskId(event.target.value)}>{taskIds.map((id, index) => <option key={id} value={id}>Task {index + 1}{index === taskIds.length - 1 ? ' · Latest' : ''}</option>)}</select></label>}
          <TaskActivity key={currentTaskId} taskId={currentTaskId} onOpenTasks={() => navigate('Team')}/>
        </div>}
        {!session?.messages?.length ? <div className="conversation-welcome"><div className="conversation-welcome-icon"><MessageSquareText size={30}/></div><h2>What should I help you do?</h2><p>Ask a question or describe a task. Follow the work here, with activity and results saved to your conversation.</p><div className="conversation-starters">
          {['Run the app test suite', 'Check my knowledge base', 'Audit my authorized website', 'Search my uploaded knowledge'].map(prompt => <button key={prompt} onClick={() => {setText(prompt);}}>{prompt}</button>)}
        </div></div> : <div className="conversation-transcript">{session.messages.map((entry,i) => <article className={`conversation-message ${entry.role}`} key={i}><div className="conversation-avatar">{entry.role === 'user' ? 'You' : 'AI'}</div><div className="conversation-content"><span className="conversation-author">{entry.role === 'user' ? 'You' : provider.model || 'Assistant'}</span><div className="conversation-text">{entry.content}</div></div></article>)}</div>}
        {sending && <div className="conversation-pending" role="status"><LoaderCircle className="spin" size={17}/> Thinking…</div>}
        <div ref={bottom}/>
      </div>
      <div className="conversation-compose-wrap">
        {error && <div role="alert" className="conversation-error">{error}<button type="button" aria-label="Dismiss error" onClick={() => setError('')}><X size={15}/></button></div>}
        {autoPlan && (autoPlan.missing?.length ?? 0) > 0 && <div className="info-note" role="status"><span>{autoPlan.missing!.join(' ')}</span><Button onClick={()=>navigate(autoPlan.workflow==='website'?'Overview':'Settings')}>Open {autoPlan.workflow==='website'?'project':'Settings'}</Button></div>}
        {videoMissing && <div className="info-note" role="status"><span>{videoMissing}</span><Button onClick={()=>navigate('Settings')}>Open Settings</Button></div>}
        {selectedSources.length > 0 && <div className="conversation-attachments" aria-label="Attached knowledge resources">{selectedSources.map(source => <div key={source.id} className={`conversation-attachment ${ready(source) ? 'ready' : 'waiting'}`}><FileText size={17}/><span title={displayName(source)}>{displayName(source)}<small>{ready(source) ? 'Indexed' : source.status === 'error' || source.status === 'failed' ? 'Import failed' : 'Indexing…'}</small></span><button type="button" aria-label={`Remove ${displayName(source)}`} onClick={() => toggleSource(source.id)}><X size={14}/></button></div>)}</div>}
        <form className="conversation-composer" onSubmit={e => { e.preventDefault(); requestSend(); }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files); }}>
          <textarea aria-label="Message AI" rows={3} maxLength={12000} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); requestSend(); } }} placeholder="Message your AI…"/>
          <div className="conversation-actions simple-conversation-actions"><div className="conversation-actions-left"><input ref={fileInput} type="file" multiple className="sr-only" aria-label="Upload documents" onChange={e => void addFiles(e.target.files || [])}/><button type="button" title="Upload documents or media" onClick={() => fileInput.current?.click()} disabled={sending || importing}><Paperclip size={18}/> <span>Upload</span></button><button type="button" title="Choose existing Knowledge" onClick={() => {void refreshSources().catch(e => setError(String(e)));setShowSources(true);}} disabled={sending}><BookOpen size={18}/> <span>Knowledge</span></button><button type="button" className={webSearch ? 'chat-web-active' : ''} aria-pressed={webSearch} title="Search public web sources; no API key needed" onClick={() => setWebSearch(v => !v)} disabled={sending}><Search size={18}/> <span>Web {webSearch ? 'on' : 'off'}</span></button></div><button className="conversation-send" aria-label="Send or review task" title={notIndexed ? 'Wait for indexing' : 'Send or run task'} type="submit" disabled={!canSend || readinessBusy}>{sending || importing || readinessBusy ? <LoaderCircle className="spin" size={18}/> : <ArrowUp size={19}/>}</button></div>
        </form>
      <details className="chat-mode-toggles chat-extra-options"><summary><ChevronDown size={16}/> Optional chat settings</summary><div className="chat-option-fields"><label className="chat-fallback-toggle"><input type="checkbox" checked={allowFallback} onChange={e=>setAllowFallback(e.target.checked)}/> Allow a configured backup AI if my model is offline</label></div></details>
        <div className="conversation-footnote">{notIndexed ? 'Attached resources are being processed. The AI can read them after indexing completes.' : 'Enter to send · Shift+Enter for a new line · Actions with side effects ask first'}{provider.provider !== 'ollama' && provider.provider !== 'remote_ollama' ? ' · Attached excerpts are sent to your configured AI provider.' : ''}</div>
      </div>
    </section>
    {showReadiness && readiness && <Modal title="Ready to start your task?" onClose={() => setShowReadiness(false)}>
      <p className="muted">One thing needs setup before this task can start.</p>
      <div className="task-connection-checks" role="list">
        {(readiness.steps || []).filter((step: RecordData) => step.status === 'setup').map((step: RecordData, i: number) => <div key={i} className="task-connection-step" role="listitem">
          <span className={step.status === 'setup' ? 'task-step-setup' : 'task-step-ready'}>{step.status === 'setup' ? 'Needs setup' : step.status === 'ready' ? 'Ready' : 'Configured'}</span>
          <strong>{step.label}</strong><p>{step.detail}</p>
          {step.action === 'settings' && step.status === 'setup' && <Button onClick={() => {setShowReadiness(false);navigate('Settings');}}>Open AI & tools setup</Button>}
          {step.action === 'project' && step.status === 'setup' && <Button onClick={() => {setShowReadiness(false);navigate('Overview');}}>Set up website project</Button>}
          {step.action === 'connect_web' && step.status === 'setup' && <Button disabled={readinessBusy} onClick={async () => {
            setReadinessBusy(true); setError('');
            try {
              await rpc('tools.gateway_start', {port:8091});
              await rpc('tools.gateway_verify_direct');
              setReadiness(await rpc('system.task_readiness',{kind:pendingIntent, project_id:projectId||null}));
            } catch (e) {setError(e instanceof Error?e.message:String(e));}
            finally {setReadinessBusy(false);}
          }}>Connect built-in web testing</Button>}

        </div>)}
      </div>
      {error && <p role="status" className="conversation-error">{error}</p>}
      <div className="form-actions"><Button onClick={() => setShowReadiness(false)}>Not now</Button><Button variant="primary" disabled={readinessBusy || !!readiness.needs_setup || !pendingIntent} onClick={() => { if (pendingIntent === 'chat') {setShowReadiness(false);} else if (pendingIntent) {proceedWithTask(pendingIntent);} }}>{pendingIntent === 'chat' ? 'Done' : 'Continue to task review'}</Button></div>
      {readiness.needs_setup && <p className="muted">Complete the required setup above. You can still use ordinary Chat without these optional tools.</p>}
    </Modal>}
    {showSources && <Modal title="Choose from your Knowledge Library" onClose={() => setShowSources(false)} wide><div className="conversation-source-modal"><p className="muted">Select up to six indexed resources to reference in this conversation. Sources remain in your local library.</p><label className="conversation-search"><Search size={17}/><input placeholder="Search file names…" aria-label="Search knowledge files" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}/></label><div className="conversation-source-list">{sources.filter(s => displayName(s).toLowerCase().includes(sourceFilter.toLowerCase())).map(s => <label key={s.id} className="conversation-source-row"><input type="checkbox" checked={selectedSourceIds.includes(s.id)} disabled={!ready(s) || (!selectedSourceIds.includes(s.id) && selectedSourceIds.length >= MAX_ATTACHMENTS)} onChange={() => toggleSource(s.id)}/><FileText size={19}/><span><strong>{displayName(s)}</strong><small>{ready(s) ? `Ready to read` : `${s.status || 'Processing'} · not ready`}</small></span>{selectedSourceIds.includes(s.id) && <Check size={17}/>}</label>)}{!sources.length && <p className="muted">No sources yet. Use Attach to add a document.</p>}</div><div className="form-actions"><Button onClick={() => setShowSources(false)}>Done ({selectedSourceIds.length}/{MAX_ATTACHMENTS})</Button></div></div></Modal>}
    {showPlan && <Modal title="Create a tracked task" onClose={() => setShowPlan(false)}>
      <p className="muted">The app first checks tools and searches your project's knowledge. External agents and web tests will stop for your explicit review; imported instructions cannot start commands.</p>
      <label className="field"><span>What kind of task?</span><select value={planMode} onChange={e => setPlanMode(e.target.value)}>
        <option value="research">Research and knowledge</option>
        <option value="code_review">Review code with Claude Code / Codex</option>
        <option value="web_review">Authorized web investigation</option>
        <option value="model_evaluation">AI model evaluation</option>
      </select></label>
      <div className="conversation-task-preview">{memoryTask}</div>
      <div className="form-actions"><Button onClick={() => setShowPlan(false)}>Cancel</Button><Button variant="primary" disabled={sending || !memoryTask} onClick={() => void createPlan()}>Plan and check readiness</Button></div>
    </Modal>}
    {showAgents && <Modal title="Deploy coding agents" onClose={()=>setShowAgents(false)}>
      <p className="muted">Launch installed coding agents on this task. They run in read-only mode; their actual commands, output, and completion status appear in Agents.</p>
      <div className="agent-picker-choices"><label><input type="checkbox" checked={runClaude} onChange={e=>setRunClaude(e.target.checked)}/> Claude Code</label><label><input type="checkbox" checked={runCodex} onChange={e=>setRunCodex(e.target.checked)}/> Codex</label></div>
      <label className="agent-folder-field">Project folder (optional)<input value={workFolder} onChange={e=>setWorkFolder(e.target.value)} placeholder="/Users/you/Projects/my-app"/><small>Leave blank for an empty research workspace. Select your actual repository to review its source code.</small></label>
      <label className="agent-edit-toggle"><input type="checkbox" checked={allowEdits} onChange={e=>setAllowEdits(e.target.checked)}/> Allow local file edits in this folder <small>For two agents, the first edits and the second reviews. Running build commands may still require your CLI's own permission checks.</small></label>
      <div className="conversation-task-preview">{memoryTask}</div>
      <div className="form-actions"><Button onClick={()=>setShowAgents(false)}>Cancel</Button><Button variant="primary" icon={Users} disabled={sending || !memoryTask || (!runClaude && !runCodex) || (allowEdits && !workFolder.trim())} onClick={()=>void delegate()}>Deploy agents</Button></div>
    </Modal>}
    {showWebsiteAudit && <Modal title={autoPlan?.workflow==='burp_repeater'?'Open in Burp Repeater':autoPlan?.workflow==='burp_scan_start'?'Start a Burp active audit':autoPlan?.workflow==='burp_scan_stop'?'Stop Burp audit':autoPlan?.workflow==='staging_price'?'Run staging-only quote tests':'Review website access'} onClose={() => setShowWebsiteAudit(false)}>
      {autoPlan?.workflow==='staging_price' ? <p><strong>Staging quote-only test:</strong> BugHunter will verify the deployment challenge and staging manifest, then send three bounded dry-run quote requests through the configured policy gateway and Burp. These requests never target checkout, order, or payment endpoints. Confirm that the synthetic test SKU cannot result in real purchases.</p> : autoPlan?.workflow==='burp_scan_start' ? <p><strong>Active scans can send many requests and may change or damage site data.</strong> Only run on a site you own or are explicitly permitted to actively test. The app checks project scope and seeds the scan from one previously observed, approved GET/HEAD request. Monitor the task in Burp and stop it if necessary.</p> : autoPlan?.workflow==='burp_scan_stop' ? <p>Stop the currently running Burp audit? This terminates the locally authenticated scan job.</p> : autoPlan?.workflow==='burp_repeater' ? <p>Open the selected request from your authorized project in Burp Repeater for manual review. <strong>This does not send the HTTP request.</strong> You remain in control of any subsequent testing.</p> : <><p>BugHunter will inspect approved read-only pages in your current project, record HTTP evidence and report possible issues. No attack requests or state changes will be made.</p>
      <p className="muted">No payments, cart changes, account modifications, POST requests or exploit payloads. Set the target, authorization, safe GET paths and gateway connection in Workspaces first.</p>
      <label className="field"><span>Maximum pages</span><input type="number" min={1} max={30} value={webPages} onChange={e=>setWebPages(Number(e.target.value))}/></label></>}
      <label className="website-audit-consent"><input type="checkbox" checked={webConsent} onChange={e=>setWebConsent(e.target.checked)}/> I am authorized to review this target and understand this action.</label>
      {error && <p role="alert" className="conversation-error">{error}</p>}
      <div className="form-actions"><Button onClick={() => {setShowWebsiteAudit(false);navigate('Overview');}}>Review website scope</Button><Button variant="primary" disabled={!webConsent||sending||!projectId} onClick={()=>void runAutopilot(true)}>{autoPlan?.workflow==='burp_repeater'?'Open Repeater tab':autoPlan?.workflow==='burp_scan_start'?'Start Burp audit':autoPlan?.workflow==='burp_scan_stop'?'Stop audit':autoPlan?.workflow==='staging_price'?'Run quote checks':'Start approved review'}</Button></div>
    </Modal>}
    {showCartAudit && <Modal title="Shopping-cart security task" onClose={() => setShowCartAudit(false)}>
      <p><strong>Automatic local test:</strong> Run ten predefined HTTP checks against BugHunterClaudex's disposable shopping-cart lab. The app records real responses, compares vulnerable and corrected behavior, and creates evidence-linked draft findings.</p>
      <p className="muted">A live store is different: provide a project you own or are authorized to assess. The app will prepare a scoped investigation and stop for individual approvals before state-changing requests. It does not silently place orders or change prices on third-party sites.</p>
      {error && <p role="alert" className="conversation-error">{error}</p>}
      <div className="conversation-task-preview">{memoryTask}</div>
      <div className="form-actions"><Button onClick={() => setShowCartAudit(false)}>Cancel</Button><Button disabled={sending} onClick={() => void planAuthorizedCartReview()}>Plan current site's audit</Button><Button variant="primary" disabled={sending} onClick={() => void launchCartLab()}>Run automatic local lab</Button></div>
    </Modal>}
    {showMemory && <Modal title="Inspect my test program" onClose={() => setShowMemory(false)}><p>The built-in memory helper can inspect your own offline test program. It is separate from Cheat Engine, so it can start even if Cheat Engine is not installed. You approve access to other processes and any changes. Protected programs may refuse access.</p><div className="conversation-task-preview">{memoryTask}</div><div className="form-actions"><Button onClick={() => setShowMemory(false)}>Cancel</Button><Button variant="primary" icon={Cpu} onClick={() => { void rpc("tasks.start", {kind:"memory_handoff", prompt:memoryTask, project_id:projectId || undefined}).catch(e => setError(String(e))); setShowMemory(false); onMemoryTask(memoryTask); }}>Open memory helper</Button></div></Modal>}
  </div>;
}
