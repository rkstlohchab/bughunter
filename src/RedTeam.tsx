import { useEffect, useState } from 'react';
import { Activity, BookOpen, CheckCircle2, Download, FlaskConical, GitBranch, History, Play, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { Button, Field, Empty, Code } from './components';
import { fileBase64, rpc } from './api';
import PromptCorpus from './PromptCorpus';
import type { RecordData } from './api';

type Case = RecordData;
type Trial = RecordData;
type Execute = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
type Props = { perform: Execute; busy: string; revision: number; projectId?: string };
const techniques = ['role-play', 'instruction hierarchy', 'context manipulation', 'format variation', 'multi-turn', 'encoded content', 'indirect injection', 'other'];
const verdicts = ['inconclusive', 'resisted', 'partial', 'bypassed'];
const blank = () => ({ title: '', prompt: '', technique: 'other', objective: '', target_system_prompt: '', notes: '', tags: '', parent_id: '' });

export default function RedTeam({ perform, busy, revision, projectId }: Props) {
  const [cases, setCases] = useState<Case[]>([]);
  const [trials, setTrials] = useState<Trial[]>([]);
  const [insights, setInsights] = useState<RecordData | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [draft, setDraft] = useState(blank());
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState('');
  const [verdict, setVerdict] = useState('inconclusive');
  const [lesson, setLesson] = useState('');
  const [reviews, setReviews] = useState<Record<string, { verdict: string; lesson: string }>>({});
  const [working, setWorking] = useState(false);
  const [suggestion, setSuggestion] = useState('');
  const [archives, setArchives] = useState<RecordData[]>([]);
  const [archiveId, setArchiveId] = useState('');
  const [archiveResult, setArchiveResult] = useState<RecordData | null>(null);
  const [importingArchive, setImportingArchive] = useState(false);
  const refreshArchives = async () => {
    const list = await rpc<RecordData[]>('sources.list');
    setArchives(list.filter(item => String(item.filename || '').toLowerCase().endsWith('.zip')));
  };
  const uploadArchive = async (file?: File) => {
    if (!file) return;
    if (file.size > 64 * 1024 * 1024) { window.alert('ZIP files must be 64 MiB or smaller. Use Knowledge → Add → Large file path for bigger archives.'); return; }
    setImportingArchive(true);
    try {
      // Resolve file conversion before creating the non-async RPC callback.
      const content_base64 = await fileBase64(file);
      const saved = await perform('Adding prompt research archive', () => rpc<RecordData>('sources.import', {
          filename: file.name, content_base64,
        }), 'Archive queued for indexing. Inspect it when indexing finishes.');
      if (saved?.id) { setArchiveId(saved.id); setArchiveResult(null); await refreshArchives(); }
    } finally { setImportingArchive(false); }
  };
  const inspectArchive = async () => {
    if (!archiveId) return;
    const result = await perform('Classifying saved prompt archive', () => rpc<RecordData>('redteam.archive.inspect', { source_id: archiveId }));
    if (result) setArchiveResult(result);
  };

  const refresh = async () => {
    const [allCases, allTrials, memory] = await Promise.all([
      rpc<Case[]>('redteam.cases'), rpc<Trial[]>('redteam.history'), rpc('redteam.insights'),
    ]);
    setCases(allCases.filter(c=>!projectId || !c.project_id || c.project_id===projectId)); setTrials(allTrials); setInsights(memory);
  };
  useEffect(() => { setSelected('');setDraft(blank()); }, [projectId]);
  useEffect(() => { refresh().catch(() => {}); refreshArchives().catch(() => {}); }, [revision, projectId]);
  const active = cases.find(c => c.id === selected);
  const activeTrials = trials.filter(t => t.case_id === selected);
  const display = cases.filter(c => !query || [c.title, c.prompt, c.technique, ...(c.tags || [])].join(' ').toLowerCase().includes(query.toLowerCase()));
  const load = (c: Case) => { setSelected(c.id); setDraft({ title: c.title, prompt: c.prompt, technique: c.technique, objective: c.objective || '', target_system_prompt: c.target_system_prompt || '', notes: c.notes || '', tags: (c.tags || []).join(', '), parent_id: c.parent_id || '' }); setResponse(''); setLesson(''); };
  const save = async () => {
    const value = await perform('Saving model test', () => rpc<Case>('redteam.save', { ...draft, id: selected || undefined, parent_id: draft.parent_id || undefined,
      project_id: projectId || undefined, tags: draft.tags.split(',').map(s => s.trim()).filter(Boolean) }), 'Test case saved.');
    if (value?.id) { setSelected(value.id); await refresh(); }
  };
  const run = async () => {
    if (!selected) return;
    setWorking(true);
    try { await perform('Testing configured model', () => rpc('redteam.run', { case_id: selected }), 'Response saved. Review and label the result.'); await refresh(); }
    finally { setWorking(false); }
  };
  const suggest = async () => {
    if (!selected) return;
    const value = await perform('Drafting from earlier model tests', () => rpc<RecordData>('redteam.suggest', { case_id: selected }));
    if (value?.prompt) {
      setSelected(''); setDraft({ ...draft, title: `Variant of ${draft.title}`.slice(0, 160),
        prompt: value.prompt, objective: value.objective || '', parent_id: active?.id || '' });
      setSuggestion(value.rationale || 'New candidate derived from previous experiments.');
    }
  };
  const record = async () => {
    const value = await perform('Saving test response', () => rpc('redteam.record', { case_id: selected, response, verdict, lesson }), 'Response added to experiment history.');
    if (value) { setResponse(''); setLesson(''); await refresh(); }
  };
  const review = async (trial: Trial) => {
    const form = reviews[trial.id] || { verdict: trial.verdict === 'untested' ? 'inconclusive' : trial.verdict, lesson: trial.lesson || '' };
    const value = await perform('Recording reviewed outcome', () => rpc('redteam.review', { id: trial.id, ...form }), 'Test result and lesson saved.');
    if (value) await refresh();
  };
  const exportData = async () => {
    const value = await rpc('redteam.export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'model-redteam-experiments.json'; anchor.click();
    URL.revokeObjectURL(url);
  };
  return <div className="redteam-layout">
    <PromptCorpus />
    <section className="surface redteam-library">
      <div className="section-title"><div><h2>Prompt library</h2><p>Repeatable test cases and prompt families.</p></div><FlaskConical size={20}/></div>
      <div className="button-group wrap"><Button icon={Plus} onClick={() => { setSelected(''); setDraft(blank()); }}>New test</Button><Button icon={Download} onClick={() => exportData().catch(() => {})}>Export JSON</Button></div>
      <Field label="Search saved prompts"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Method, prompt, tag..."/></Field>
      {display.length ? <div className="redteam-case-list">{display.map(c => <button key={c.id} type="button" className={`redteam-case ${selected === c.id ? 'selected' : ''}`} onClick={() => load(c)}><strong>{c.title}</strong><small>{c.technique} · {trials.filter(t => t.case_id === c.id).length} trials</small></button>)}</div> : <Empty title="No saved tests" description="Create an experiment or search with different words."/>}
      <div className="redteam-stats">
        <h3><BookOpen size={17}/> Import a prompt ZIP</h3>
        <p className="muted">Add a ZIP of examples to Knowledge, then inspect candidate techniques. Nothing runs automatically or becomes a training label without review.</p>
        <label className="field"><span>Upload prompt archive</span><input type="file" accept=".zip,application/zip" disabled={importingArchive || !!busy} onChange={e => { void uploadArchive(e.target.files?.[0]); e.target.value = ''; }}/></label>
        <label className="field"><span>Previously uploaded ZIPs</span><select aria-label="Select prompt archive" value={archiveId} onChange={e => {setArchiveId(e.target.value);setArchiveResult(null);}}><option value="">Select archive</option>{archives.map(a => <option value={a.id} key={a.id}>{a.title || a.filename}</option>)}</select></label>
        <Button disabled={!archiveId || !!busy || importingArchive} icon={Search} onClick={inspectArchive}>Analyze saved archive</Button>
        {archiveResult && <div className="archive-analysis" role="status"><p>{archiveResult.message}</p>{archiveResult.ready && <><p>{archiveResult.analyzed_chunks} of {archiveResult.total_chunks} passages checked · {archiveResult.duplicate_passages} exact duplicates · {archiveResult.unclassified_passages} without a keyword match.</p><ul>{(archiveResult.categories || []).map((r: RecordData) => <li key={r.name}><strong>{r.name}</strong> — {r.matching_passages} matching passages</li>)}</ul><details><summary>Show cited examples</summary>{(archiveResult.examples || []).map((item: RecordData) => <p key={item.chunk_id}><strong>{item.locator}</strong>: {item.excerpt}</p>)}</details></>}</div>}
      </div>
      <div className="redteam-stats"><h3><BookOpen size={17}/> Experiment memory</h3><p>{insights?.case_count || 0} test cases · {insights?.trial_count || 0} recorded trials</p><p className="muted">Outcomes are evidence you review. This updates retrieval memory, not model weights.</p><div>{Object.entries(insights?.verdict_counts || {}).map(([name, count]) => <span className="redteam-count" key={name}>{name}: {String(count)}</span>)}</div></div>
    </section>
    <div className="redteam-editor">
      <section className="surface"><div className="section-title"><div><h2>{active ? 'Edit test case' : 'New model test'}</h2><p>Use a prompt you designed or reuse a previously evaluated method.</p></div><GitBranch size={20}/></div>
        <div className="form-grid"><Field label="Test title"><input value={draft.title} onChange={e => setDraft({...draft, title: e.target.value})} maxLength={160} placeholder="e.g. Instruction hierarchy comparison"/></Field><Field label="Method"><select value={draft.technique} onChange={e => setDraft({...draft, technique: e.target.value})}>{techniques.map(t => <option key={t} value={t}>{t}</option>)}</select></Field></div>
        <Field label="Test prompt"><textarea rows={7} maxLength={24000} value={draft.prompt} onChange={e => setDraft({...draft, prompt: e.target.value})} placeholder="Paste the prompt to send to the model under test."/></Field>
        <Field label="System prompt for the model under test" hint="Optional. Sent as that model's system instructions for this test. Separate from the workbench agent's system prompt in Settings."><textarea rows={4} maxLength={8000} value={draft.target_system_prompt} onChange={e => setDraft({...draft, target_system_prompt: e.target.value})} placeholder="Describe the desired response rules or filter for this evaluation."/></Field>
        <Field label="Expected behaviour / test objective"><textarea rows={2} maxLength={2000} value={draft.objective} onChange={e => setDraft({...draft, objective: e.target.value})} placeholder="What should a robust model do?"/></Field>
        <div className="form-grid"><Field label="Tags (comma separated)"><input value={draft.tags} onChange={e => setDraft({...draft, tags: e.target.value})} placeholder="hierarchy, refusal, evaluation"/></Field><Field label="Notes"><input value={draft.notes} onChange={e => setDraft({...draft, notes: e.target.value})} placeholder="Hypothesis and conditions"/></Field></div>
        <div className="button-group wrap"><Button variant="primary" disabled={!!busy || !draft.title.trim() || !draft.prompt.trim()} onClick={save}>Save test case</Button>{active && <><Button icon={GitBranch} disabled={!!busy} onClick={suggest}>AI draft from history</Button><Button icon={GitBranch} onClick={() => { setSelected(''); setDraft({...draft, title: `Variant of ${draft.title}`.slice(0,160), parent_id: active.id}); }}>Create variant</Button><Button icon={Trash2} disabled={!!busy} onClick={async () => { if (!window.confirm('Delete this case and its entire trial history?')) return; const result = await perform('Deleting model test', () => rpc('redteam.delete', { id: active.id })); if (result) { setSelected(''); setDraft(blank()); await refresh(); } }}>Delete</Button></>}</div>
      </section>
      {suggestion && <section className="surface" role="status"><h3>Why this variant?</h3><p>{suggestion}</p><Button onClick={() => setSuggestion('')}>Dismiss rationale</Button></section>}
      {active && <>
        <section className="surface"><div className="section-title"><div><h2>Evaluate model response</h2><p>One explicit API call. The provider and model are selected under Settings.</p></div><Activity size={20}/></div><div className="button-group wrap"><Button icon={Play} variant="primary" disabled={!!busy || working} onClick={run}>Run against configured API</Button><Button icon={RefreshCw} disabled={!!busy} onClick={() => refresh().catch(() => {})}>Refresh history</Button></div><p className="muted">Hosted API calls use your configured key and pricing limits. Test prompts and system instructions are sent to the chosen provider. No execution tools are exposed to the tested model.</p>
        <details><summary>Record a response manually</summary><Field label="Model response"><textarea rows={5} maxLength={32000} value={response} onChange={e => setResponse(e.target.value)} placeholder="Paste the response from a model you tested elsewhere."/></Field><div className="form-grid"><Field label="Outcome"><select value={verdict} onChange={e => setVerdict(e.target.value)}>{verdicts.map(v => <option key={v} value={v}>{v}</option>)}</select></Field><Field label="What this test taught you"><input value={lesson} onChange={e => setLesson(e.target.value)} placeholder="Reusable observation"/></Field></div><Button disabled={!response.trim() || !!busy} onClick={record}>Save response and lesson</Button></details></section>
        <section className="surface"><div className="section-title"><div><h2><History size={20}/> Previous trials ({activeTrials.length})</h2><p>Review outcomes to build the next prompt variant from previous results.</p></div></div>
          {activeTrials.length ? activeTrials.map(t => { const r = reviews[t.id] || { verdict: t.verdict === 'untested' ? 'inconclusive' : t.verdict, lesson: t.lesson || '' }; return <article className="redteam-trial" key={t.id}><div className="redteam-trial-title"><strong>{t.provider} / {t.model}</strong><span className="muted">{t.verdict} · {t.evaluated_at || t.created_at}</span></div><Code label="Observed response" value={t.response || '(empty response)'}/><div className="form-grid"><Field label="Reviewed verdict"><select value={r.verdict} onChange={e => setReviews({...reviews, [t.id]: {...r, verdict: e.target.value}})}>{verdicts.map(v => <option key={v} value={v}>{v}</option>)}</select></Field><Field label="Reusable lesson"><input value={r.lesson} onChange={e => setReviews({...reviews, [t.id]: {...r, lesson: e.target.value}})} maxLength={4000}/></Field></div><div className="button-group wrap"><Button icon={CheckCircle2} disabled={!!busy} onClick={() => review(t)}>Save review</Button><Button icon={BookOpen} disabled={!!busy || t.verdict === 'untested' || !t.lesson || !!t.published_source_id} onClick={() => perform('Publishing reviewed lesson', () => rpc('redteam.publish', { id: t.id }), 'Reviewed lesson sent to the shared Knowledge Library.').then(() => refresh())}>{t.published_source_id ? 'In Library' : 'Publish lesson to Library'}</Button></div></article>; }) : <Empty title="No trials yet" description="Run this prompt against a model or record an external response."/>}</section>
      </>}
      {insights && (insights.lessons?.length ?? 0) > 0 && <section className="surface"><h2>Lessons from previous prompts</h2>{insights.lessons.slice(0, 10).map((x: RecordData) => <p className="redteam-lesson" key={x.trial_id}><strong>{x.technique} · {x.verdict}:</strong> {x.lesson}</p>)}</section>}
    </div>
  </div>;
}
