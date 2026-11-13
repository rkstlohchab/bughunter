import {useEffect,useState} from 'react';
import {rpc} from './api';
import {Button} from './components';

type Citation={chunk_id:string;locator:string;timestamp_seconds?:number;quote:string;basis?:string};
type Report={source_status:string;kind:string;audio:{completed_parts:number;total_parts:number;transcription_process_complete:boolean;transcript_passages:number;unclear_total:number;unclear_passages:Citation[]};visual:{plan_available:boolean;planned_frames:number|null;completed_planned_frames:number|null;saved_visual_observations:number;unreadable_total:number;unreadable_intervals:Citation[];maximum_sampling_spacing_seconds:number|null;pending_ranges:{start:number;last_timestamp:number;frames:number}[]};methods:{candidate_steps:Citation[];candidate_steps_total:number;mentioned_tools:{name:string;quote:string;locator:string}[]};windows:{label:string;audio:string;planned_frames:number|null;completed_frames:number;readability_issues:number;status:string}[];warnings:string[]};
const stamp=(seconds:number)=>{const value=Math.floor(seconds);return `${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value%3600/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;};

export default function ResourceQuality({sourceId}:{sourceId:string}){
  const [report,setReport]=useState<Report|null>(null);
  const [error,setError]=useState('');
  const [version,setVersion]=useState(0);
  useEffect(()=>{
    let current=true;
    setReport(null);setError('');
    rpc<Report>('sources.coverage',{source_id:sourceId}).then(value=>{
      if(!value?.audio||!value.visual||!value.methods||!Array.isArray(value.windows))throw new Error('Coverage unavailable. Refresh after the application backend is updated.');
      if(current)setReport(value);
    }).catch(error=>{if(current)setError(String(error));});
    return()=>{current=false;};
  },[sourceId,version]);
  return <section className="surface" aria-label="Learning coverage">
    <div className="section-title"><div><h3>Learning coverage</h3><p>Check the saved evidence and what still needs review.</p></div><Button onClick={()=>setVersion(value=>value+1)}>Refresh coverage</Button></div>
    <p>Saved descriptions do not prove that every demonstration was captured or that the app can reproduce a method. Imported knowledge is retrieved with citations; model weights are unchanged.</p>
    {error&&<p role="alert" className="error-text">{error}</p>}
    {!report&&!error&&<p role="status">Checking coverage…</p>}
    {report&&<>
      <div className="media-metrics">
        <span>{report.audio.total_parts>0?`Audio processed: ${report.audio.completed_parts} / ${report.audio.total_parts} parts`:`Transcript passages: ${report.audio.transcript_passages}`}</span>
        {report.kind==='video'&&<span>{report.visual.plan_available?`Planned frames saved: ${report.visual.completed_planned_frames} / ${report.visual.planned_frames}`:`Saved frame observations: ${report.visual.saved_visual_observations}`}</span>}
        <span>Independently tested methods: none recorded</span>
      </div>
      {report.kind==='video'&&<p className="muted">{report.visual.maximum_sampling_spacing_seconds!=null?`The largest planned sampling interval is ${report.visual.maximum_sampling_spacing_seconds.toFixed(1)} seconds. `:'An exact frame plan is not available for this older import. '}Short actions between screenshots may be missed. Audio completion does not guarantee word-perfect transcription.</p>}
      {report.warnings?.map((warning,index)=><p className="info-note" key={index}>{warning}</p>)}
      {(report.visual.unreadable_total>0||report.audio.unclear_total>0)&&<details><summary>Intervals needing review ({report.visual.unreadable_total+report.audio.unclear_total})</summary>{[...report.visual.unreadable_intervals,...report.audio.unclear_passages].map((item,index)=><div key={index}><strong>{item.locator}</strong><blockquote>{item.quote}</blockquote><small>Citation: {item.chunk_id}</small></div>)}</details>}
      {report.visual.pending_ranges.length>0&&<details><summary>Pending visual intervals ({report.visual.pending_ranges.length})</summary>{report.visual.pending_ranges.map((gap,index)=><p key={index}>{stamp(gap.start)}–{stamp(gap.last_timestamp)} · {gap.frames} planned frames pending</p>)}</details>}
      {report.windows.length>0&&<details><summary>Coverage by ten-minute section ({report.windows.length})</summary><div className="table-scroll"><table><thead><tr><th>Section</th><th>Audio</th><th>Saved frames</th><th>Review flags</th></tr></thead><tbody>{report.windows.map(window=><tr key={window.label}><td>{window.label}</td><td>{window.audio.replaceAll('_',' ')}</td><td>{window.completed_frames}{window.planned_frames!=null?` / ${window.planned_frames}`:' (plan unavailable)'}</td><td>{window.readability_issues}</td></tr>)}</tbody></table></div></details>}
      <details><summary>Quoted method steps ({report.methods.candidate_steps_total} candidates)</summary>
        <p>These quoted lines may describe a workflow. They have not been independently reproduced or verified. Text from a visual description is the model’s interpretation of a sampled screen.</p>
        {report.methods.mentioned_tools.length>0&&<p>Tools mentioned: {report.methods.mentioned_tools.map(tool=>tool.name).join(', ')}. A mention does not establish correct use.</p>}
        {report.methods.candidate_steps.length?report.methods.candidate_steps.map((step,index)=><div key={index}><strong>{step.locator} · {step.basis?.replaceAll('_',' ')}</strong><blockquote>{step.quote}</blockquote><small>Citation: {step.chunk_id}</small></div>):<p>No cited instruction candidates are available yet.</p>}
        {report.methods.candidate_steps_total>report.methods.candidate_steps.length&&<p>Showing the first {report.methods.candidate_steps.length} candidates. Full passages remain in the source reader.</p>}
      </details>
    </>}
  </section>;
}
