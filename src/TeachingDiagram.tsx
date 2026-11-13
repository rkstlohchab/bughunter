import {useEffect,useId,useState} from 'react';
import {Download} from 'lucide-react';
let queue=Promise.resolve();
export default function TeachingDiagram({source}:{source:string}){
 const id='diagram-'+useId().replace(/[^A-Za-z0-9]/g,'');const [svg,setSvg]=useState('');const [error,setError]=useState('');const [showCode,setShowCode]=useState(false);
 useEffect(()=>{let live=true;setSvg('');setError('');queue=queue.catch(()=>{}).then(async()=>{try{if(source.length>12000||/%%\s*\{|^---/m.test(source))throw Error('Diagram is too large or contains configuration. Use plain Mermaid syntax.');const {default:mermaid}=await import('mermaid');mermaid.initialize({startOnLoad:false,securityLevel:'strict',htmlLabels:false,theme:'dark',fontFamily:'IBM Plex Sans, sans-serif',maxTextSize:12000,maxEdges:120,flowchart:{htmlLabels:false}});const result=await mermaid.render(id,source);if(live)setSvg(result.svg);}catch(error){if(live)setError(error instanceof Error?error.message:'Could not draw this diagram.');document.getElementById('d'+id)?.remove();}});return()=>{live=false;};},[id,source]);
 const uri=svg?'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg):'';
 return <figure className="teaching-diagram"><div className="chat-code-toolbar"><span>Diagram</span><button type="button" onClick={()=>setShowCode(!showCode)}>{showCode?'Show diagram':'View source'}</button>{svg&&<a href={uri} download="explanation.svg" aria-label="Download diagram"><Download size={14}/></a>}</div>{error?<p role="status">{error}</p>:svg&&!showCode?<img src={uri} alt="Visual explanation generated from the diagram source"/>:!svg?<p role="status">Drawing…</p>:null}{(showCode||error)&&<pre><code>{source}</code></pre>}</figure>;
}
