import os
import pytest
from bughunter.app import Application
from bughunter.game_runtime.service import birth

@pytest.fixture
def app(tmp_path,monkeypatch):
    a=Application(tmp_path,primary=False,native_credential='a'*64)
    monkeypatch.setattr(a.operator,'_loop',lambda *args:None)
    a.dispatch('gamelab.native.bootstrap',{'credential_hex':'a'*64})
    yield a
    a.close()

def task(a,mode='auto'):
    return a.harness.start('Test the game runtime using my course',grants=['app'],mode='game',execution_mode=mode,web_search_mode='off')

def test_private_credentials_and_public_tools_do_not_expose_native(app):
    with pytest.raises(PermissionError):app.dispatch('gamelab.native.poll',{})
    with pytest.raises(PermissionError):app.dispatch('gamelab.native.poll',{'credential_hex':'b'*64})
    with pytest.raises(PermissionError):app.dispatch('gamelab.native.bootstrap',{'credential_hex':'a'*64})
    t=task(app)
    from bughunter.operator_tools import OperatorTools
    tools=OperatorTools(app,t['run_id'])
    assert 'game_read' in {s['name'] for s in tools.specs()}
    assert not any('native.' in str(s) for s in tools.specs())
    with pytest.raises(ValueError):tools.app_action('gamelab.native.complete',{})
    ordinary=app.harness.start('Read my sources',['app'],web_search_mode='off')
    assert not any(s['name'].startswith('game_') for s in OperatorTools(app,ordinary['run_id']).specs())

def test_browser_no_native_executor(tmp_path,monkeypatch):
    a=Application(tmp_path,primary=False)
    monkeypatch.setattr(a.operator,'_loop',lambda *args:None)
    try:
        t=task(a)
        with pytest.raises(ValueError,match='Native executor'):a.game_runtime.fixture(t['id'])
        with pytest.raises(PermissionError):a.game_runtime.private('gamelab.native.bootstrap',{'credential_hex':'a'*64})
    finally:a.close()

def test_cross_task_session_and_plan_write_rejected(app):
    first=task(app);second=task(app,'plan')
    g=app.game_runtime;b=g.broker
    s=b.create_session('target',first['id'],birth(os.getpid()),'fp')
    with pytest.raises(PermissionError):g.read(s['id'],'0x1000',harness_id=second['id'])
    s2=b.create_session('target',second['id'],birth(os.getpid()),'fp')
    with pytest.raises(PermissionError,match='Plan mode'):g.write(s2['id'],'unknown',7)

def test_forged_read_and_process_birth_rejected(app):
    t=task(app);g=app.game_runtime;s=g.broker.create_session('target',t['id'],birth(os.getpid()),'fp')
    op=g.broker.enqueue(s['id'],'read','read_only',{'address':'0x1000','vtype':'uint'})
    with pytest.raises(ValueError,match='successful read'):g.write(s['id'],op['id'],7)
    with pytest.raises(ValueError):g.private('gamelab.native.register',{'credential_hex':'a'*64,'executor_id':'e','helper_pid':os.getpid(),'helper_start_time_ns':1,'process_instance_id':birth(os.getpid()),'target_fingerprint':'fp'})

def test_stop_queued_without_executor_and_no_replay(app):
    t=task(app);g=app.game_runtime;s=g.broker.create_session('target',t['id'],birth(os.getpid()),'fp')
    op=g.read(s['id'],'0x1000');g.stop(s['id'])
    assert g.broker.get_session(s['id'])['state']=='stopped'
    assert g.broker.get_operation(op['id'])['status']=='cancelled_before_dispatch'
    with pytest.raises(ValueError):g.read(s['id'],'0x1000')

def test_game_followup_has_persisted_course_context(app,monkeypatch):
    captured=[]
    original=app.operator.start
    def start(**kw):captured.append(kw);return original(**kw)
    monkeypatch.setattr(app.operator,'start',start)
    t=task(app)
    assert 'Cited course plan' in captured[0]['context']
    assert app.store.get('game_task_context','game_context:'+t['id'])['plan_id']
    monkeypatch.setattr(app.operator,'send',lambda **kw:captured.append(kw))
    app.harness.send(t['id'],'Use that lesson')
    assert 'Cited course plan' in captured[-1]['reference_context']
    assert app.harness.get(t['id'])['mode']=='game'

def test_plan_fixture_launch_rejected(app):
    t=task(app,'plan')
    with pytest.raises(PermissionError,match='Plan mode'):app.game_runtime.fixture(t['id'])

def test_recovery_waits_for_observed_helper_death(app):
    import subprocess,sys
    from bughunter.game_runtime.service import GameRuntimeService
    helper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
    g=app.game_runtime;b=g.broker;t=task(app)
    try:
        s=b.create_session('target',t['id'],birth(os.getpid()),'fp')
        e=b.register_executor('live','host',birth(helper.pid),s['process_instance_id'],'fp')
        recovered=GameRuntimeService(app,credential='c'*64,recover=True)
        assert recovered.broker.get_executor('live')['state']!='dead'
        with pytest.raises(ValueError):recovered.broker.register_executor('replacement','host',birth(os.getpid()),s['process_instance_id'],'fp')
        with pytest.raises(ValueError,match='not confirmed'):g.private('gamelab.native.stopped',{'credential_hex':'a'*64,'executor_id':'live','executor_epoch':0,'helper_death_confirmed':True})
        helper.kill();helper.wait(timeout=3);recovered.reconcile()
        assert recovered.broker.get_executor('live')['state']=='dead'
        assert recovered.broker.get_session(s['id'])['state']=='interrupted'
        assert recovered.broker.register_executor('replacement','host',birth(os.getpid()),s['process_instance_id'],'fp')['state']=='active'
    finally:
        if helper.poll() is None:helper.kill();helper.wait(timeout=3)


def test_native_type_schemas_match_adapter(app):
    from bughunter.operator_tools import OperatorTools
    from bughunter.memory_engine.mac_adapter import _SCALAR_FMT
    t=task(app)
    spec=next(s for s in OperatorTools(app,t['run_id']).specs() if s['name']=='game_read')
    assert set(spec['input_schema']['properties']['vtype']['enum'])==set(_SCALAR_FMT)

def test_temporary_client_shutdown_does_not_stop_desktop_sessions(app):
    from bughunter.game_runtime.service import GameRuntimeService
    t=task(app);b=app.game_runtime.broker
    session=b.create_session('desktop-target',t['id'],birth(os.getpid()),'fp')
    op=b.enqueue(session['id'],'read','read_only',{'address':'0x1000'})
    GameRuntimeService(app).shutdown()
    assert b.get_session(session['id'])['state']=='running'
    assert b.get_operation(op['id'])['status']=='queued'
