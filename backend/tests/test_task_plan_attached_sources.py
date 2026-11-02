"""Tasks must pin selected knowledge without breaking project separation."""
from types import SimpleNamespace
import pytest
from bughunter.store import Store
from bughunter.orchestrator import Orchestrator

class Knowledge:
    def search_for_project(self, query, project_id, limit=5):
        return []

class Skills:
    def match(self, query, project_id):
        return []


def test_plan_pins_selected_documents_and_cites_them(tmp_path):
    store=Store(tmp_path/'data')
    project=store.put('project', {'name':'Project A'})
    shared=store.put('source', {'filename':'shared-guide.pdf','status':'ready','chunk_count':2})
    private=store.put('source', {'title':'Project notebook','project_id':project['id'],'status':'ready','chunk_count':3})
    orchestrator=Orchestrator(SimpleNamespace(store=store,knowledge=Knowledge(),skills=Skills()))
    row=orchestrator.create('Inspect the references and prepare a report',project['id'],source_ids=[shared['id'],private['id']])
    assert row['source_ids']==[shared['id'],private['id']]
    result=orchestrator.run(row['id'])
    refs=result['steps'][1]['result']['references']
    assert {r['source_id'] for r in refs}=={shared['id'],private['id']}
    assert 'Project notebook' in orchestrator.report(row['id'])['content']


def test_plan_rejects_cross_project_or_unindexed_sources(tmp_path):
    store=Store(tmp_path/'data')
    a=store.put('project',{'name':'A'})
    b=store.put('project',{'name':'B'})
    foreign=store.put('source',{'title':'B secret','project_id':b['id'],'status':'ready','chunk_count':1})
    not_ready=store.put('source',{'title':'Queued','project_id':a['id'],'status':'queued','chunk_count':0})
    orchestrator=Orchestrator(SimpleNamespace(store=store))
    for source_id in (foreign['id'],not_ready['id'],'does-not-exist'):
        with pytest.raises(ValueError):
            orchestrator.create('Review these documents',a['id'],source_ids=[source_id])
    with pytest.raises(ValueError):
        orchestrator.create('Review these documents',a['id'],source_ids=['x']*7)
