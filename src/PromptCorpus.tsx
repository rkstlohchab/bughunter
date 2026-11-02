import { useEffect, useMemo, useState } from 'react';
import { BookOpen, CheckCircle, FileText, Image as ImageIcon, Search, ShieldAlert } from 'lucide-react';
import { rpc } from './api';
import type { RecordData } from './api';
import { Button } from './components';

type Prompt = RecordData & { id: string; path: string; models: string[]; techniques: string[]; screenshot_ids: string[] };
const verdictOptions = [
  {value:'unverified', label:'Not reviewed'},
  {value:'observed_success',label:'I observed success'},
  {value:'observed_failure',label:'I observed failure'},
  {value:'inconclusive',label:'Inconclusive'},
];

export default function PromptCorpus() {
  const [stats,setStats] = useState<RecordData | null>(null);
  const [items,setItems] = useState<Prompt[]>([]);
  const [model,setModel] = useState('');
  const [method,setMethod] = useState('');
  const [query,setQuery] = useState('');
  const [selected,setSelected] = useState<Prompt | null>(null);
  const [images,setImages] = useState<Record<string,string>>({});
  const [availableShots,setAvailableShots] = useState<RecordData[]>([]);
  const [evidenceIds,setEvidenceIds] = useState<string[]>([]);
  const [notes,setNotes] = useState('');
  const [verdict,setVerdict] = useState('unverified');
  const [loading,setLoading] = useState(false);
  const [error,setError] = useState('');
  useEffect(()=>{void Promise.all([rpc<RecordData>('corpus.summary'),rpc<RecordData[]>('corpus.screenshots')]).then(([summary,shots])=>{setStats(summary);setAvailableShots(shots);}).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{void rpc<Prompt[]>('corpus.list',{model,technique:method,query,limit:200}).then(setItems).catch(e=>setError(String(e)));},[model,method,query]);
  const modelOptions = useMemo(()=>Object.keys(stats?.models || {}).sort(),[stats]);
  const methodOptions = useMemo(()=>Object.keys(stats?.techniques || {}).sort(),[stats]);
  async function open(item:Prompt) {
    setLoading(true);setError('');setImages({});
    try {
      const record=await rpc<Prompt>('corpus.get',{id:item.id});
      setSelected(record);setVerdict(record.review?.verdict||'unverified');setNotes(record.review?.notes||'');
      setEvidenceIds(record.review?.screenshots || record.screenshot_ids || []);
      for(const shot of record.screenshots||[]) {
        try {
          const image=await rpc<RecordData>('corpus.image',{id:shot.id});
          setImages(previous=>({...previous,[shot.id]:`data:image/png;base64,${image.content_base64}`}));
        } catch { /* A damaged screenshot should not hide the prompt. */ }
      }
    } catch(e){setError(String(e));}finally{setLoading(false);}
  }
  async function saveReview(){
    if(!selected)return;
    setLoading(true);setError('');
    try {
      const review=await rpc('corpus.review',{id:selected.id,verdict,notes,screenshot_ids:evidenceIds});
      const refreshed=await rpc<Prompt>('corpus.get',{id:selected.id});
      setSelected(refreshed);
      setItems(previous=>previous.map(item=>item.id===selected.id?{...item,review}:item));
    } catch(e){setError(String(e));}finally{setLoading(false);}
  }
  return <section className="surface corpus-section" aria-label="Research prompt corpus">
    <header className="section-title"><div><h2><BookOpen size={19}/> Model-specific prompt research</h2><p>Original prompts and screenshots grouped by the model named in their file or folder. No prompts run automatically.</p></div></header>
    {stats&&<p className="muted">{stats.prompts} text records · {stats.screenshots} screenshots · {stats.attachments} other attachments. Duplicate records are retained with links to the matching original.</p>}
    <div className="corpus-filters">
      <label>Model<select aria-label="Filter research model" value={model} onChange={e=>setModel(e.target.value)}><option value="">All models</option>{modelOptions.map(m=><option key={m} value={m}>{m} ({stats?.models?.[m]})</option>)}</select></label>
      <label>Technique<select aria-label="Filter technique" value={method} onChange={e=>setMethod(e.target.value)}><option value="">All techniques</option>{methodOptions.map(m=><option key={m} value={m}>{m}</option>)}</select></label>
      <label>Find prompt<Search size={14}/><input aria-label="Find prompt filename" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Filename or category"/></label>
    </div>
    {error&&<p className="conversation-error" role="alert">{error}</p>}
    <div className="corpus-layout">
      <div className="corpus-list" aria-label="Indexed research prompts">
        {!items.length&&<p className="muted">No matching records.</p>}
        {items.map(item=><button type="button" key={item.id} onClick={()=>void open(item)} className={`corpus-item ${selected?.id===item.id?'selected':''}`}>
          <FileText size={16}/><span><strong>{item.filename}</strong><small>{item.models.join(', ')} · {item.techniques.slice(0,2).join(', ') || 'Not classified'}</small>{item.review?.verdict&&item.review.verdict!=='unverified'&&<small><CheckCircle size={12}/> {item.review.verdict.replaceAll('_',' ')}</small>}</span>
          {!!item.screenshot_ids.length&&<ImageIcon size={15} aria-label="Screenshot suggestion"/>}
        </button>)}
      </div>
      <div className="corpus-detail">
        {!selected?<div className="corpus-placeholder"><ShieldAlert size={22}/><strong>Select a saved prompt</strong><p>Inspect the original text and any nearby screenshots. Review each observation before recording an outcome.</p></div>:<>
          <strong>{selected.filename}</strong>
          <p className="muted corpus-source">Source: {selected.path}</p>
          <p className="muted">Model clues: {selected.models.join(', ')}. Methods: {selected.techniques.join(', ')||'Not classified'}.</p>
          {selected.duplicate_of&&<p className="muted">Exact duplicate of record {selected.duplicate_of}.</p>}
          <details className="corpus-raw"><summary>View original prompt as research data</summary><pre>{selected.text}</pre></details>
          {(selected.screenshots||[]).map((shot:RecordData)=><figure key={shot.id} className="corpus-figure">
            {images[shot.id]?<img src={images[shot.id]} alt={`Screenshot suggested to be related to ${selected.filename}`}/>:<p>Screenshot unavailable</p>}
            <figcaption>{shot.path} · Suggested by nearby archive order, not independently verified.</figcaption>
          </figure>)}
          <div className="corpus-review">
            <details className="corpus-raw"><summary>Correct screenshot associations ({evidenceIds.length} selected)</summary>
              <p className="muted">Files were paired using archive order, not verified visually. Select which screenshots actually show this prompt's results.</p>
              <div className="corpus-evidence-options">{availableShots.map(shot=><label key={shot.id}><input type="checkbox" checked={evidenceIds.includes(shot.id)} onChange={e=>setEvidenceIds(previous=>e.target.checked?[...previous,shot.id].slice(0,12):previous.filter(x=>x!==shot.id))}/><span>{shot.path}</span></label>)}</div>
            </details>
            <label>Observed result<select value={verdict} onChange={e=>setVerdict(e.target.value)}>{verdictOptions.map(o=><option value={o.value} key={o.value}>{o.label}</option>)}</select></label>
            <label>Review notes<textarea rows={3} value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Record what the screenshot or target response actually shows"/></label>
            <Button disabled={loading} onClick={()=>void saveReview()}>{loading?'Saving…':'Save review'}</Button>
          </div>
        </>}
      </div>
    </div>
  </section>;
}
