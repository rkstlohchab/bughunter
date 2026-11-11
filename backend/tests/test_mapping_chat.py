"""Conversation lifecycle around cited research and native receipt evidence."""
import base64
import json
from types import SimpleNamespace
import pytest
from bughunter.app import Application
from bughunter.mapping_chat import observed_value

TEXT = 'The score in fixture.bin is read at module offset 0x100 with pointer offset 0x10.'
CONTEXT = {'pid':42,'process_instance_id':'42:fixture:one','program':'fixture.bin','build_sha256':'a'*64}

@pytest.fixture
def app(tmp_path, monkeypatch):
    import bughunter.memory_mappings as module
    application = Application(tmp_path, primary=False)
    monkeypatch.setattr(module.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda:None))
    monkeypatch.setattr(module, 'probe_provider', lambda _s: {'ok':True})
    monkeypatch.setattr(application.settings, 'get', lambda: {'provider':'ollama','model':'fixture','local_only':True})
    def propose(_cfg, messages, *_args):
        ref=json.loads(messages[1]['content'])['references'][0]
        return json.dumps({'candidates':[{'kind':'module_pointer','module_name':'fixture.bin',
            'rva':'0x100','offsets':['0x10'],'value_type':'i32','reference_id':ref['id'],'quote':TEXT,'label':'Score'}]})
    monkeypatch.setattr(module,'request_chat',propose)
    yield application
    application.shutdown()


def source(app, project_id=None):
    row=app.knowledge.import_source(filename='score.md', content_base64=base64.b64encode(TEXT.encode()).decode(), project_id=project_id)
    app.knowledge.process_source(row['id'])
    return row['id']


def start(app, message='Research memory mappings for score', source_ids=None):
    started=app.dispatch('chat.mapping.start',{'message':message,'search_web':False,'source_ids':source_ids or [source(app)]})
    sid=started['session']['id']
    selected=app.dispatch('chat.mapping.select',{'id':sid,'context':CONTEXT})
    app.memory_mappings._work(selected['research']['id'])
    return sid


def receipt(app, result, **overrides):
    check=result['next']; request=check['request']; expected=int(request['expected_value'])
    native={**CONTEXT,'read_only':True,'expected_input':request['expected_value'],'expected':expected,
        'samples':[{'address':'0x1234','value':expected,'matched':True}],**overrides}
    (app.memory_mappings.receipts/(check['check_id']+'.json')).write_text(json.dumps(
        {'schema':'bughunter.mapping-receipt/v1','check_id':check['check_id'],'request':request,'result':native}))


def test_conversation_completes_and_restores_cited_report(app):
    sid=start(app)
    ready=app.dispatch('chat.mapping.advance',{'id':sid})
    assert ready['workflow']['status']=='needs_value'
    assert 'numeric value' in ready['session']['messages'][-1]['content']
    for value,status in [('100','needs_value'),('90','completed')]:
        app.dispatch('chat.mapping.reply',{'id':sid,'message':value})
        pending=app.dispatch('chat.mapping.advance',{'id':sid})
        assert app.dispatch('chat.mapping.advance',{'id':sid})['next']['check_id']==pending['next']['check_id']
        receipt(app,pending)
        done=app.dispatch('chat.mapping.advance',{'id':sid})
        assert done['workflow']['status']==status
    report=done['session']['messages'][-1]
    assert '1 verified value mappings' in report['content']
    assert report['citations'][0]['quote']==TEXT
    assert report['citations'][0]['source_id']
    assert 'fixture.bin + 0x100' in report['content'] and '0x1234' in report['content']
    assert app.dispatch('chat.mapping.advance',{'id':sid})['session']['messages']==done['session']['messages']
    exported=json.loads(app.dispatch('chat.export',{'id':sid})['content'])
    assert exported['mapping_workflows'][0]['research']['verified_count']==1
    restored=Application(app.store.root,primary=False)
    try: assert restored.dispatch('chat.get',{'id':sid})['messages']==done['session']['messages']
    finally: restored.shutdown()


def test_initial_value_and_wrong_process_never_verifies(app):
    sid=start(app,'Research memory mappings for score; score is 100')
    check=app.dispatch('chat.mapping.advance',{'id':sid})
    assert check['next']['request']['expected_value']=='100'
    receipt(app,check,process_instance_id='42:other')
    result=app.dispatch('chat.mapping.advance',{'id':sid})
    assert result['workflow']['status']=='blocked'
    assert result['research']['verified_count']==0


def test_stop_pending_check_cannot_resume(app):
    sid=start(app,'Research memory mappings; value is 100')
    check=app.dispatch('chat.mapping.advance',{'id':sid})['next']['check_id']
    assert app.dispatch('chat.mapping.stop',{'id':sid})['workflow']['status']=='cancelled'
    assert app.dispatch('chat.mapping.advance',{'id':sid})['next'] is None
    with pytest.raises(ValueError): app.memory_mappings.pending(check)


def test_same_number_cannot_advance_verification(app):
    sid=start(app,'Research memory mappings; value is 100')
    check=app.dispatch('chat.mapping.advance',{'id':sid});receipt(app,check)
    app.dispatch('chat.mapping.advance',{'id':sid})
    again=app.dispatch('chat.mapping.reply',{'id':sid,'message':'0100'})
    assert again['workflow']['round']==1
    assert again['workflow']['status']=='needs_value'
    assert 'same value' in again['session']['messages'][-1]['content']


def test_scope_and_active_task_isolation(app):
    for project in ('project_a','project_b'): app.store.put('project',{'name':project},id=project)
    private=source(app,'project_a')
    with pytest.raises(ValueError,match='unavailable'):
        app.dispatch('chat.mapping.start',{'message':'Research memory mappings','project_id':'project_b','source_ids':[private]})
    sid=start(app)
    with pytest.raises(ValueError,match='another project'):
        app.dispatch('chat.mapping.start',{'id':sid,'message':'Research memory mappings','project_id':'project_a'})
    with pytest.raises(ValueError,match='mapping task'):
        app.dispatch('chat.agent',{'id':sid,'message':'Another task'})
    with pytest.raises(ValueError):
        app.dispatch('chat.mapping.reply',{'id':sid,'message':'Use this reference','source_ids':[private]})


def test_stop_all_and_delete_conversation(app):
    sid=start(app)
    app.dispatch('system.stop',{})
    assert app.dispatch('chat.mapping.status',{'id':sid})['workflow']['status']=='cancelled'
    app.store.set_setting('emergency_stop',False)
    sid=start(app)
    app.dispatch('chat.delete',{'id':sid})
    assert app.store.get('chat',sid) is None
    app.dispatch('system.stop',{})


def test_references_can_be_added_in_conversation_and_history_is_retained(app):
    sid=start(app)
    app.dispatch('chat.mapping.advance',{'id':sid})
    updated=app.dispatch('chat.mapping.reply',{'id':sid,'message':'Use this source. Value is 30','source_ids':[source(app)]})
    assert updated['workflow']['status']=='researching'
    assert len(updated['workflow']['research_ids'])==2
    app.memory_mappings._work(updated['research']['id'])
    check=app.dispatch('chat.mapping.advance',{'id':sid})
    assert check['next']['request']['expected_value']=='30'
    exported=json.loads(app.dispatch('chat.export',{'id':sid})['content'])
    assert len(exported['mapping_workflows'][0]['attempts'])==2


@pytest.mark.parametrize('text', ['Research memory mappings for build 2026 PID 42','Version 100.4','0x1234'])
def test_does_not_infer_visible_value_from_unrelated_numbers(text):
    assert observed_value(text,initial=True) is None


@pytest.mark.parametrize('text,expected', [('The score is 100.','100'),('Now 90.','90'),('Value is 1.5.','1.5'),('value is 9007199254740993','9007199254740993')])
def test_values_accept_sentence_punctuation_without_losing_integer_precision(text,expected):
    assert observed_value(text)==expected


def test_check_errors_can_reselect_without_reusing_old_instance(app):
    sid=start(app,'Research memory mappings; value is 100')
    check=app.dispatch('chat.mapping.advance',{'id':sid})
    selected=app.dispatch('chat.mapping.reply',{'id':sid,'message':'choose program'})
    assert selected['workflow']['requires_selection']
    assert selected['workflow']['context'] is None
    with pytest.raises(ValueError): app.memory_mappings.pending(check['next']['check_id'])
    result=app.dispatch('chat.mapping.select',{'id':sid,'context':{**CONTEXT,'process_instance_id':'42:fixture:two'}})
    assert result['workflow']['context']['process_instance_id']=='42:fixture:two'
    assert len(result['workflow']['research_ids'])==2


def test_six_round_limit_reports_unverified_results(app):
    sid=start(app,'Research memory mappings; value is 100')
    for n in range(6):
        if n: app.dispatch('chat.mapping.reply',{'id':sid,'message':str(100-n)})
        pending=app.dispatch('chat.mapping.advance',{'id':sid})
        receipt(app,pending,samples=[{'address':'0x1234','value':0,'matched':False}])
        result=app.dispatch('chat.mapping.advance',{'id':sid})
    assert result['workflow']['status']=='limited'
    assert result['research']['verified_count']==0
    assert result['session']['messages'][-1]['citations']
    assert app.dispatch('chat.mapping.advance',{'id':sid})['next'] is None


def test_web_search_can_be_requested_in_chat(app,monkeypatch):
    sid=start(app)
    app.dispatch('chat.mapping.advance',{'id':sid})
    updated=app.dispatch('chat.mapping.reply',{'id':sid,'message':'search the web'})
    assert updated['research']['search_web'] is True
    assert len(updated['workflow']['research_ids'])==2


def test_selection_failure_is_durable_and_does_not_start_research(app):
    started=app.dispatch('chat.mapping.start',{'message':'Research memory mappings for my program','search_web':False})
    sid=started['session']['id']
    reason='macOS refused memory access to fixture.bin. No mapping checks ran.'
    blocked=app.dispatch('chat.mapping.pause',{'id':sid,'reason':reason})
    assert blocked['workflow']['status']=='blocked'
    assert blocked['workflow']['context'] is None
    assert blocked['research'] is None
    assert any(reason in m['content'] for m in blocked['session']['messages'])
    for _ in range(3):
        refreshed=app.dispatch('chat.mapping.advance',{'id':sid})
        assert refreshed['workflow']['progress']==reason
        assert refreshed['workflow']['status']=='blocked'
    assert app.store.list('mapping_check')==[]
    chosen=app.dispatch('chat.mapping.reply',{'id':sid,'message':'choose program'})
    assert chosen['workflow']['status']=='needs_program'
    assert chosen['workflow']['requires_selection'] is True
    assert any(reason in m['content'] for m in chosen['session']['messages'])
