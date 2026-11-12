"""Stop/resume, passive permission checks and reviewed chat tasks."""
import base64
import sys
import threading
import time

import pytest

from bughunter.app import Application
from bughunter.policy import scope_hash


@pytest.fixture
def app(tmp_path):
    instance = Application(tmp_path, primary=False)
    yield instance
    instance.shutdown()


def test_stop_latches_before_cancellation_and_continues_after_one_failure(app, monkeypatch):
    seen = []
    def broken():
        assert app.store.setting('emergency_stop') is True
        raise RuntimeError('fixture cancellation failed')
    monkeypatch.setattr(app.memory_mappings, 'stop_all', broken)
    monkeypatch.setattr(app.readiness_p7, 'cancel_all', lambda: seen.append('checks'))
    run = app.store.put('run', {'status':'running', 'events':[]})
    source = app.store.put('source', {'status':'queued'})
    course = app.store.put('course_batch', {'status':'queued'})
    desktop = app.store.put('work_task', {'kind':'desktop_control','status':'running','events':[]})
    result = app.dispatch('system.stop', {})
    assert result['stopped'] and result['cancellation_errors'][0]['task'] == 'mapping research'
    assert seen == ['checks']
    assert app.store.get('run',run['id'])['status'] == 'cancelled'
    assert app.store.get('source',source['id'])['status'] == 'cancelled'
    assert app.store.get('course_batch',course['id'])['status'] == 'cancelled'
    assert app.store.get('work_task',desktop['id'])['status'] == 'interrupted'
    with pytest.raises(ValueError,match='stopped'):app.runs.start_agent('continue')
    with pytest.raises(ValueError,match='stopped'):
        app.knowledge.import_source(filename='test.txt',content_base64=base64.b64encode(b'hello').decode())
    app.dispatch('system.resume', {})
    assert app.store.setting('emergency_stop') is False
    assert app.store.get('source',source['id'])['status'] == 'cancelled'
    assert app.store.get('course_batch',course['id'])['status'] == 'cancelled'
    assert app.store.get('run',run['id'])['status'] == 'cancelled'


@pytest.mark.parametrize("runner", ["knowledge", "media_setup"])
def test_stop_terminates_local_media_subprocess_even_after_resume(app, tmp_path, runner):
    ready = tmp_path/'started'
    failures = []
    def run():
        try:
            execute=app.knowledge._run if runner=="knowledge" else app.media._run_setup
            execute([sys.executable,'-c',
                'import pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text("started");time.sleep(30)',str(ready)],timeout=30)
        except Exception as exc:failures.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic()+3
    while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert ready.exists()
    app.dispatch('system.stop', {})
    app.dispatch('system.resume', {})
    thread.join(2)
    assert not thread.is_alive()
    assert len(failures)==1 and isinstance(failures[0],InterruptedError)


def test_setup_check_is_passive_and_keeps_unknown_permissions_distinct(app, monkeypatch):
    monkeypatch.setattr(app.store,'require_desktop_owner',lambda:None)
    monkeypatch.setattr('bughunter.app.platform.system',lambda:'Darwin')
    monkeypatch.setattr('bughunter.tool_readiness.inventory',lambda _:[])
    monkeypatch.setattr(app.media,'status',lambda:{})
    monkeypatch.setattr(app.media,'start',lambda:pytest.fail('Settings must not start a service'))
    monkeypatch.setattr(app.tools,'gateway_start',lambda **_:pytest.fail('Settings must not start a gateway'))
    monkeypatch.setattr(app,'_full_disk_access',lambda:None)
    app.store.set_setting('emergency_stop',True)
    result=app.autosetup(False)
    assert result['backend_full_disk'] is None
    assert app.store.setting('emergency_stop') is True
    assert all(row['status']!='needs_you' for row in result['steps'])


def test_task_start_requires_review_of_current_scope_and_reports_once(app, monkeypatch):
    project=app.policy.save_project({'name':'Fixture','scope':['https://example.test/'],
        'authorization':'Fixture public pages','read_only_paths':['/'],'max_requests':15})
    app.store.set_setting('gateway',{'verified_at':time.time()})
    calls=[]
    def start(**kwargs):
        calls.append(kwargs)
        return app.store.put('work_task',{'kind':'website_audit','status':'completed','project_id':project['id'],
            'title':'Public review','result':{'pages':[{'path':'/','evidence_ids':['traffic_fixture']}],
            'requests_sent':1,'observations':[]},'events':[]})
    monkeypatch.setattr(app.website_audit,'start',start)
    args={'message':'audit the website','project_id':project['id'],'max_pages':3}
    with pytest.raises(ValueError,match='approve'):app.chat.task_start(args)
    with pytest.raises(ValueError,match='scope changed'):app.chat.task_start({**args,'approved':True,'expected_scope':'stale'})
    assert not calls
    session=app.chat.task_start({**args,'approved':True,'expected_scope':scope_hash(project)})
    assert len(calls)==1 and calls[0]['max_pages']==3
    result=app.chat.task_result({'id':session['id'],'task_id':session['task_id']})
    assert 'No vulnerability was automatically confirmed' in result['messages'][-1]['content']
    assert 'traffic_fixture' in result['messages'][-1]['content']
    again=app.chat.task_result({'id':session['id'],'task_id':session['task_id']})
    assert again['messages']==result['messages']
    with pytest.raises(ValueError,match='does not belong'):
        app.chat.task_result({'id':session['id'],'task_id':'unrelated'})


def test_stop_during_agent_preflight_does_not_launch_after_resume(app,monkeypatch):
    app.settings.save({'provider':'ollama','model':'fixture'})
    def preflight(_):
        app.dispatch('system.stop',{})
        app.dispatch('system.resume',{})
    monkeypatch.setattr(app.runs,'_preflight',preflight)
    monkeypatch.setattr(app.runs,'_work',lambda *_:pytest.fail('Cancelled preflight must never launch'))
    with pytest.raises(ValueError,match='stopped'):
        app.runs.start_agent('Read my notes')
    assert not app.runs.threads
    assert app.store.list('run')[0]['status']=='cancelled'


def test_full_disk_probe_handles_missing_user_database_without_reading_contents(app,monkeypatch):
    import builtins
    from contextlib import nullcontext
    monkeypatch.setattr('bughunter.app.platform.system',lambda:'Darwin')
    calls=[]
    def opened(path,mode):
        calls.append(str(path))
        assert mode=='rb'
        if len(calls)==1:raise FileNotFoundError()
        return nullcontext(object())
    monkeypatch.setattr(builtins,'open',opened)
    assert app._full_disk_access() is True
    assert calls[-1]=='/Library/Application Support/com.apple.TCC/TCC.db'
