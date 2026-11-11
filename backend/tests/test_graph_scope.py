from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


def test_graph_filters_sources_and_edges_before_limit(tmp_path):
    store = Store(tmp_path)
    knowledge = KnowledgeService(store)
    shared = store.put('source', {'title': 'Shared'})
    own = store.put('source', {'title': 'Own', 'project_id': 'one'})
    foreign = store.put('source', {'title': 'Foreign', 'project_id': 'two'})
    with store.connection() as db:
        for source in (shared, own, foreign):
            knowledge._node(db, source['id'], 'source', source['title'], {'source_id': source['id']})
        knowledge._node(db, 'concept', 'technique', 'Coupon eligibility', {})
        for source in (own, foreign):
            knowledge._edge(db, source['id'], 'concept', 'describes', source['id'], None, {})
        for i in range(40):
            knowledge._node(db, f'foreign{i}', 'example', f'AAA {i}', {'source_id': foreign['id']})
        knowledge._node(db, 'verified', 'observation', 'Verified coupon finding', {'source_id': own['id']})
    graph = knowledge.dispatch('knowledge.graph', {'project_id': 'one', 'limit': 10})
    assert {n['id'] for n in graph['nodes']} == {shared['id'], own['id'], 'concept', 'verified'}
    assert graph['total_nodes'] == 4 and not graph['truncated']
    assert graph['nodes'][0]['id'] == 'verified'
    assert {e['source_id'] for e in graph['edges']} == {own['id']}
    library = knowledge.graph(project_id=None)
    assert {n['id'] for n in library['nodes']} == {shared['id']}
    assert not library['edges']
    # Search runs before pagination, including results beyond the first page.
    assert len(knowledge.graph(limit=10)['nodes']) == 10
    found = knowledge.graph(limit=10, project_id='two', query='AAA 39')
    assert [n['id'] for n in found['nodes']] == ['foreign39']
    assert found['total_nodes'] == 1
