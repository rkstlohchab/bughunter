import json
from pathlib import Path
import pytest
from bughunter.app import Application
from bughunter.pdf_vision import analyze_page,VERSION

@pytest.fixture
def pdf_app(tmp_path):
    import pymupdf
    from PIL import Image,ImageDraw
    image=tmp_path/'diagram.png';im=Image.new('RGB',(1200,800),'white');ImageDraw.Draw(im).text((40,40),'FORD -> Recon -> ToDo',fill='black');im.save(image)
    path=tmp_path/'diagram.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=960,height=540);page.insert_image(page.rect,filename=str(image));doc.save(path)
    app=Application(tmp_path/'data',primary=False)
    source=app.store.put('source',{'title':'Diagram','status':'partial','path':str(path),'filename':'diagram.pdf','content_hash':'fixture-hash','analysis_model':{'provider':'claude_subscription','model':'claude-opus-5-5','effort':'medium'}})
    yield app,source,path
    app.close()

def answer(unreadable=()):return json.dumps({'observations':'A central FORD node connects to Recon and ToDo.','adjacent_context':'','lessons':'Organize recon work as a map.','unreadable_details':list(unreadable)})

def test_selected_opus_replaces_old_provider_cache_and_uses_native_images(pdf_app,monkeypatch):
    from PIL import Image
    app,source,path=pdf_app;calls=[]
    def describe(service,config,prompt,images,check):
        calls.append(config);assert Image.open(images[1]).size==(1200,800);return answer()
    monkeypatch.setattr('bughunter.pdf_vision.describe',describe)
    previous={'page':1,'provider':'codex_subscription','model':'gpt-6.1-sol','text':'Old description','warnings':['Old unreadable warning']}
    result=analyze_page(app.knowledge,path,1,source,previous)
    assert result['model']=='claude-opus-5-5' and result['provider']=='claude_subscription' and result['warnings']==[]
    checkpoint=json.loads((app.store.root/'artifacts'/source['id']/'pdf-page-analysis.json').read_text());assert checkpoint['history']==[previous]
    assert analyze_page(app.knowledge,path,1,source,result)==result;assert len(calls)==1
    changed={**source,'analysis_model':{**source['analysis_model'],'effort':'high'}}
    analyze_page(app.knowledge,path,1,changed,result);assert len(calls)==2

def test_stop_all_discards_a_late_page_result(pdf_app,monkeypatch):
    app,source,path=pdf_app
    def describe(*args):app.store.set_setting('stop_generation',1);return answer()
    monkeypatch.setattr('bughunter.pdf_vision.describe',describe)
    with pytest.raises(InterruptedError):analyze_page(app.knowledge,path,1,source)
    assert not (app.store.root/'artifacts'/source['id']/'pdf-page-analysis.json').exists()

def test_unreadable_original_pixels_remain_explicit_and_missing_limits_are_rejected(pdf_app,monkeypatch):
    app,source,path=pdf_app;monkeypatch.setattr('bughunter.pdf_vision.describe',lambda *args:answer(['Small domain labels']))
    result=analyze_page(app.knowledge,path,1,source);assert 'claude-opus-5-5' in result['warnings'][0] and 'Small domain labels' in result['text']
    monkeypatch.setattr('bughunter.pdf_vision.describe',lambda *args:json.dumps({'observations':'Some text'}))
    with pytest.raises(ValueError,match='readability'):analyze_page(app.knowledge,path,1,source,{**result,'model':'old'})

def test_pdf_extraction_retries_model_mismatch_and_preserves_citations(pdf_app,monkeypatch):
    app,source,path=pdf_app;folder=app.store.root/'artifacts'/source['id'];folder.mkdir()
    (folder/'pdf-page-analysis.json').write_text(json.dumps({'source_hash':'fixture-hash','pages':[{'page':1,'text':'Old provider description','provider':'codex_subscription','model':'gpt-6.1-sol','warnings':['Old unreadable warning']}]}))
    monkeypatch.setattr('bughunter.pdf_vision.describe',lambda *args:answer())
    segments,warnings=app.knowledge._extract_pdf(path,source['id'])
    visual=next(s for s in segments if s['modality']=='vision');assert visual['model']=='claude-opus-5-5' and visual['locator']=='Page 1 · image interpretation';assert not warnings

def test_failed_opus_analysis_keeps_existing_page_evidence_and_reports_failure(pdf_app,monkeypatch):
    app,source,path=pdf_app;folder=app.store.root/'artifacts'/source['id'];folder.mkdir()
    (folder/'pdf-page-analysis.json').write_text(json.dumps({'source_hash':'fixture-hash','pages':[{'page':1,'text':'Old provider description','provider':'codex_subscription','model':'gpt-6.1-sol','warnings':[]}]}))
    def fail(*args):raise ValueError('Provider unavailable')
    monkeypatch.setattr('bughunter.pdf_vision.describe',fail)
    segments,warnings=app.knowledge._extract_pdf(path,source['id']);assert any('Old provider description' in s['text'] for s in segments);assert any('Provider unavailable' in w for w in warnings)

def test_local_pdf_uses_the_existing_cancellable_inference_path(pdf_app,monkeypatch):
    from bughunter.pdf_vision import describe
    app,source,path=pdf_app;calls=[]
    monkeypatch.setattr(app.knowledge,'_source_vision',lambda *a,**kw:calls.append(kw) or answer())
    result=describe(app.knowledge,{'vision_provider':'local','vision_model':'fixture'},'Read the original page',[path],lambda:None)
    assert json.loads(result)['unreadable_details']==[];assert calls[0]['response_format']=='json';assert 'Read the original page' in calls[0]['prompt_override'];assert callable(calls[0]['cancel_check'])

def test_completed_page_readability_limits_are_notes_and_provider_failures_remain_errors(pdf_app,monkeypatch):
    from bughunter.knowledge import extraction_messages
    app,source,path=pdf_app;monkeypatch.setattr('bughunter.pdf_vision.describe',lambda *args:answer(['A cropped edge label is not fully present in the source image.']))
    segments,messages=app.knowledge._extract_pdf(path,source['id']);warnings,notes=extraction_messages(messages)
    assert not warnings and len(notes)==1 and 'cropped edge' in notes[0]
    assert any('cropped edge' in segment['text'] for segment in segments)
    errors,_=extraction_messages(['Page 1: analysis with the selected model could not finish'])
    assert errors
