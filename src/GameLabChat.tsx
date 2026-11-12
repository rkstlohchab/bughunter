// GameLabChat.tsx — multi-turn chat view for the GameLab guide, usable
// inside the GameLab panel. Pulls transcript via gamelab.chat_get, sends
// turns via gamelab.chat_send.
import { useEffect, useRef, useState } from 'react';
import { gameLab } from './gameLabApi';

type Msg = { role: string; content: string };

export default function GameLabChat({ projectId }: { projectId?: string }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('idle');
  const [transcript, setTranscript] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  // Load most recent thread for this project on mount
  useEffect(() => {
    gameLab.chatList(projectId).then(({ runs }) => {
      if (runs.length > 0) {
        setRunId(runs[0].id);
      }
    }).catch(() => undefined);
  }, [projectId]);

  // Poll transcript while an active thread exists
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    const tick = async () => {
      try {
        const g = await gameLab.chatGet(runId);
        if (!alive) return;
        setTranscript(g.transcript.map(t => ({
          role: t.role,
          content: typeof t.content === 'string' ? t.content : JSON.stringify(t.content),
        })));
        setStatus(g.status);
        if (g.status === 'running' || g.status === 'awaiting_approval') {
          pollRef.current = window.setTimeout(tick, 1500);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void tick();
    return () => {
      alive = false;
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [runId]);

  const send = async () => {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      if (!runId) {
        const started = await gameLab.chatStart(text, projectId);
        setRunId(started.run_id);
      } else {
        await gameLab.chatSend(runId, text);
      }
      setText('');
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  };

  const newThread = () => {
    if (runId) void gameLab.chatStop(runId).catch(() => undefined);
    setRunId(null);
    setTranscript([]);
    setStatus('idle');
  };

  return (
    <div className="gl-chat">
      <div className="gl-chat-head">
        <strong>GameLab guide</strong>
        <span className="gl-chat-status">status: {status}</span>
        <button onClick={newThread}>New thread</button>
      </div>
      {error && <div className="gl-err">{error}</div>}
      <div className="gl-chat-transcript">
        {transcript
          .filter(m => m.role !== 'system')
          .map((m, i) => (
            <div key={i} className={`gl-chat-msg gl-chat-msg-${m.role}`}>
              <div className="gl-chat-role">{m.role}</div>
              <pre className="gl-chat-body">{m.content}</pre>
            </div>
          ))}
        {sending && <div className="gl-chat-thinking">sending…</div>}
        {status === 'running' && !sending &&
          <div className="gl-chat-thinking">guide is working…</div>}
      </div>
      <form className="gl-chat-composer"
            onSubmit={e => { e.preventDefault(); void send(); }}>
        <textarea rows={3}
                  value={text}
                  onChange={e => setText(e.target.value)}
                  placeholder={runId
                    ? 'reply to the guide…'
                    : 'ask the guide — e.g. walk me through setting up BGMI ESP'}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault(); void send();
                    }
                  }} />
        <button type="submit" disabled={sending || !text.trim()}>
          {sending ? 'sending…' : runId ? 'send' : 'start thread'}
        </button>
      </form>
    </div>
  );
}
