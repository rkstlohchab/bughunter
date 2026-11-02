"""Shared articles are reusable, private project articles never cross projects."""
import base64
from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


def test_project_separated_knowledge_and_shared_material(tmp_path):
    store=Store(tmp_path)
    for id in ('project_a','project_b'):
        store.put('project', {'name':id}, id=id)
    library=KnowledgeService(store,start_worker=False)
    try:
        text=base64.b64encode(b'The quartz fox studies memory pointers.').decode('ascii')
        other=base64.b64encode(b'The quartz fox studies private offsets.').decode('ascii')
        a=library.import_source(filename='notes.md',content_base64=text,project_id='project_a')
        b=library.import_source(filename='notes.md',content_base64=text,project_id='project_b')
        shared=library.import_source(filename='common.md',content_base64=other)
        assert len({a['id'],b['id'],shared['id']})==3
        for row in (a,b,shared): library.process_source(row['id'])
        matches_a=library.search_for_project('quartz fox', 'project_a')
        matches_b=library.search_for_project('quartz fox', 'project_b')
        matches_none=library.search_for_project('quartz fox', None)
        assert a['id'] in {r['source_id'] for r in matches_a}
        assert b['id'] not in {r['source_id'] for r in matches_a}
        assert b['id'] in {r['source_id'] for r in matches_b}
        assert a['id'] not in {r['source_id'] for r in matches_b}
        assert {r['source_id'] for r in matches_none}=={shared['id']}
        assert shared['id'] in {r['source_id'] for r in matches_a}
    finally:
        library.shutdown()
