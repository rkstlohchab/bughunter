"""Read-only coverage and exact cited instruction candidates, never a mastery score."""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from .course_p0 import ACTION_RE
from .store import now

UNREADABLE=re.compile(r'unreadable|illegible|too small to read|cannot read|hard to read|insufficiently legible|partially legible',re.I)
INAUDIBLE=re.compile(r'\binaudible\b|\bunintelligible\b|\[blank.audio\]',re.I)
TOOLS={'Burp Suite':r'\bburp\b','Jython':r'\bjython\b','GitHub':r'\bgithub\b',
       'Nuclei':r'\bnuclei\b','ffuf':r'\bffuf\b','Nmap':r'\bnmap\b',
       'Waymore':r'\bwaymore\b','Browser developer tools':r'\bdevtools\b|developer tools',
       'Cheat Engine':r'\bcheat engine\b','Ghidra':r'\bghidra\b'}


def number(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def stamp(value):
    value=max(0,int(value or 0))
    return f'{value//3600:02}:{value%3600//60:02}:{value%60:02}'


def excerpt(text,match):
    return text[max(0,match.start()-80):min(len(text),match.end()+180)]


def coverage(service, source_id):
    source=service.store.get('source',source_id)
    if not source:
        raise ValueError('Source not found.')
    with service.store.connection() as db:
        rows=[dict(row) for row in db.execute(
            "SELECT id,text,locator,modality,metadata,corrected FROM knowledge_chunks WHERE source_id=? AND coalesce(json_extract(metadata,'$.active'),1)=1 ORDER BY ordinal",(source_id,))]
    for row in rows:
        row['metadata']=json.loads(row['metadata'])
    plan=source.get('sampling_plan') or {}
    duration=number(plan.get('duration')) or number(source.get('video_duration')) or number(source.get('duration'))
    speech=source.get('speech_coverage') or {}
    duration=duration or number(speech.get('duration')) or 0
    media_type=source.get('media_type','')
    filename=source.get('filename','').lower()
    is_video=media_type.startswith('video/') or filename.endswith(('.mp4','.mov','.mkv','.webm','.avi'))
    is_audio=media_type.startswith('audio/') or filename.endswith(('.mp3','.wav','.m4a','.flac','.ogg'))
    planned={round(value,2) for item in plan.get('planned',[]) if (value:=number(item)) is not None and 0<=value<duration}
    plan_known=bool(planned)
    reported=source.get('media_progress') or {}
    reported_total=number((source.get('media_coverage') or {}).get('planned'))
    if reported_total is None and reported.get('phase') in {'frames','provider_wait'}:
        reported_total=number(reported.get('total'))
    visual_rows=[row for row in rows if row['modality']=='vision']
    ocr_rows=[row for row in rows if row['modality']=='ocr']
    transcript_rows=[row for row in rows if row['modality']=='transcript']
    visual={round(value,2) for row in visual_rows if (value:=number(row['metadata'].get('start'))) is not None}
    ocr={round(value,2) for row in ocr_rows if (value:=number(row['metadata'].get('start'))) is not None}
    processed=planned & (visual|ocr) if plan_known else set()
    unreadable=[]
    unclear=[]
    instruction_candidates=[]
    tools={}
    models=Counter()
    for row in rows:
        meta=row['metadata'];text=row['text']
        citation={'source_id':source_id,'chunk_id':row['id'],'locator':row['locator'],
                  'timestamp_seconds':number(meta.get('start'))}
        if row['modality']=='vision':
            models[f"{meta.get('provider','unknown')}:{meta.get('model','unknown')}"]+=1
            if match:=UNREADABLE.search(text):
                unreadable.append({**citation,'quote':excerpt(text,match),'status':'model_reported_unreadable'})
        if row['modality']=='transcript' and (match:=INAUDIBLE.search(text)):
            unclear.append({**citation,'quote':excerpt(text,match),'status':'transcript_reported_unclear'})
        for label,pattern in TOOLS.items():
            if label not in tools and (match:=re.search(pattern,text,re.I)):
                tools[label]={**citation,'name':label,'quote':excerpt(text,match),'status':'mentioned_in_source'}
        for line in text.splitlines():
            quote=line.strip()
            if 12<=len(quote)<=450 and ACTION_RE.search(quote):
                instruction_candidates.append({**citation,'quote':quote,
                    'basis':'model_description' if row['modality']=='vision' else 'narration' if row['modality']=='transcript' else 'source_text',
                    'verified':False})
    # Deduplicate generated sections at the same timestamp while preserving the
    # actual cited text. A heading or sentence is not independent test evidence.
    unique={(step['chunk_id'],step['quote']):step for step in instruction_candidates}
    candidates=list(unique.values())
    candidates.sort(key=lambda step:(step['timestamp_seconds'] if step['timestamp_seconds'] is not None else math.inf,step['chunk_id']))
    speech_seconds=number(speech.get('seconds')) or 0
    parts=number(speech.get('total')) or 0
    completed_parts=number(speech.get('completed')) or 0
    audio_complete=bool(parts>0 and completed_parts>=parts and speech_seconds>0)
    windows=[]
    for index in range(min(2000,math.ceil(duration/600))):
        start=index*600;end=min(duration,start+600)
        expected={value for value in planned if start<=value<end}
        read=expected & (visual|ocr)
        timed=[row for row in transcript_rows if (begin:=number(row['metadata'].get('start'))) is not None
            and begin<end and (number(row['metadata'].get('end')) or begin)>=start]
        windows.append({'start':start,'end':end,'label':stamp(start)+'–'+stamp(end),
            'audio':'processed' if speech_seconds>=end and parts else 'text_present' if timed else 'not_processed',
            'transcript_passages':len(timed),'planned_frames':len(expected) if plan_known else None,
            'completed_frames':len(read) if plan_known else sum(start<=value<end for value in visual|ocr),
            'status':'all_planned_samples_saved' if expected and read==expected else 'partial' if read else 'pending' if plan_known else 'plan_unavailable',
            'readability_issues':sum(start<=(item['timestamp_seconds'] or 0)<end for item in unreadable)})
    pending=sorted(planned-(visual|ocr)) if plan_known else []
    gaps=[]
    for value in pending:
        if gaps and value-gaps[-1]['last_timestamp']<=max(2,number(plan.get('interval')) or 30)*1.5:
            gaps[-1].update(last_timestamp=value,frames=gaps[-1]['frames']+1)
        else:gaps.append({'start':value,'last_timestamp':value,'frames':1})
    spacing=max((later-earlier for earlier,later in zip(sorted({0.0,duration,*planned}),sorted({0.0,duration,*planned})[1:])),default=None) if plan_known else None
    return {'source_id':source_id,'title':source.get('title'),'checked_at':now(),'source_status':source['status'],
        'kind':'video' if is_video else 'audio' if is_audio else 'document','duration_seconds':duration or None,
        'audio':{'completed_parts':int(completed_parts),'total_parts':int(parts),'processed_seconds':speech_seconds,
            'transcription_process_complete':audio_complete,'transcript_passages':len(transcript_rows),
            'unclear_passages':unclear[:100],'unclear_total':len(unclear),
            'limitation':'Completed transcription means the audio was processed; it does not establish that every spoken word was recognized correctly.'},
        'visual':{'plan_available':plan_known,'planned_frames':len(planned) if plan_known else int(reported_total) if reported_total is not None else None,
            'completed_planned_frames':len(processed) if plan_known else None,'saved_visual_observations':len(visual),
            'saved_ocr_frames':len(ocr),'additional_observations':len(visual-planned) if plan_known else None,
            'all_planned_samples_saved':bool(plan_known and len(processed)==len(planned)),
            'maximum_sampling_spacing_seconds':spacing,'unreadable_total':len(unreadable),
            'unreadable_intervals':unreadable[:100],'pending_ranges':gaps[:100],'models':dict(models),
            'limitation':'Screens are sampled. Short actions between samples can be missed, even when every planned sample is saved. Model-reported readability is not an independent accuracy check.'},
        'methods':{'candidate_steps':candidates[:100],'candidate_steps_total':len(candidates),
            'mentioned_tools':list(tools.values()),'independently_tested':False,
            'limitation':'These are exact quoted instruction candidates. Source text and model descriptions do not prove the method was understood or reproduced successfully.'},
        'windows':windows,'warnings':source.get('warnings',[]),'extraction_notes':source.get('extraction_notes',[]),
        'assurance':{'all_actions_captured':False,'method_reproduction_verified':False,'model_weights_trained':False,
            'meaning':'This is a cited retrieval library. Practical competence requires a separate task test with observed results.'}}
