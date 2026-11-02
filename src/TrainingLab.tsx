import { useCallback, useEffect, useState } from 'react';
import { BookOpen, CheckCircle2, Download, FolderPlus, Layers3, Plus, Trash2 } from 'lucide-react';
import { rpc } from './api';
import type { RecordData as Row } from './api';
import { Button, Code, Empty, Field } from './components';

const DOMAINS = [
  { id: 'burp_suite', name: 'Burp Suite', detail: 'Authorized HTTP, proxy, request analysis and documented findings.' },
  { id: 'cheat_engine', name: 'Cheat Engine', detail: 'Offline process inspection, scan refinement and reviewed local memory workflows.' },
  { id: 'ai_redteam', name: 'AI red team', detail: 'Model robustness observations, prompt families, outcomes and repeatable evaluations.' },
  { id: 'tor_research', name: 'Tor research', detail: 'Reviewed research notes, page analysis and source-backed forum findings.' },
] as const;
type Domain = typeof DOMAINS[number]['id'];
type Run = (label: string, fn: () => Promise<any>, success?: string) => Promise<any>;
const blank = { instruction: '', response: '', context: '', source: '', notes: '', group: '', tags: '' };

export default function TrainingLab({ perform, revision, projectId }: { perform: Run; revision: number; projectId?: string }) {
  const [domain, setDomain] = useState<Domain>('burp_suite');
  const [workspaces, setWorkspaces] = useState<Row[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [examples, setExamples] = useState<Row[]>([]);
  const [newTitle, setNewTitle] = useState('');
  const [instructions, setInstructions] = useState('');
  const [modelSource, setModelSource] = useState('');
  const [draft, setDraft] = useState(blank);
  const [editId, setEditId] = useState('');
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState('');
  const [trials, setTrials] = useState<Row[]>([]);
  const [trialId, setTrialId] = useState('');
  const [torPages, setTorPages] = useState<Row[]>([]);
  const [torPageId, setTorPageId] = useState('');
  const [torSummary, setTorSummary] = useState('');
  const current = workspaces.find(w => w.id === workspaceId);
  useEffect(() => { if (domain === 'tor_research') rpc<Row[]>('tor.pages').then(x => setTorPages(x.filter(p => p.reviewed))).catch(() => {}); }, [domain, revision]);
  useEffect(() => {
    if (domain !== 'ai_redteam') return;
    rpc<Row[]>('redteam.history').then(items => setTrials(items.filter(t => t.verdict !== 'untested' && t.lesson))).catch(() => {});
  }, [domain, revision]);
  const refresh = useCallback(async () => {
    const all = await rpc<Row[]>('training.workspaces');
    setWorkspaces(all.filter(w=>!projectId || !w.project_id || w.project_id===projectId));
  }, [projectId]);
  useEffect(() => { setWorkspaceId(''); }, [projectId]);
  useEffect(() => { refresh().catch(e => setNotice(String(e))); }, [refresh, revision]);
  useEffect(() => { setWorkspaceId(''); setEditId(''); setDraft(blank); }, [domain]);
  useEffect(() => {
    if (!workspaceId && workspaces.length) {
      setWorkspaceId(workspaces.find(w => w.domain === domain)?.id || '');
    }
  }, [domain, workspaceId, workspaces]);
  useEffect(() => {
    if (!workspaceId) { setExamples([]); setInstructions(''); setModelSource(''); return; }
    setInstructions(current?.instructions || '');
    setModelSource(current?.model_source || '');
    rpc<Row[]>('training.examples', { workspace_id: workspaceId }).then(setExamples).catch(e => setNotice(String(e)));
  }, [workspaceId, current?.updated_at]);
  const reload = async () => {
    await refresh();
    if (workspaceId) setExamples(await rpc<Row[]>('training.examples', { workspace_id: workspaceId }));
  };
  const create = async () => {
    const result = await perform('Creating curriculum', () => rpc<Row>('training.create', { domain, title: newTitle.trim(), project_id: projectId || undefined }), 'Training project created.');
    if (result?.id) { setWorkspaceId(result.id); setNewTitle(''); await reload(); }
  };
  const saveWorkspace = async () => {
    if (!current) return;
    const result = await perform('Saving curriculum', () => rpc('training.update', {
      id: current.id, title: current.title, instructions, model_source: modelSource,
    }), 'Project-specific instructions saved.');
    if (result) await reload();
  };
  const saveExample = async () => {
    if (!workspaceId) return;
    const result = await perform('Saving training example', () => rpc('training.save', {
      workspace_id: workspaceId, ...Object.fromEntries(Object.entries(draft).filter(([key]) => key !== 'tags')),
      tags: draft.tags.split(',').map(x => x.trim()).filter(Boolean), ...(editId ? { id: editId } : {}),
    }), 'Example saved as draft; review it before exporting.');
    if (result) { setEditId(''); setDraft(blank); await reload(); }
  };
  const review = async (id: string, approved: boolean) => {
    const result = await perform(approved ? 'Reviewing training example' : 'Returning example to draft',
      () => rpc('training.review', { id, approved }), approved ? 'Example marked reviewed.' : 'Example returned to draft.');
    if (result) await reload();
  };
  const exportDataset = async () => {
    try {
      const data = await rpc<Row>('training.export', { workspace_id: workspaceId });
      const bytes = atob(data.content_base64);
      const raw = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; ++i) raw[i] = bytes.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/zip' }));
      const a = document.createElement('a'); a.href = url; a.download = data.filename; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(`Exported ${data.manifest.reviewed_examples} reviewed examples. No model training has run.`);
    } catch (e) { setNotice(String(e)); }
  };
  const filtered = examples.filter(e => `${e.instruction} ${e.response} ${e.notes}`.toLowerCase().includes(search.toLowerCase()));
  const reviewed = examples.filter(e => e.status === 'reviewed').length;

  return <div className="training-stack">
    <section className="surface">
      <div className="section-title"><div><h2>Specialist learning and fine-tuning</h2><p>Keep each field's instructions, worked examples, review history and eventual adapters separate.</p></div><Layers3 size={23}/></div>
      <div className="training-domains">{DOMAINS.map(d => <button key={d.id} className={`training-domain ${domain === d.id ? 'selected' : ''}`} onClick={() => { setDomain(d.id); setEditId(''); setDraft(blank); }} aria-pressed={domain === d.id}><strong>{d.name}</strong><small>{d.detail}</small></button>)}</div>
      <div className="form-grid"><Field label="Select project"><select aria-label="Select training project" value={workspaceId} onChange={e => { setWorkspaceId(e.target.value); setEditId(''); setDraft(blank); }}><option value="">Select a curriculum</option>{workspaces.filter(w => w.domain === domain).map(w => <option value={w.id} key={w.id}>{w.title} ({w.reviewed_count}/{w.example_count} reviewed)</option>)}</select></Field><Field label="Create another project"><div className="button-group"><input value={newTitle} maxLength={120} onChange={e => setNewTitle(e.target.value)} placeholder="e.g. CE memory scanning basics"/><Button icon={FolderPlus} disabled={!newTitle.trim()} onClick={create}>Create</Button></div></Field></div>
      {notice && <p role="status" className="muted">{notice}</p>}
    </section>
    {!current ? <section className="surface"><Empty icon={BookOpen} title="Create a field-specific curriculum" description="Separate projects prevent experimental prompt variants and memory-tool lessons from contaminating other fine-tuning datasets."/></section> : <>
      <section className="surface">
        <div className="section-title"><div><h2>{current.title}</h2><p>{reviewed} reviewed · {examples.length - reviewed} drafts · {current.domain}</p></div><Button icon={Download} disabled={!reviewed} onClick={exportDataset}>Export MLX dataset</Button></div>
        <Field label="System instructions for this specialist model" hint="Only this project's dataset and examples receive these instructions. Separate from Settings and the red-team target prompt."><textarea value={instructions} onChange={e => setInstructions(e.target.value)} maxLength={8000} rows={4} placeholder="Describe the specialist's scope, response format and desired behavior."/></Field>
        <Field label="Model source / version (optional)" hint="Keep a model's exact origin and version for reproducibility (e.g. a Kaggle handle or Ollama model tag)."><input value={modelSource} onChange={e => setModelSource(e.target.value)} maxLength={400} placeholder="Kaggle handle, model ID, or local model version"/></Field>
        <div className="button-group wrap"><Button onClick={saveWorkspace}>Save project settings</Button><Button icon={Trash2} variant="danger-quiet" onClick={async () => { if (!window.confirm('Delete this training project and all its examples?')) return; const result = await perform('Deleting curriculum', () => rpc('training.delete', { id: current.id })); if (result) { setWorkspaceId(''); await reload(); } }}>Delete project</Button></div>
      </section>
      {domain === 'tor_research' && <section className="surface"><h3>Turn research into a worked example</h3><p>Select a reviewed capture and supply your corrected takeaway. It is stored as a draft until separately approved.</p><Field label="Reviewed capture"><select aria-label="Reviewed Tor capture" value={torPageId} onChange={e => setTorPageId(e.target.value)}><option value="">Select a captured page</option>{torPages.map(p => <option key={p.id} value={p.id}>{p.title} · {p.url.slice(0, 45)}</option>)}</select></Field><Field label="Your verified takeaway"><textarea rows={3} value={torSummary} onChange={e => setTorSummary(e.target.value)} placeholder="Summarize the source in your own words, preserving provenance and uncertainties."/></Field><Button disabled={!workspaceId || !torPageId || !torSummary.trim()} onClick={async () => { const result = await perform('Promoting research capture', () => rpc('training.import_tor', { workspace_id: workspaceId, page_id: torPageId, summary: torSummary }), 'Research imported as a draft example.'); if (result) { setTorPageId(''); setTorSummary(''); await reload(); } }}>Create draft training example</Button></section>}
      {domain === 'ai_redteam' && <section className="surface"><h3>Promote a reviewed model test</h3><p>Turn an existing reviewed prompt/response/verdict into a draft analysis example. This does not train the model to imitate the tested model's response.</p><div className="button-group wrap"><select aria-label="Reviewed model test" value={trialId} onChange={e => setTrialId(e.target.value)}><option value="">Select reviewed trial</option>{trials.map(t => <option key={t.id} value={t.id}>{t.case_title || t.technique} · {t.verdict} · {t.id.slice(-6)}</option>)}</select><Button disabled={!trialId} onClick={async () => { const result = await perform('Importing reviewed experiment', () => rpc('training.import_redteam', { workspace_id: workspaceId, trial_id: trialId }), 'Experiment imported as draft; review its training response.'); if (result) await reload(); }}>Import as draft</Button></div></section>}
      <section className="surface">
        <div className="section-title"><div><h2>{editId ? 'Edit instruction pair' : 'Teach a worked example'}</h2><p>Correct target behavior first; never train on raw, unreviewed responses.</p></div><Plus size={18}/></div>
        <Field label="Task or instruction"><textarea rows={3} value={draft.instruction} onChange={e => setDraft({...draft, instruction: e.target.value})} maxLength={16000} placeholder="Describe what the specialist should recognize or do."/></Field>
        <Field label="Reviewed ideal answer or workflow"><textarea rows={5} value={draft.response} onChange={e => setDraft({...draft, response: e.target.value})} maxLength={24000} placeholder="Provide the correct answer, decision criteria, or verified procedure."/></Field>
        <Field label="Reference context (optional)"><textarea rows={3} value={draft.context} onChange={e => setDraft({...draft, context: e.target.value})} maxLength={12000} placeholder="Relevant excerpt or facts — treated as reference data."/></Field>
        <div className="form-grid"><Field label="Source reference"><input value={draft.source} onChange={e => setDraft({...draft, source: e.target.value})} maxLength={1000} placeholder="Document, lesson, experiment ID, or URL"/></Field><Field label="Scenario group (for split isolation)" hint="Give variants of the same problem the same group ID."><input value={draft.group} maxLength={160} onChange={e => setDraft({...draft, group: e.target.value})} placeholder="e.g. session-validation-01"/></Field><Field label="Tags"><input value={draft.tags} onChange={e => setDraft({...draft, tags: e.target.value})} placeholder="scanning, pointers, evaluation"/></Field><Field label="Review notes"><input value={draft.notes} onChange={e => setDraft({...draft, notes: e.target.value})} maxLength={3000} placeholder="What changed and why?"/></Field></div>
        <div className="button-group wrap"><Button variant="primary" disabled={!draft.instruction.trim() || !draft.response.trim()} onClick={saveExample}>{editId ? 'Update draft' : 'Save example'}</Button>{editId && <Button onClick={() => { setEditId(''); setDraft(blank); }}>Cancel edit</Button>}</div>
      </section>
      <section className="surface">
        <div className="section-title"><div><h2>Example review and memory ({examples.length})</h2><p>Only reviewed examples are retrieved by agents or exported into fine-tuning datasets.</p></div></div>
        <input aria-label="Search training examples" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search instructions and lessons"/>
        {!filtered.length ? <Empty title="No examples yet" description="Add a worked task and its desired result above."/> : filtered.map(e => <article className="training-example" key={e.id}><div className="row-between"><strong>{e.status === 'reviewed' ? 'Reviewed' : 'Draft'} · {e.group || 'Standalone example'}</strong><small className="muted">{e.source || 'No source reference'}</small></div><p><strong>Task:</strong> {e.instruction}</p><details><summary>View desired response</summary><Code label="Expected response" value={e.response}/></details><div className="button-group wrap"><Button icon={CheckCircle2} onClick={() => review(e.id, e.status !== 'reviewed')}>{e.status === 'reviewed' ? 'Unreview' : 'Mark reviewed'}</Button>{e.status === 'reviewed' && <Button onClick={async()=>{const result=await perform('Updating training consent',()=>rpc('dataset.p2.consent',{id:e.id,allowed:!e.training_allowed}),e.training_allowed?'Training permission withdrawn.':'Approved for P2 dataset export.');if(result)await reload();}}>{e.training_allowed?'Withdraw training permission':'Allow P2 training export'}</Button>}<Button onClick={() => { setEditId(e.id); setDraft({ instruction:e.instruction, response:e.response, context:e.context || '', source:e.source || '', notes:e.notes || '', group:e.group || '', tags:(e.tags || []).join(', ') }); }}>Edit</Button><Button variant="danger-quiet" icon={Trash2} onClick={async () => { if (!window.confirm('Delete this example?')) return; const result = await perform('Deleting training example', () => rpc('training.delete_example', { id: e.id })); if (result) await reload(); }}>Delete</Button></div></article>)}
      </section>
    </>}
    <section className="surface"><h3>Local and Kaggle models</h3><p>Use a compatible downloaded model for inference through <strong>Ollama</strong> (Settings → Provider: Ollama). Kaggle is a model repository, not an inference API. The app can use a model labelled “uncensored” if it is licensed and compatible with your local runtime; the label alone says nothing about its quality.</p><Code label="Download a Kaggle GGUF into Ollama (run manually)" value={'python -m pip install kagglehub\npython scripts/install_kaggle_gguf.py owner/model/framework/variation --name research-local\n# Settings → Ollama → model: research-local'}/><p className="muted">For Apple Silicon LoRA: export a reviewed project's dataset ZIP, extract it, then run <code>python scripts/train_mlx_adapter.py --data /path/to/extracted --model /path/to/compatible-mlx-model</code>. This trains a separate adapter; no weight updates occur during ordinary app use.</p></section>
  </div>;
}
