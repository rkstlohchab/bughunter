"""Recover a prior hosted observation only when its exact evidence still matches."""
import hashlib
import json
from pathlib import Path

from .media_context import CONTEXT_VERSION, context_key, retained_timeline, save_json
from .media_routing import HOSTED, cache_model
from .store import now

MODEL_CHANGE='Visual provider, model or evidence version changed.'


def recover_hosted_observations(service, source_id):
    source=service.store.get('source',source_id)
    if not source:
        raise ValueError('Source not found.')
    if source.get('status')=='processing':
        raise ValueError('Wait for the active source worker before recovering its cache.')
    root=service.store.root/'artifacts'/source_id
    root.mkdir(exist_ok=True)
    timeline_path=root/'media_timeline.json'
    timeline=json.loads(timeline_path.read_text()) if timeline_path.exists() else []
    with service.store.connection() as db:
        rows=[dict(row) for row in db.execute(
            "SELECT * FROM knowledge_chunks WHERE source_id=? AND modality='vision' ORDER BY ordinal",(source_id,))]
    groups={}
    blocked=set()
    active=set()
    for row in rows:
        meta=json.loads(row['metadata']);stamp=meta.get('start')
        if row['corrected'] or meta.get('review_status')=='rejected':
            blocked.add(stamp)
        if meta.get('active',True):
            active.add(stamp)
        if (meta.get('provider') not in HOSTED or meta.get('context_version')!=CONTEXT_VERSION
                or meta.get('review_status')!='superseded' or meta.get('review_reason')!=MODEL_CHANGE):
            continue
        groups.setdefault((stamp,meta.get('context_key')),[]).append((row,meta))
    # Prefer the latest generation when several historical outputs share a timestamp.
    candidates={}
    skipped=0
    for (stamp,key),pieces in groups.items():
        if stamp in blocked or stamp in active:
            continue
        row,meta=pieces[-1]
        try:
            image=Path(meta['frame_path']).resolve(strict=True)
            if not image.is_relative_to(root.resolve()):
                raise ValueError('Image is outside its source directory.')
            previous=[]
            for item in meta.get('previous_frames',[]):
                path=(root/item['artifact_id']).resolve(strict=True)
                if not path.is_relative_to(root.resolve()):
                    raise ValueError('Previous image is outside its source directory.')
                previous.append({'path':str(path),'time':item['time']})
            actual=context_key(source.get('content_hash'),cache_model(meta),
                {'path':str(image),'time':stamp},previous,meta.get('narration_context',[]))
            if actual!=key:
                raise ValueError('The saved evidence hash changed.')
        except (ValueError,KeyError,OSError):
            skipped+=1
            continue
        excluded={'active','review_status','review_reason','reviewed_at','section'}
        entry={k:v for k,v in meta.items() if k not in excluded}
        ids=[piece['id'] for piece,_ in pieces]
        entry.update(text='\n\n'.join(piece['text'] for piece,_ in pieces),modality='vision',
            locator=row['locator'],recovery_id=hashlib.sha256('|'.join(ids).encode()).hexdigest(),
            recovered_from_chunks=ids,recovered_at=now(),
            recovery_reason='Reused prior observation after a provider selection change; exact image and narration hashes match.')
        candidates[stamp]=entry
    recovered=list(candidates.values())
    if recovered:
        save_json(timeline_path,retained_timeline(timeline,recovered))
        # New citation generations keep the superseded originals in audit.
        service._index(source,recovered,preserve_existing=True)
        with service.store.connection() as db:
            count=db.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1",(source_id,)).fetchone()[0]
        service._update(source_id,chunk_count=count,media_mixed_providers=True)
        service.store.event('knowledge.media_recovered',{'source_id':source_id,
            'observations':len(recovered),'verified_evidence_hashes':True})
    return {'source_id':source_id,'recovered_observations':len(recovered),
            'skipped_changed_evidence':skipped,'rejected_generations_untouched':True}
