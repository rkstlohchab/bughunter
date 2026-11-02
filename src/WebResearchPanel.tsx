import { useState } from 'react';
import { BookOpen, Search } from 'lucide-react';
import { rpc } from './api';
import { Button, Field, Modal } from './components';

type Result = {title:string;url:string;snippet:string};
export default function WebResearchPanel({onClose,projectId,onSaved}:{onClose:()=>void;projectId?:string;onSaved:(id:string)=>void}) {
 const [query,setQuery]=useState('');const [engine,setEngine]=useState('auto');const [response,setResponse]=useState<{id:string;engine:string;results:Result[]}|null>(null);
 const [busy,setBusy]=useState(false);const [error,setError]=useState(''); const [saved,setSaved]=useState<string[]>([]);
 async function search(){setBusy(true);setError('');try{setResponse(await rpc('research.search',{query,engine,project_id:projectId||null}));}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function save(i:number){if(!response)return;setBusy(true);setError('');try{const r=await rpc<{id:string}>('research.save',{id:response.id,index:i});onSaved(r.id);setSaved(ids=>[...ids,r.id]);}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <Modal title="Search the web" wide onClose={onClose}><div className="web-research-modal"><p className="muted">Search public sources, review the evidence, then save useful results to your project Knowledge. External snippets are untrusted.</p>
 <form onSubmit={e=>{e.preventDefault();void search();}} className="research-form"><Field label="Search for"><input autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="What do you want to research?" maxLength={400}/></Field><Field label="Search engine"><select value={engine} onChange={e=>setEngine(e.target.value)}><option value="auto">Automatic — free search, then Wikipedia</option><option value="free">Free web search (no key)</option><option value="brave">Brave Search (optional key)</option><option value="wikipedia">Wikipedia</option></select></Field><Button type="submit" variant="primary" icon={Search} disabled={!query.trim()||busy}>Search</Button></form>
 {error&&<p role="alert" className="conversation-error">{error}</p>}
 {response&&<div className="research-results"><p className="muted">{response.results.length} results · {response.engine}</p>{response.results.map((result,i)=><article className="research-result" key={i}><h4>{result.title}</h4><p>{result.snippet}</p><a href={result.url} target="_blank" rel="noopener noreferrer">{result.url}</a><div><Button icon={BookOpen} disabled={busy||saved.length>=6} onClick={()=>void save(i)}>Save to Knowledge</Button></div></article>)}</div>}
 <div className="form-actions"><Button onClick={onClose}>Close</Button></div></div></Modal>;
}
