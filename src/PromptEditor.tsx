import { useState } from 'react';
import { Check, Copy, PencilLine } from 'lucide-react';
import { Button, Field } from './components';
import { polishTone, PROMPT_LIMIT } from './promptTone';
import './prompt-editor.css';

export default function PromptEditor() {
  const [original, setOriginal] = useState('');
  const [result, setResult] = useState<ReturnType<typeof polishTone> | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  function change(text: string) { setOriginal(text); setResult(null); setCopied(false); setError(''); }
  function polish() {
    try { setResult(polishTone(original)); setCopied(false); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not edit this prompt.'); }
  }
  async function copy() {
    if (!result) return;
    try { await navigator.clipboard.writeText(result.text); setCopied(true); setError(''); }
    catch { setError('Clipboard access is unavailable. Select and copy the edited text below.'); }
  }
  return <section className="surface prompt-editor" aria-labelledby="prompt-editor-title">
    <div className="section-title"><div><h1 id="prompt-editor-title">Prompt editor</h1><p>Make common rude phrases more courteous. Review every change before copying.</p></div><PencilLine size={22} aria-hidden="true" /></div>
    <p className="muted">Runs on this device with a small English phrase list. It does not disguise intent, change actions or targets, or bypass model safeguards. Your text is not sent to a model or saved.</p>
    <form onSubmit={e => { e.preventDefault(); polish(); }}>
      <Field label="Original prompt" hint={`${original.length.toLocaleString()} / ${PROMPT_LIMIT.toLocaleString()} characters`}><textarea rows={6} value={original} maxLength={PROMPT_LIMIT} onChange={e => change(e.target.value)} placeholder="What the hell is causing this error?" /></Field>
      <div className="button-group wrap"><Button type="submit" variant="primary" disabled={!original.trim()}>Polish tone</Button><Button disabled={!original} onClick={() => change('')}>Clear</Button></div>
    </form>
    {result && <div className="prompt-editor-result">
      <p role="status">{result.edits.length ? `${result.edits.length} wording ${result.edits.length === 1 ? 'change' : 'changes'}. Review the result below.` : 'No matching tone phrases. Your wording is unchanged.'}</p>
      <Field label="Edited prompt"><textarea rows={6} value={result.text} readOnly /></Field>
      {result.edits.length > 0 && <details><summary>See wording changes</summary><ul>{result.edits.map((edit, i) => <li key={i}><q>{edit.before}</q> → <q>{edit.after}</q></li>)}</ul></details>}
      <Button icon={copied ? Check : Copy} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy edited prompt'}</Button>
    </div>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
