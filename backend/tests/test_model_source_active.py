"""Model source pages contain current evidence, while desktop audit stays intact."""
import json
from types import SimpleNamespace

import pytest

from bughunter.knowledge import KnowledgeService
from bughunter.model_tools import ModelTools
from bughunter.store import Store


def fixture(tmp_path):
    store = Store(tmp_path/'store')
    knowledge = KnowledgeService(store,start_worker=False)
    source = store.put('source',{'title':'Course','status':'processing','project_id':None})
    run = store.put('run',{'status':'running','kind':'study','source_id':source['id'],'project_id':None})
    app = SimpleNamespace(store=store,knowledge=knowledge,settings=SimpleNamespace(get=lambda:{'provider':'ollama'}))
    return knowledge,source,ModelTools(app,run['id'],study=True)


def test_study_source_excludes_rejected_passages_and_correction_audit(tmp_path):
    knowledge,source,model = fixture(tmp_path)
    root=knowledge.store.root/'artifacts'/source['id'];root.mkdir()
    knowledge._publish_media(source,[
        {'text':'Rejected obsolete charcoal claim.','locator':'Frame at 00:00:00','modality':'vision','context_key':'first'},
        {'text':'Outdated active statement.','locator':'Frame at 00:00:30','modality':'vision','context_key':'second'},
    ],root)
    bad=knowledge.search('charcoal')[0]['id']
    good=knowledge.search('Outdated active')[0]['id']
    knowledge.correct(bad,'Rejected corrected charcoal claim.')
    knowledge.correct(good,'Current emerald technique.')
    knowledge.reject(bad,'Unsupported frame interpretation.')
    result=model.call('get_source',{'id':source['id']})
    assert [c['id'] for c in result['chunks']]==[good]
    assert result['chunk_window']=={'offset':0,'count':1,'total':1,'next_offset':None}
    assert result['chunk_count']==1
    assert 'corrections' not in result and 'original_text' not in result['chunks'][0]
    assert 'charcoal' not in json.dumps(result) and 'Outdated active' not in json.dumps(result)
    assert not model.call('search_knowledge',{'query':'charcoal'})
    assert model.call('get_source',{'id':source['id'],'offset':1})['chunks']==[]
    desktop=knowledge.get_source(source['id'])
    assert len(desktop['chunks'])==2 and len(desktop['corrections'])==2
    assert any(c['original_text']=='Rejected obsolete charcoal claim.' for c in desktop['chunks'])


def test_inactive_chunks_do_not_displace_active_pagination(tmp_path,monkeypatch):
    knowledge,source,model=fixture(tmp_path)
    rows=[]
    for i in range(30):
        rows.extend([{'id':f'old-{i}','text':'withdrawn','active':False},
                     {'id':f'current-{i}','text':'current','original_text':'audit only'}])
    monkeypatch.setattr(knowledge,'get_source',lambda _: {'chunks':rows,'chunk_count':60,'corrections':[{'previous_text':'withdrawn'}]})
    first=model.call('get_source',{'id':source['id']})
    assert first['chunk_window']=={'offset':0,'count':25,'total':30,'next_offset':25}
    assert [c['id'] for c in first['chunks']]==[f'current-{i}' for i in range(25)]
    last=model.call('get_source',{'id':source['id'],'offset':25})
    assert last['chunk_window']=={'offset':25,'count':5,'total':30,'next_offset':None}
    assert [c['id'] for c in last['chunks']]==[f'current-{i}' for i in range(25,30)]
    assert 'audit only' not in json.dumps(first) and 'withdrawn' not in json.dumps(first)
