import json
import pytest
from bughunter.app import Application

@pytest.fixture
def app(tmp_path):
    app=Application(tmp_path,primary=False)
    for id,title,filename,project in [('day2','Day 2 App Analysis hide01.ir','Day 2 App Analysis hide01.ir.mp4',None),('day1','Day 1 Recon','day1.mp4',None),('secret','Private Day 2 App Analysis','private.mp4','other')]:
        if project and not app.store.get('project',project):app.store.put('project',{'name':'Other'},id=project)
        source=app.store.put('source',{'title':title,'filename':filename,'status':'ready','project_id':project,'chunk_count':12},id=id)
        segments=[{'text':f'At stage {i}, the instructor describes mapping application features and recording Burp requests.','modality':'transcript','locator':f'00:{i:02d}:00','start':i*60,'end':i*60+30} for i in range(12)]
        app.knowledge._index(source,segments)
    yield app
    app.close()

@pytest.mark.parametrize('query',['What did you learn from that day 2 app recon Video resource','Day 2 App Analysis hide01.ir','Day Two App Analysis'])
def test_approximate_title_resolves_existing_video_without_web(app,query):
    resources=app.library.sources(query)['items'];assert resources[0]['source_id']=='day2';assert all(r['source_id']!='secret' for r in resources)
    context=app.library.context(query);assert 'day2' in context and 'LIVE APP LIBRARY' in context and 'not a website' in context;assert len(context)<12500

def test_summary_samples_full_timeline_and_exact_citations(app):
    result=app.library.search('Summarize the lessons in day 2 video');assert result['passages'][0]['start']==0;assert result['passages'][-1]['start']==660
    assert all(p['citation']['chunk_id']==p['id'] and p['citation']['source_id']=='day2' for p in result['passages'])

def test_project_isolation_and_rejected_excerpts(app):
    with pytest.raises(ValueError):app.library.read('secret')
    assert app.library.read('secret','other')['passages']
    with app.store.connection(immediate=True) as db:
        row=db.execute("SELECT * FROM knowledge_chunks WHERE source_id='day2' ORDER BY ordinal LIMIT 1").fetchone();app.knowledge._deactivate_chunk(db,row,'rejected','Fixture rejection')
    result=app.library.read('day2');assert result['passages'][0]['start']==60;assert all(p['id']!=row['id'] for p in app.library.search('Burp requests',source_id='day2')['passages'])

def test_paginated_read_has_no_duplicate_passages_and_followup_uses_history(app):
    first=app.library.read('day2',limit=8);second=app.library.read('day2',offset=first['next_offset']);assert len(first['passages'])==8 and len(second['passages'])==4
    assert not {p['id'] for p in first['passages']}&{p['id'] for p in second['passages']}
    context=app.library.context('Summarize that video',messages=[{'role':'user','content':'Day 2 App Analysis hide01.ir'}]);assert 'day2' in context

def test_each_reasoning_provider_receives_live_library_context_and_direct_tools(app,monkeypatch):
    import time
    captured=[]
    def turn(**kwargs):
        captured.append(kwargs);return {'message':{'role':'assistant','content':'The video covers mapping features [Day 2 App Analysis hide01.ir — 00:00:00].'},'tool_calls':[]}
    monkeypatch.setattr('bughunter.provider_api.single_tool_turn',turn)
    for provider in ('codex','claude_code','ollama','openai','gemini'):
        task=app.harness.start('What did you learn from that day 2 app recon Video resource',['app'],settings_override={'provider':provider,'model':'fixture'})
        deadline=time.monotonic()+3
        while app.harness.get(task['id'])['status']=='running' and time.monotonic()<deadline:time.sleep(.02)
        assert app.harness.get(task['id'])['status']=='awaiting_user'
    assert len(captured)==5
    for call in captured:
        assert {'library_sources','library_search','library_read'}<={s['name'] for s in call['tool_specs']}
        host=next(m['content'] for m in call['transcript'] if m.get('host_capabilities'));assert 'day2' in host and 'Burp requests' in host
        assert 'private.mp4' not in host
