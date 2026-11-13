export type MediaProgress = { phase?: string; analyzed: number; total: number; timestamp: number; completed?: number; duration?: number; visual_context?: number; ocr?: number; gaps?: unknown[]; retry_at?:number; retry_provider?:string;saved_analyzed?:number };

export default function SourceProgress({ status, progress,paused=false }: { status?: string; progress?: MediaProgress;paused?:boolean }) {
  if(paused)return <div className="source-progress" role="status"><span>Processing paused</span><small>Completed work is saved. Use Continue to resume.</small></div>;
  if(status==='queued' && progress?.phase==='provider_wait')return <div className="source-progress" role="status">
    <span>Waiting for {progress.retry_provider||'AI provider'} usage limit</span>
    <small>{progress.analyzed} / {progress.total} frames saved. Completed knowledge stays searchable.</small>
    <small>{progress.retry_at?`Automatic retry: ${new Date(progress.retry_at*1000).toLocaleString()}.`:'Waiting for a retry time.'} Keep the app open to continue. After switching accounts, use Retry now; saved frames are reused.</small>
  </div>;
  if (status !== 'processing' || !progress) return null;
  const completed = Math.max(0, Math.min(progress.total, Number(progress.saved_analyzed??progress.analyzed) || 0));
  if (progress.phase === 'scene_scan') return <div className="source-progress" role="status">
    <span>{progress.total > 0 ? `Finding screen changes: ${completed} / ${progress.total} windows` : 'Finding screen changes…'}</span>
    <progress aria-label="Finding screen changes" value={progress.total > 0 ? completed : undefined} max={progress.total > 0 ? progress.total : undefined}/>
    <small>Completed audio is saved. Preparing the video frame analysis.</small>
    {progress.completed != null && <small>{progress.completed} windows completed successfully.</small>}
    {progress.gaps?.length ? <small>{progress.gaps.length} screen-change scan gaps need attention.</small> : null}
  </div>;
  if (!(progress.total > 0)) return null;
  const speech = progress.phase === 'speech';
  const label = speech ? 'Transcribing audio' : 'Reading video frames';
  return <div className="source-progress" role="status">
    <span>{label}: {completed} / {progress.total} {speech ? 'parts' : 'frames'}</span>
    <progress aria-label={label} value={completed} max={progress.total}/>
    {speech && <small>Completed audio is saved so the import can resume.</small>}
    {!speech && progress.visual_context != null && <small>{progress.visual_context} contextual observations · {progress.ocr || 0} OCR frames. Completed batches are searchable.</small>}
  </div>;
}
