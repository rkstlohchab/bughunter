"""Cited hypotheses require exact native receipts before verification."""
import base64
import json
from types import SimpleNamespace
import pytest
from bughunter.app import Application
from bughunter.knowledge import KnowledgeService
from bughunter.memory_mappings import MemoryMappings, digest, numeric
from bughunter.store import Store

@pytest.fixture
def mappings(tmp_path):
    store = Store(tmp_path)
    knowledge = KnowledgeService(store, start_worker=False)
    app = SimpleNamespace(store=store, knowledge=knowledge,
        settings=SimpleNamespace(get=lambda: {'provider':'ollama','model':'fixture','local_only':True}),
        research=SimpleNamespace(search=lambda _p: {'id':'search','results':[]}))
    yield MemoryMappings(app)
    knowledge.shutdown()

def reference(service, text='score: fixture.bin at 0x100 with pointer hop 0x10 stores the observed score.', project_id=None):
    row = service.app.knowledge.import_source(filename='score.md',
        content_base64=base64.b64encode(text.encode()).decode(), project_id=project_id)
    service.app.knowledge.process_source(row['id'])
    with service.store.connection() as db:
        chunk = dict(db.execute('SELECT * FROM knowledge_chunks WHERE source_id=?', (row['id'],)).fetchone())
    return {'id':'ref','kind':'library','source_id':row['id'],'chunk_id':chunk['id'],
        'title':'Score reference','text':chunk['text'],'sha256':digest(chunk['text'])}

def ready(service, ref=None, kind='module_pointer', project_id=None):
    ref = ref or reference(service)
    job = service.store.put('mapping_research', {'status':'ready','project_id':project_id,'query':'score','program':'fixture.bin',
        'references':[ref], 'warnings':[], 'events':[], 'progress':'Ready'})
    candidate = service._save_candidate(job['id'], {'kind':kind,'module_name':'fixture.bin','rva':'0x100','offsets':['0x10'],
        'value_type':'i32','reference_id':ref['id'],'quote':ref['text'],'label':'Score'}, 'model_proposal')
    return job, candidate

def readback(service, candidate, expected='100', value=None, instance='42:instance:one', build='a'*64, error=None):
    check = service.challenge(candidate['id'], 42, instance, expected)
    result = {'pid':42,'process_instance_id':instance,'build_sha256':build,'read_only':True,
        'expected_input':str(expected),'expected':int(expected),
        'samples':[{'address':'0x1234','value':int(expected) if value is None else value,
                    'matched':value is None or value == int(expected)}]}
    receipt = {'schema':'bughunter.mapping-receipt/v1','check_id':check['id'],'request':check['request'],'result':result}
    if error: receipt['error'] = error
    path = service.receipts / (check['id']+'.json')
    path.write_text(json.dumps(receipt))
    return check, receipt, path

def test_two_distinct_values_persistence_and_citations(mappings):
    job, candidate = ready(mappings)
    for value, status in [('100','matched_once'),('100','matched_once'),('90','value_verified')]:
        check, _, _ = readback(mappings, candidate, value)
        row = mappings.observe(check['id'])
        assert row['candidates'][0]['status'] == status
        assert mappings.observe(check['id'])['candidates'][0]['observations'] == row['candidates'][0]['observations']
    exported = json.loads(MemoryMappings(mappings.app, recover=True).export(job['id'])['content'])
    assert exported['verified_count'] == 1
    assert exported['candidates'][0]['quote'] == exported['references'][0]['text']
    assert len(exported['candidates'][0]['observations']) == 3

def test_errors_mismatch_and_build_changes_break_confirmation(mappings):
    _, candidate = ready(mappings)
    for expected, value, build, error, status in [
        ('100',None,'a'*64,None,'matched_once'), ('90',None,'a'*64,None,'value_verified'),
        ('80',None,'b'*64,None,'matched_once'), ('70',None,'b'*64,'short read','check_failed'),
        ('60',None,'b'*64,None,'matched_once'), ('50',0,'b'*64,None,'mismatch'),
        ('40',None,'b'*64,None,'matched_once')]:
        check, _, _ = readback(mappings, candidate, expected, value, build=build, error=error)
        assert mappings.observe(check['id'])['candidates'][0]['status'] == status

@pytest.mark.parametrize('alter', [
    lambda r: r['request'].update(expected_value='999'),
    lambda r: r['result'].update(process_instance_id='another-process'),
    lambda r: r['result'].update(build_sha256=None),
    lambda r: r['result'].update(expected=99),
    lambda r: r['result']['samples'][0].update(matched=False),
    lambda r: r.update(schema='model-output'),
])
def test_rejects_bad_receipts(mappings, alter):
    _, candidate = ready(mappings)
    check, receipt, path = readback(mappings, candidate)
    alter(receipt); path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError): mappings.observe(check['id'])
    assert mappings.store.get('mapping_candidate', candidate['id'])['status'] == 'proposed'

def test_no_result_upload_or_missing_receipt(mappings):
    _, candidate = ready(mappings)
    check = mappings.challenge(candidate['id'],42,'42:instance','100')
    with pytest.raises(ValueError, match='native check receipt'): mappings.observe(check['id'])
    with pytest.raises(TypeError): mappings.observe(check['id'], tool_result={'matched':True})
    with pytest.raises(ValueError): mappings.observe('../escape')

def test_changed_source_revokes_status(mappings):
    job, candidate = ready(mappings)
    for value in ('100','90'):
        check, _, _ = readback(mappings,candidate,value); mappings.observe(check['id'])
    with mappings.store.connection() as db:
        db.execute('UPDATE knowledge_chunks SET text=? WHERE id=?', ('corrected source',job['references'][0]['chunk_id']))
    assert mappings.get(job['id'])['candidates'][0]['status'] == 'source_changed'
    assert json.loads(mappings.export(job['id'])['content'])['verified_count'] == 0
    with pytest.raises(ValueError, match='corrected or deleted'): mappings.challenge(candidate['id'],42,'42:instance','90')

def test_stop_and_recovery(mappings):
    job, candidate = ready(mappings)
    check, _, _ = readback(mappings,candidate)
    mappings.store.set_setting('emergency_stop',True)
    with pytest.raises(ValueError, match='stopped'): mappings.pending(check['id'])
    with pytest.raises(ValueError, match='stopped'): mappings.observe(check['id'])
    mappings.store.set_setting('emergency_stop',False)
    mappings.stop(job['id']); mappings._update(job['id'], status='ready')
    assert mappings.get(job['id'])['status'] == 'cancelled'
    with pytest.raises(ValueError, match='ready'): mappings.pending(check['id'])
    job,candidate=ready(mappings)
    check=mappings.challenge(candidate['id'],42,'42:instance','1')
    mappings._update(job['id'],status='researching')
    recovered=MemoryMappings(mappings.app,recover=True)
    assert recovered.get(job['id'])['status']=='interrupted'
    assert recovered.store.get('mapping_check',check['id'])['status']=='interrupted'

def test_scan_children_instance_binding_and_latest_evidence(mappings):
    job,candidate = ready(mappings,kind='value_scan')
    scan_id = None
    for expected in ('100','90'):
        check, receipt, path = readback(mappings,candidate,expected)
        scan_id = scan_id or check['id']
        receipt['result'].update(scan_id=scan_id,candidate_count=1,partial=False,truncated=False)
        path.write_text(json.dumps(receipt)); row = mappings.observe(check['id'])
    child = next(c for c in row['candidates'] if c['recipe']['kind']=='address')
    assert child['status']=='value_verified'
    with pytest.raises(ValueError,match='earlier process'): mappings.challenge(child['id'],42,'42:new-instance','80')
    check,receipt,path=readback(mappings,candidate,'80')
    receipt['result'].update(scan_id=scan_id,candidate_count=0,partial=False,truncated=False,samples=[])
    path.write_text(json.dumps(receipt)); row=mappings.observe(check['id'])
    assert next(c for c in row['candidates'] if c['id']==child['id'])['status']=='needs_recheck'
    assert row['verified_count']==0

def test_research_scopes_refs_and_rejects_invented_offsets(mappings,monkeypatch):
    import bughunter.memory_mappings as module
    for pid in ('project_a','project_b'): mappings.store.put('project',{'name':pid},id=pid)
    reference(mappings,project_id='project_a')
    other=reference(mappings,'score: private fixture.bin at 0x900 with pointer hop 0x20 stores score.','project_b')
    monkeypatch.setattr(module.threading,'Thread',lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module,'probe_provider',lambda _s: {'ok':True})
    def propose(_cfg,messages,*_args):
        refs=json.loads(messages[1]['content'])['references']
        assert other['source_id'] not in {r['source_id'] for r in refs}
        return json.dumps({'candidates':[{'kind':'module_pointer','value_type':'i32','module_name':'fixture.bin',
            'rva':'0xBAD','offsets':[],'reference_id':refs[0]['id'],'quote':refs[0]['text']}]})
    monkeypatch.setattr(module,'request_chat',propose)
    job=mappings.start('score','fixture.bin',project_id='project_a',search_web=False)
    mappings._work(job['id']); row=mappings.get(job['id'])
    assert row['status']=='ready' and row['candidates'][0]['origin']=='scan_plan'
    assert row['warnings'][0].startswith('Rejected proposal')
    assert mappings.dispatch('memory.mappings.list',{'project_id':'project_b'})==[]

def test_model_outage_leaves_cited_hypothesis(mappings,monkeypatch):
    import bughunter.memory_mappings as module
    reference(mappings)
    monkeypatch.setattr(module.threading,'Thread',lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module,'probe_provider',lambda _s: {'ok':False})
    job=mappings.start('score','fixture.bin',search_web=False)
    mappings._work(job['id']); row=mappings.get(job['id'])
    assert row['status']=='ready' and row['candidates'][0]['status']=='proposed'
    assert 'unavailable' in row['warnings'][0]

@pytest.mark.parametrize('value,vtype',[('nan','f32'),('inf','f64'),('128','i8'),('-1','u32'),('1.5','i32'),(True,'i32')])
def test_numeric_bounds(value,vtype):
    with pytest.raises(ValueError): numeric(value,vtype)

def test_rpc_wiring_and_agent_exclusion(tmp_path):
    app=Application(tmp_path,primary=False)
    try:
        assert app.dispatch('memory.mappings.list',{})==[]
        with pytest.raises(ValueError,match='ModelTools'): app.dispatch('memory.mappings.start',{},role='agent')
    finally: app.shutdown()

def test_new_check_supersedes_unfinished_and_native_request_is_exact(mappings):
    _,candidate=ready(mappings)
    first=mappings.challenge(candidate['id'],42,'42:instance','100')
    second=mappings.challenge(candidate['id'],42,'42:instance','90')
    assert mappings.store.get('mapping_check',first['id'])['status']=='superseded'
    with pytest.raises(ValueError): mappings.pending(first['id'])
    pending=mappings.pending(second['id'])
    assert pending['request']==second['request']
    assert pending['receipt_path'].endswith(second['id']+'.json')


def test_no_references_means_no_candidate(mappings,monkeypatch):
    import bughunter.memory_mappings as module
    monkeypatch.setattr(module.threading,'Thread',lambda **kw: SimpleNamespace(start=lambda:None))
    job=mappings.start('missing subject','fixture.bin',search_web=False)
    mappings._work(job['id'])
    row=mappings.get(job['id'])
    assert row['status']=='needs_references' and row['candidates']==[]


def test_supplied_link_passage_is_used_after_ingestion(mappings,monkeypatch):
    import bughunter.memory_mappings as module
    ref=reference(mappings)
    source=mappings.store.get('source',ref['source_id'])
    monkeypatch.setattr(module.threading,'Thread',lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module,'probe_provider',lambda _s:{'ok':False})
    monkeypatch.setattr(mappings.app.knowledge,'import_source',lambda **kw:source)
    monkeypatch.setattr(mappings.app.knowledge,'search_for_project',lambda *_a:[])
    job=mappings.start('different vocabulary','fixture.bin',search_web=False,urls=['https://example.org/reference'])
    mappings._work(job['id']); row=mappings.get(job['id'])
    assert row['status']=='ready' and row['references'][0]['source_id']==source['id']


def test_stopping_during_model_call_saves_no_candidate(mappings,monkeypatch):
    import bughunter.memory_mappings as module
    reference(mappings)
    monkeypatch.setattr(module.threading,'Thread',lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module,'probe_provider',lambda _s:{'ok':True})
    job=mappings.start('score','fixture.bin',search_web=False)
    def stop(*_args): mappings.stop(job['id']); return '{"candidates":[]}'
    monkeypatch.setattr(module,'request_chat',stop)
    mappings._work(job['id']); row=mappings.get(job['id'])
    assert row['status']=='cancelled' and row['candidates']==[]


def test_large_course_context_is_bounded_without_losing_citations(mappings, monkeypatch):
    import bughunter.memory_mappings as module
    quote = 'Scan the observed cooldown as a float.'
    ref = reference(mappings, quote + ' Course context.' * 200)
    passages = [dict(ref, id=ref['chunk_id'], title='Long course title ' * 100,
                     citation={'url':'https://example.org/' + 'a' * 1600}, locator='Document')
                for _ in range(10)]
    monkeypatch.setattr(mappings.app.knowledge, 'search_for_project', lambda *_a: passages)
    monkeypatch.setattr(module.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module, 'probe_provider', lambda _s: {'ok':True})
    calls = []
    def propose(config, messages, prompt, output_tokens, estimated):
        context = json.loads(messages[1]['content'])
        refs = context['references']
        calls.append(config['provider'])
        assert 1 <= len(refs) <= 4
        assert all(len(r['text'].encode()) <= 800 and len(r['title'].encode()) <= 80 for r in refs)
        assert all(set(r) == {'id','source_id','title','text'} for r in refs)
        assert sum(len(m['content'].encode()) for m in messages) <= 2800
        assert estimated <= 2800 and output_tokens == 1024
        assert 'f32 for float and f64 for double' in prompt
        return json.dumps({'candidates':[{'kind':'value_scan', 'value_type':'f32',
            'reference_id':refs[0]['id'], 'quote':quote, 'label':'Cooldown candidate'}]})
    monkeypatch.setattr(module, 'request_chat', propose)
    job = mappings.start('cooldown', 'fixture.bin', search_web=False)
    mappings._work(job['id'])
    row = mappings.get(job['id'])
    assert calls == ['ollama']
    assert row['candidates'][0]['origin'] == 'model_proposal'
    assert row['candidates'][0]['recipe']['value_type'] == 'f32'
    assert len(row['references']) == 10 and len(row['references'][0]['text']) > 800
    assert row['verified_count'] == 0
    assert row['references'][0]['sha256'] == digest(ref['text'])


def test_unicode_and_escaped_excerpts_fit_input_budget():
    from bughunter.memory_mappings import proposal_context, PROMPT
    refs = [{'id':'ref_'+'a'*32, 'source_id':'source_'+'b'*24,
             'text':'数值参考 \\ "\n' * 700, 'title':'長い講義名' * 100} for _ in range(10)]
    job = {'program':'程序' * 80, 'query':'数值' * 150}
    context = proposal_context(job, refs)
    assert len(PROMPT.encode()) + len(context.encode()) <= 2800
    for ref in json.loads(context)['references']:
        assert len(ref['text']) >= 12 and ref['text'] in refs[0]['text']
    assert len(refs[0]['text']) > 800


def test_shorter_prompt_still_rejects_rewritten_quotes(mappings, monkeypatch):
    import bughunter.memory_mappings as module
    reference(mappings)
    monkeypatch.setattr(module.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module, 'probe_provider', lambda _s: {'ok':True})
    def propose(_cfg, messages, *_args):
        ref = json.loads(messages[1]['content'])['references'][0]
        return json.dumps({'candidates':[{'kind':'value_scan', 'value_type':'f32',
            'reference_id':ref['id'], 'quote':'This rewritten quote does not occur in the source.'}]})
    monkeypatch.setattr(module, 'request_chat', propose)
    job = mappings.start('score', 'fixture.bin', search_web=False)
    mappings._work(job['id'])
    row = mappings.get(job['id'])
    assert row['verified_count'] == 0
    assert row['candidates'][0]['origin'] == 'scan_plan'
    assert any('exact quote' in w for w in row['warnings'])
