"""Scheduled subscription lanes reuse prior cited observations without relabeling them."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.local_media import LocalMediaRuntime
from bughunter.media_routing import frame_config, detail_config, VisualProviderFailure
from bughunter.store import Store


def test_claude_starts_only_on_the_second_lane_after_the_explicit_time():
    base={'vision_provider':'codex_subscription','codex_vision_model':'gpt-5.6-luna',
          'claude_parallel_enabled':True,'claude_parallel_after':2000,
          'claude_vision_model':'claude-opus-4-7'}
    assert frame_config(base,1,current_time=1999)['vision_provider']=='codex_subscription'
    assert frame_config(base,0,current_time=2000)['vision_provider']=='codex_subscription'
    assert frame_config(base,1,current_time=2000)['vision_provider']=='claude_subscription'
    assert base['vision_provider']=='codex_subscription'
    assert frame_config({**base,'claude_parallel_enabled':False},1,current_time=3000)['vision_provider']=='codex_subscription'
    assert detail_config({**base,'codex_detail_model':'gpt-6.1-sol'})['codex_vision_model']=='gpt-6.1-sol'
    assert detail_config(frame_config(base,1,current_time=2000))['vision_provider']=='claude_subscription'


def test_mixed_provider_resume_keeps_completed_frames_and_their_original_citations(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');runtime=LocalMediaRuntime(store)
    runtime.configure({'vision_provider':'codex_subscription','codex_vision_consent':True,
        'codex_vision_model':'gpt-6.1-sol','adaptive_frames':False,'frame_interval':2,'max_frames':0})
    service=KnowledgeService(store,start_worker=False,media_runtime=runtime)
    source=store.put('source',{'title':'Course','status':'processing','content_hash':'fixture'})
    output=store.root/'artifacts'/source['id'];output.mkdir()
    video=tmp_path/'video.mp4';video.write_bytes(b'video')
    speech=[{'modality':'transcript','text':'Compare the visible screens.','start':0,'end':6,'locator':'00:00:00'}]
    def run(args,timeout):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'6'},'streams':[{'codec_type':'video','duration':'6'}]}))
        Path(args[-1]).write_bytes(b'frame '+args[args.index('-ss')+1].encode())
        return SimpleNamespace(stdout='',stderr='')
    calls=[]
    def vision(image,stamp,**kwargs):
        if stamp==4 and not runtime.config()['claude_parallel_enabled']:
            raise InterruptedError('Pause after two completed frames')
        config=kwargs['config'];calls.append((stamp,config['vision_provider'],config.get('codex_vision_model')))
        return f'Visible facts: Cobalt frame {stamp}.\nInferred tutorial intent: Compare screens.'
    monkeypatch.setattr(service,'_run',run)
    monkeypatch.setattr(runtime,'vision',vision)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:name)
    try:
        with pytest.raises(InterruptedError):service._extract_media(video,output,source,transcript=speech)
        old=service.search('Cobalt')[0]
        runtime.configure({'codex_vision_model':'gpt-5.6-luna','claude_parallel_enabled':True,
            'claude_parallel_after':0,'claude_vision_consent':True,'claude_vision_model':'claude-opus-4-7'})
        service._processing.lane=1
        calls.clear()
        segments,_=service._extract_media(video,output,source,transcript=speech)
        assert calls==[(4.0,'claude_subscription','gpt-5.6-luna')]
        visuals=[row for row in segments if row['modality']=='vision']
        assert [row['provider'] for row in visuals]==['codex_subscription','codex_subscription','claude_subscription']
        assert [row['model'] for row in visuals]==['gpt-6.1-sol','gpt-6.1-sol','claude-opus-4-7']
        assert old['id'] in {row['id'] for row in service.search('Cobalt')}
        assert service.integrity()['status']=='healthy'
        runtime.configure({'claude_parallel_enabled':False})
        calls.clear()
        service._extract_media(video,output,store.get('source',source['id']),transcript=speech)
        assert not calls
        assert len(service.search('Cobalt'))==3
    finally:service.shutdown()


def test_actual_failed_provider_controls_wait_even_when_chatgpt_is_primary(tmp_path,monkeypatch):
    runtime=SimpleNamespace(config=lambda:{'vision_provider':'codex_subscription','codex_vision_model':'gpt-6.1-sol'})
    service=KnowledgeService(Store(tmp_path),start_worker=False,media_runtime=runtime)
    source=service.store.put('source',{'title':'Course','status':'queued'})
    def extract(*args):
        raise VisualProviderFailure('You hit your limit.','claude_subscription','claude-opus-4-7')
    monkeypatch.setattr(service,'_extract',extract)
    try:
        result=service.process_source(source['id'])
        assert result['provider_wait']['provider']=='claude_subscription'
        assert result['provider_wait']['model']=='claude-opus-4-7'
        assert result['media_progress']['retry_provider']=='Claude'
    finally:service.shutdown()


def test_high_resolution_retry_records_sol_and_reuses_it_after_restart(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');runtime=LocalMediaRuntime(store)
    runtime.configure({'vision_provider':'codex_subscription','codex_vision_consent':True,
        'codex_vision_model':'gpt-5.6-luna','codex_detail_model':'gpt-6.1-sol',
        'claude_parallel_enabled':True,'claude_vision_consent':True,'claude_parallel_after':0,
        'adaptive_frames':False,'frame_interval':2,'max_frames':0})
    service=KnowledgeService(store,start_worker=False,media_runtime=runtime)
    source=store.put('source',{'title':'Fine text','status':'processing','content_hash':'fine-text'})
    output=store.root/'artifacts'/source['id'];output.mkdir()
    video=tmp_path/'video.mp4';video.write_bytes(b'video')
    def run(args,timeout):
        if '-show_streams' in args:
            return SimpleNamespace(stdout=json.dumps({'format':{'duration':'1'},'streams':[{'codec_type':'video','duration':'1'}]}))
        Path(args[-1]).write_bytes(b'detail' if '-detail' in args[-1] else b'overview')
        return SimpleNamespace(stdout='',stderr='')
    calls=[]
    def vision(image,stamp,**kwargs):
        calls.append(kwargs['config']['codex_vision_model'])
        return ('Visible facts: Fine text is unreadable.\nUncertain or missing evidence: Need a clearer view.'
                if len(calls)==1 else 'Visible facts: The dependency is Jython.\nUncertain or missing evidence: Installation unverified.')
    monkeypatch.setattr(service,'_run',run);monkeypatch.setattr(runtime,'vision',vision)
    monkeypatch.setattr('bughunter.local_media.binary',lambda name:name)
    try:
        segments,_=service._extract_media(video,output,source)
        assert calls==['gpt-5.6-luna','gpt-6.1-sol']
        visual=next(row for row in segments if row['modality']=='vision')
        assert visual['model']=='gpt-6.1-sol' and visual['frame_path'].endswith('-detail.jpg')
        calls.clear()
        service._extract_media(video,output,store.get('source',source['id']))
        assert calls==[]
    finally:service.shutdown()
