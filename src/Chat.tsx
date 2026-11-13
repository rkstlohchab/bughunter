import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Download, History, LoaderCircle, MessageSquarePlus, Paperclip, Plus, Search, Sparkles, Square, Trash2, X } from 'lucide-react';
import { fileBase64, rpc } from './api';
import { wantsMappingResearch, wantsHarnessTask, gameLabPrefix, wantsGameLab } from './taskIntent';
import { mappingActive, useMappingChat } from './useMappingChat';
import MappingChatActivity from './MappingChatActivity';
import HarnessSetup from './HarnessSetup';
import ChatContent from './ChatContent';
import ChatPromptNavigator from './ChatPromptNavigator';
import ChatTaskSetup from './ChatTaskSetup';
import ChatModelPicker from './ChatModelPicker';
import ModelEffort from './ModelEffort';
import ExecutionMode,{defaultMode} from './ExecutionMode';
import type {ModeProfile} from './ExecutionMode';
import BackgroundTasks from './BackgroundTasks';
import VoiceInput from './VoiceInput';
import ChatAttachments from './ChatAttachments';
import type {ProviderInfo} from './ChatModelPicker';
import type { RecordData as Row } from './api';
type RecordData = Row;

type ChatProps = { navigate: (page: 'Settings' | 'Team') => void; projectId?: string; projectName?: string; stopped?:boolean; initialPrompt?:{id:number;text:string}|null; onPromptConsumed?:()=>void; selectProject?:(id:string)=>Promise<void> };
type Message = { role: 'user' | 'assistant'; content: string; run_id?: string; task_id?: string; pending?: boolean; citations?:{title:string;url?:string;source_id?:string}[] };
type Session = RecordData & { id: string; title: string; messages: Message[] };
const MAX_FILE_BYTES = 64 * 1024 * 1024;

export default function Chat({ navigate, projectId, projectName, stopped=false, initialPrompt, onPromptConsumed, selectProject }: ChatProps) {
  const [modeProfile,setModeProfile]=useState<ModeProfile>(defaultMode);
  const [voiceSubmit,setVoiceSubmit]=useState(false);
  const [history, setHistory] = useState<RecordData[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const mapping = useMappingChat(session, setSession);
  const [text, setText] = useState('');
  const [setup,setSetup]=useState<Row|null>(null);
  const [harnessPlan,setHarnessPlan]=useState<{message:string;mode:string}|null>(null);
  const [harness,setHarness]=useState<Row|null>(null);
  const [sending, setSending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [activeTask, setActiveTask] = useState<RecordData | null>(null);
  const [taskId, setTaskId] = useState('');
  const [trackingError, setTrackingError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [visionMode,setVisionMode]=useState('current');
  const [imageCapable,setImageCapable]=useState(false);
  const [attached, setAttached] = useState<RecordData[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activeModel, setActiveModel] = useState<{ provider: string; model: string;reasoning_effort?:string }>({ provider: '', model: '' });
  const [modelSaving,setModelSaving]=useState(false);
  const [modelLoading,setModelLoading]=useState(true);
  const [modelError,setModelError]=useState('');
  const modelVersion=useRef(0);
  const historyButton = useRef<HTMLButtonElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const conversation = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const scopeVersion = useRef(0);
  const selectionVersion = useRef(0);
  const sendGuard = useRef(false);
  const scopeKey = `bughunter.chat.${projectId || 'library'}`;

  useEffect(()=>{const stop=()=>{scopeVersion.current++;sendGuard.current=false;setVoiceSubmit(false);setSending(false);setLoading(false);setSetup(null);setHarnessPlan(null);};window.addEventListener('bughunter-stop-all',stop);return()=>window.removeEventListener('bughunter-stop-all',stop);},[]);
  function closeHistory() { setHistoryOpen(false); historyButton.current?.focus(); }

  const refreshHistory = async () => {
    const version = scopeVersion.current;
    const rows = await rpc<RecordData[]>('chat.list', { project_id: projectId || null });
    if (version === scopeVersion.current) setHistory(rows);
  };
  const refreshModels = async (refresh=false) => {
    const version=modelVersion.current;
    setModelLoading(true);setModelError('');
    try {
      const r = await rpc<{ providers: ProviderInfo[]; active: { provider: string; model: string;reasoning_effort?:string } }>('models.available',{refresh});
      if(version!==modelVersion.current)return;
      setProviders(r.providers || []); setActiveModel(r.active || { provider: '', model: '' });
    } catch (error) {
      if(version===modelVersion.current)setModelError('Could not load your provider models. Refresh the list to retry.');
      if(refresh)throw error;
    } finally {if(version===modelVersion.current)setModelLoading(false);}
  };
  useEffect(() => {
    const version = ++scopeVersion.current;
    selectionVersion.current++;
    sendGuard.current = false;
    let alive = true;
    setSetup(null);setHarnessPlan(null);setHarness(null);setSession(null); setHistory([]); setText(''); setActiveTask(null); setTaskId(''); setAttached([]);
    setLoading(true); setError(''); setSending(false); setImporting(false);
    void refreshModels();
    const load = async () => {
      try {
        const list = await rpc<RecordData[]>('chat.list', { project_id: projectId || null });
        if (!alive || version !== scopeVersion.current) return;
        setHistory(list);
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
  useEffect(()=>{followLatest.current=true;},[session?.id]);
  useEffect(() => {
    const stream=conversation.current;
    if(stream && followLatest.current)stream.scrollTop=stream.scrollHeight;
  }, [session?.messages?.length,session?.messages?.at(-1)?.content,sending]);
  useEffect(() => { setTaskId(session?.harness_id?'':session?.task_id || session?.run_id || ''); }, [session?.id, session?.task_id, session?.run_id,session?.harness_id]);
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

  useEffect(()=>{
    const id=session?.harness_id;
    if(!id){setHarness(null);return;}
    let alive=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try{
        const result=await rpc<Row>('harness.get',{id});
        if(!alive)return;
        setHarness(result);setSession(result.session as Session);setSending(result.status==='running');setTrackingError('');
        if(result.status!=='running'){void refreshHistory();return;}
      }catch(error){if(alive)setTrackingError(`Task connection interrupted. Retrying… ${String(error)}`);}
      if(alive)timer=setTimeout(()=>void poll(),1000);
    };
    void poll();return()=>{alive=false;clearTimeout(timer);};
  },[session?.harness_id,harness?.status==='running',stopped]);

  const filteredHistory = history.filter(chat => String(chat.title || 'Chat').toLowerCase().includes(filter.toLowerCase()));
  const canSend = !stopped && !setup && !harnessPlan && !loading && !sending && !importing && !mapping.busy && !(session?.mapping_id && !mapping.data) && !!text.trim() && (!attached.some(file=>file.has_images||file.kind==='image')||visionMode==='configured'||imageCapable);

  useEffect(()=>{if(voiceSubmit&&canSend){setVoiceSubmit(false);void send();}},[voiceSubmit,text,canSend]);

  function newChat() {
    window.dispatchEvent(new Event('bughunter-stop-listening'));setVoiceSubmit(false);
    scopeVersion.current++;sendGuard.current=false;setSending(false);
    selectionVersion.current++;
    setSetup(null);setHarnessPlan(null);setHarness(null);setSession(null); setText(''); setError(''); setActiveTask(null); setTaskId(''); setAttached([]); setHistoryOpen(false);
  }
  async function selectChat(id: string) {
    window.dispatchEvent(new Event('bughunter-stop-listening'));setVoiceSubmit(false);
    scopeVersion.current++;sendGuard.current=false;setSending(false);
    const version = scopeVersion.current;
    const selection = ++selectionVersion.current;
    setSetup(null);setHarnessPlan(null);setHarness(null);setError(''); setActiveTask(null); setAttached([]);
    try {
      const loaded = await rpc<Session>('chat.get', { id });
      if (version !== scopeVersion.current || selection !== selectionVersion.current) return;
      setSession(loaded); setHistoryOpen(false);
    } catch (e) { if (version === scopeVersion.current) setError(String(e instanceof Error ? e.message : e)); }
  }
  async function pickModel(provider: string, model: string,reasoning_effort?:string) {
    if(modelSaving)return;
    const version=++modelVersion.current;
    setModelSaving(true);setError('');
    try {
      const saved=await rpc<RecordData>('models.select', { provider, model,...(reasoning_effort!==undefined?{reasoning_effort}:{}) });
      if(version!==modelVersion.current)return;
      setActiveModel({provider:saved?.provider||provider,model:saved?.model||model,reasoning_effort:saved?.reasoning_effort||reasoning_effort||''});
      void refreshModels();
    }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally{if(version===modelVersion.current)setModelSaving(false);}
  }

  async function startConfiguredHarness(message:string,mode:string,version:number){
    const result=await rpc<Row>('harness.start',{id:session?.id,message,mode,project_id:projectId||null,source_ids:[],attachment_ids:attached.map(file=>file.id),vision_mode:visionMode,grants:modeProfile.grants,execution_mode:modeProfile.execution_mode,background:modeProfile.background,web_search_mode:modeProfile.web_search_mode||'auto'});
    if(version!==scopeVersion.current)return;
    setHarness(result);setSession(result.session as Session);setText('');setAttached([]);setSending(result.status==='running');void refreshHistory();
  }

  async function send() {
    if (!canSend || sendGuard.current) return;
    sendGuard.current = true;
    const version = scopeVersion.current;
    const message = text.trim();
    followLatest.current=true;
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
          project_id:projectId || null,source_ids:[],search_web:modeProfile.web_search_mode==='on'});
        if (version !== scopeVersion.current) return;
        setSession(result.session);setText('');setAttached([]);setSending(false);await refreshHistory();return;
      }
      const gameRequest=wantsGameLab(message);
      if(message==='/exit' && session?.harness_id){
        const result=await rpc<Row>('harness.detach',{id:session.harness_id});
        if(version!==scopeVersion.current)return;
        setSession(result.session as Session);setHarness(null);setText('');setSending(false);return;
      }
      const leavingForLearning=/\b(?:course|lesson|library|transcri(?:be|ption)|video analysis|vision model|local (?:ai|model))\b/i.test(message);
      if(session?.harness_id && !leavingForLearning){
        if(harness&&(['cancelled','interrupted'].includes(harness.status)||(harness.execution_mode&&harness.execution_mode!==modeProfile.execution_mode)||(harness.background!==undefined&&harness.background!==modeProfile.background)||(harness.web_search_mode&&harness.web_search_mode!==(modeProfile.web_search_mode||'auto'))||(modeProfile.permissions_configured&&modeProfile.execution_mode==='auto'&&JSON.stringify([...(harness.grants||[])].sort())!==JSON.stringify([...(modeProfile.grants||[])].sort())))){
          if(modeProfile.permissions_configured){await startConfiguredHarness(message,harness.mode||'task',version);return;}
          setHarnessPlan({message,mode:harness.mode||'task'});setSending(false);return;
        }
        const result=await rpc<Row>('harness.send',{id:session.harness_id,message,attachment_ids:attached.map(a=>a.id),vision_mode:visionMode});
        if(version!==scopeVersion.current)return;
        setHarness(result);setSession(result.session as Session);setText('');setSending(result.status==='running');return;
      }
      if(gameRequest || wantsHarnessTask(message) || (!leavingForLearning&&/\b(?:github|vercel|plugin|connector|deploy(?:ment)?)\b/i.test(message)) || modeProfile.execution_mode!=='auto' || (modeProfile.permissions_configured&&!leavingForLearning)){
        const prefix=gameLabPrefix(message);
        const body=prefix?message.slice(prefix.length).trim()||'Help me inspect my game setup':message.replace(/^\/task\s*/i,'');
        if(modeProfile.permissions_configured){await startConfiguredHarness(body,gameRequest?'game':'task',version);return;}
        setHarnessPlan({message:body,mode:gameRequest?'game':'task'});setSending(false);return;
      }
      if(!attached.length){
        const plan = await rpc<RecordData>('autopilot.plan', { message, project_id: projectId || null, source_ids: [] });
        if (version !== scopeVersion.current) return;
        if (plan.workflow && plan.workflow !== 'chat') {
          if(plan.requires_approval || plan.missing?.length){setSetup({...plan,message,source_ids:[]});setSending(false);return;}

          const saved = await rpc<Session>('chat.autopilot', { id: session?.id, message, project_id: projectId || undefined, source_ids: [] });
          if (version !== scopeVersion.current) return;
          setSession(saved); setText(''); setAttached([]); setSending(false); await refreshHistory(); return;
        }
      }
      const started = await rpc<{ session: Session; run_id: string; cursor: number }>('chat.agent', {
        id: session?.id, message, project_id: projectId || undefined,
        source_ids: [], attachment_ids:attached.map(file=>file.id),vision_mode:visionMode,web_search_mode:modeProfile.web_search_mode||'auto',
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
      if(session?.harness_id){
        const result=await rpc<Row>('harness.stop',{id:session.harness_id});setHarness(result);setSession(result.session as Session);setSending(false);return;
      } else if (session?.mapping_id && mappingActive(mapping.data?.workflow.status)) {
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
    if (chosen.some(f => f.size > MAX_FILE_BYTES)) { setError('Attach references under 64 MB. Add courses separately from Library.'); return; }
    setImporting(true); setError('');
    try {
      const imported: RecordData[] = [];
      for (const file of chosen) imported.push(await rpc<RecordData>('chat.attachments.add', { filename: file.name, content_base64: await fileBase64(file), project_id: projectId || null }));
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
      <div className="chat-history-actions"><button type="button" className="chat-new" onClick={newChat}><MessageSquarePlus size={17}/> New chat</button><button type="button" className="chat-tool-btn chat-history-close" aria-label="Close chat history" onClick={closeHistory}><X size={18}/></button></div>
      <label className="chat-side-search"><Search size={15}/><input aria-label="Search chats" placeholder="Search chats" value={filter} onChange={e => setFilter(e.target.value)}/></label>
      <div className="chat-side-list" aria-busy={loading}>
        {loading ? <p className="chat-side-empty" role="status">Loading conversations…</p> : !filteredHistory.length ? <div className="chat-side-empty" role="status"><p>{filter ? 'No matching chats' : 'No chats yet'}</p>{filter ? <button type="button" className="chat-clear-search" onClick={() => setFilter('')}>Clear search</button> : <small>Your conversations will appear here.</small>}</div> : filteredHistory.map(chat => <div key={chat.id} className={`chat-side-row ${session?.id === chat.id ? 'active' : ''}`}><button onClick={() => selectChat(chat.id)} title={chat.title || 'Chat'}>{chat.title || 'Chat'}</button><button className="chat-side-del" aria-label="Delete chat" onClick={() => deleteChat(chat.id)}><Trash2 size={13}/></button></div>)}
      </div>
    </aside>

    <section className="chat-main">
      <div className="chat-head">
        <button ref={historyButton} className="chat-tool-btn chat-history-toggle" aria-label="Chat history" aria-controls="chat-history" aria-expanded={historyOpen} onClick={() => setHistoryOpen(v => !v)}><History size={18}/></button>
        <span className="chat-scope">{projectName || 'All knowledge'}</span><BackgroundTasks projectId={projectId} onOpen={id=>void selectChat(id)}/>{session && <button className="chat-tool-btn" aria-label="Download conversation and results" onClick={() => void downloadConversation()}><Download size={17}/></button>}
      </div>

      <div className="conversation-scroll" ref={conversation} onScroll={event=>{const stream=event.currentTarget;followLatest.current=stream.scrollHeight-stream.scrollTop-stream.clientHeight<80;}}>
        {!messages.length && !sending ? <div className="conversation-welcome"><div className="conversation-welcome-icon"><Sparkles size={28}/></div><h2>What should I do?</h2><p>Describe your goal. I’ll collect the setup and permissions, run the supported steps, and report back here. You can stop work at any time.</p><div className="conversation-starters">
          {['Control an app on my computer', 'Run app checks', 'Check my knowledge base', 'Study a course I upload'].map(prompt => <button key={prompt} onClick={() => {setText(prompt);}}>{prompt}</button>)}
        </div></div> : <div className="conversation-transcript">
          {messages.map((entry, i) => entry.pending ? null : <article className={`conversation-message ${entry.role}`} data-prompt-index={entry.role==='user'?i:undefined} key={i}><div className="conversation-content"><span className="conversation-author">{entry.role === 'user' ? 'You' : 'Assistant'}</span><div className="conversation-text"><ChatContent content={entry.content}/></div>{!!entry.citations?.length && <div className="conversation-citations">{entry.citations.map((cite,j)=>/^https?:\/\//i.test(cite.url||'')?<a key={j} href={cite.url} target="_blank" rel="noreferrer">[{j+1}] {cite.title}</a>:<span key={j}>[{j+1}] {cite.title}{cite.source_id?` · ${cite.source_id}`:''}</span>)}</div>}</div></article>)}
        </div>}
        {session?.mapping_id && <MappingChatActivity controller={mapping}/>}
        {(sending||working)&&<div className="chat-working" role="status"><LoaderCircle size={14} className="spin"/><span>{harness?.background===false?'Working…':'Working in background…'}</span><button type="button" className="chat-tool-btn" aria-label="Stop task" onClick={()=>void stopTask()}><Square size={14}/></button></div>}
        {trackingError&&<p role="status">{trackingError}</p>}
        {(harness?.error||activeTask?.error)&&<p role="alert">{harness?.error||activeTask?.error}</p>}
        {harness&&['cancelled','interrupted','provider_error','crashed','max_turns'].includes(harness.status)&&<button type="button" className="text-button" disabled={stopped} onClick={()=>setHarnessPlan({message:text.trim()||'Continue the previous task from its recorded results.',mode:'task'})}>Continue task</button>}
        {!session?.harness_id && taskIds.length > 1 && <div className="saved-task-picker"><label htmlFor="saved-task">Saved work</label><select id="saved-task" value={taskId} onChange={event => setTaskId(event.target.value)}>{taskIds.map((id, index) => <option key={id} value={id}>Task {index + 1}{id === session?.task_id ? ' (latest)' : ''}</option>)}</select></div>}
        {setup&&<ChatTaskSetup plan={setup} projectId={projectId} sessionId={session?.id} stopped={stopped} onCancel={()=>setSetup(null)} onStarted={async saved=>{setSetup(null);setText('');setAttached([]);setSession(saved as Session);await refreshHistory();}} selectProject={selectProject}/>}
        {harnessPlan&&<HarnessSetup {...harnessPlan} executionMode={modeProfile.execution_mode} background={modeProfile.background} webSearchMode={modeProfile.web_search_mode||'auto'} initialGrants={session?.harness_id?harness?.grants:undefined} sessionId={session?.id} projectId={projectId} sourceIds={[]} attachmentIds={attached.map(file=>file.id)} visionMode={visionMode} stopped={stopped} onCancel={()=>setHarnessPlan(null)} onStarted={result=>{setHarnessPlan(null);setHarness(result);setSession(result.session as Session);setText('');setAttached([]);setSending(result.status==='running');void refreshHistory();}}/>}
      </div>

      <ChatPromptNavigator prompts={messages.flatMap((message,index)=>message.role==='user'?[{index,text:message.content}]:[])} stream={conversation} sessionId={session?.id}/>
      <div className="chat-compose">
        {stopped&&<p className="chat-stopped" role="status">All tasks are stopped. Use Resume tasks in the top-right corner to start new work.</p>}
        {error && <div role="alert" className="conversation-error">{error}<button type="button" aria-label="Dismiss" onClick={() => setError('')}><X size={15}/></button></div>}
        {attached.length>0&&<p className="muted chat-reference-note">Chat references only · Not added to Library</p>}
        <ChatAttachments provider={activeModel.provider} model={activeModel.model} hasImages={attached.some(file=>file.has_images||file.kind==='image')} value={visionMode} onChange={setVisionMode} onConfigure={()=>navigate('Settings')} onCapability={setImageCapable}/>
        {attached.length > 0 && <div className="conversation-attachments">{attached.map(a => <div key={a.id} className="conversation-attachment ready"><Paperclip size={15}/><span>{a.title || a.filename || 'File'}</span><button type="button" aria-label="Remove" onClick={() => {setAttached(prev => prev.filter(x => x.id !== a.id));void rpc('chat.attachments.remove',{id:a.id}).catch(()=>{});}}><X size={13}/></button></div>)}</div>}
        <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files); }}>
          <textarea aria-label="Message" rows={1} maxLength={12000} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} placeholder={sending ? 'Working…' : 'Message the agent…'} disabled={stopped}/>
          <div className="chat-composer-footer">
            <div className="chat-model-controls" aria-label="Chat model and effort">
              <ModelEffort variant="compact" info={providers.find(provider=>provider.provider===activeModel.provider)?.model_options?.find(model=>model.id===activeModel.model)?.effort} value={activeModel.reasoning_effort} disabled={modelSaving} onChange={effort=>void pickModel(activeModel.provider,activeModel.model,effort)}>
                <ChatModelPicker compact providers={providers} active={activeModel} floating disabled={modelSaving} loading={modelLoading} error={modelError} onPick={(provider, model) => void pickModel(provider, model)} onConfigure={() => navigate('Settings')} onRefresh={()=>refreshModels(true)}/>
              </ModelEffort>
              <ExecutionMode provider={activeModel.provider} model={activeModel.model} chatId={session?.id} projectId={projectId} value={modeProfile} onChange={profile=>{setModeProfile(profile);if(profile.chat_id&&profile.chat_id!==session?.id)void selectChat(profile.chat_id);}} disabled={modelSaving||!activeModel.model}/>
            </div>
            <div className="chat-composer-actions">
              <input ref={fileInput} type="file" multiple className="sr-only" aria-label="Attach files" onChange={e => void addFiles(e.target.files || [])}/>
              <button type="button" className="chat-tool-btn" aria-label="Attach a file" title="Attach a file to discuss" onClick={() => fileInput.current?.click()} disabled={sending || importing || stopped}><Plus size={18}/></button>
              <VoiceInput key={scopeKey} disabled={stopped||importing} value={text} onChange={setText} onSend={value=>{setText(value);setVoiceSubmit(true);}} onSetup={()=>navigate('Settings')}/>

              <button className="chat-send" aria-label="Send" type="submit" disabled={!canSend}>{sending || importing ? <LoaderCircle className="spin" size={18}/> : <ArrowUp size={18}/>}</button>
            </div>
          </div>
        </form>
        <p className="chat-footnote">{!activeModel.model ? 'Choose a model to begin.' : 'Enter to send · Shift+Enter for a newline.'}</p>
      </div>
    </section>
  </div>;
}
