export type MediaProgress = { phase?: string; analyzed: number; total: number; timestamp: number; duration?: number; visual_context?: number; ocr?: number };

export default function SourceProgress({ status, progress }: { status?: string; progress?: MediaProgress }) {
  if (status !== 'processing' || !progress) return null;
  if (progress.phase === 'scene_scan') return <div className="source-progress" role="status">
    <span>Finding screen changes…</span>
    <progress aria-label="Finding screen changes"/>
    <small>Audio is saved. Preparing the video frame analysis.</small>
  </div>;
  if (!(progress.total > 0)) return null;
  const completed = Math.max(0, Math.min(progress.total, Number(progress.analyzed) || 0));
  const speech = progress.phase === 'speech';
  const label = speech ? 'Transcribing audio' : 'Reading video frames';
  return <div className="source-progress" role="status">
    <span>{label}: {completed} / {progress.total} {speech ? 'parts' : 'frames'}</span>
    <progress aria-label={label} value={completed} max={progress.total}/>
    {speech && <small>Completed audio is saved so the import can resume.</small>}
    {!speech && progress.visual_context != null && <small>{progress.visual_context} contextual observations · {progress.ocr || 0} OCR frames. Completed batches are searchable.</small>}
  </div>;
}
