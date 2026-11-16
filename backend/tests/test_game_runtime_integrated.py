"""Exercise service -> durable broker -> helper runtime with controlled process memory."""
import os
import struct
import time
from types import SimpleNamespace
from pathlib import Path
import pytest
from bughunter.store import Store
from bughunter.game_runtime.service import GameRuntimeService, birth
from bughunter.game_runtime.mappings import MappingStore
from bughunter.memory_engine.protocol import ProcessIdentity, read_process_start_time_ns
from bughunter.memory_engine.server import MemoryEngineServer, ServiceConfig
from bughunter.memory_engine.runtime import RuntimeOperations
from bughunter.memory_engine.ownership import ExecutorRegistry
from test_game_runtime_features import FakeAdapter


@pytest.fixture
def rig(tmp_path):
    store=Store(tmp_path)
    harness=SimpleNamespace(check=lambda *args:{'mode':'game','execution_mode':'full'})
    app=SimpleNamespace(store=store,harness=harness,primary=False)
    service=GameRuntimeService(app,credential='a'*64)
    service._bootstrapped=True;service._last_poll=time.monotonic()
    pid=os.getpid();instance=birth(pid)
    session=service.broker.create_session('fixture','task',instance,'a'*64,[])
    adapter=FakeAdapter()
    adapter.identity=ProcessIdentity(pid=pid,start_time_ns=read_process_start_time_ns(pid),target_fingerprint='a'*64)
    base=adapter.region_base
    adapter.write_value(base+32,60.,'f32')
    store.put('game_runtime_session',{'descriptor':{'fixture':True,'module_bases':{'Fake':hex(base)},
              'regions':[{'base':hex(base),'size':adapter.region_size}]}},id=session['id'])
    server=MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
    server.attach(adapter,session_id='native')
    runtime=RuntimeOperations(server)
    executor=service.broker.register_executor('executor','host',instance,instance,'a'*64)
    def execute(receipt):
        operation=service.broker.claim('executor',executor['executor_epoch'],operation_id=receipt['operation_id'])
        operation=service.broker.dispatched(operation['id'],'executor',executor['executor_epoch'],operation['attempt_id'],operation['revision'])
        params={**operation['arguments'],'session_id':'native','task_session_id':session['id'],'identity':adapter.identity.to_wire()}
        result=runtime.dispatch(params)
        service.private('gamelab.native.complete',{'credential_hex':'a'*64,'operation_id':operation['id'],
            'executor_id':'executor','executor_epoch':executor['executor_epoch'],'attempt_id':operation['attempt_id'],
            'revision':operation['revision'],'status':'succeeded','result':result})
        return result
    def save_observed(offset=32,name='field'):
        recipe=service.mapping_save(session['id'],name=name,root='mod:Fake+0x0',steps=[{'op':'add','offset':hex(offset),'width':4}],value_type='f32')
        pending=service.mapping_revalidate(session['id'],recipe['id'])
        result=execute(pending)
        observed=service.mapping_observe(session['id'],recipe['id'],pending['operation_id'])
        return observed,result
    yield SimpleNamespace(service=service,session=session,adapter=adapter,execute=execute,save=save_observed,runtime=runtime,base=base)
    runtime.close();server.shutdown()


def test_resolution_uses_pointer_recipe_not_observed_value_as_address(rig):
    recipe,result=rig.save()
    assert result['address']==hex(rig.base+32)
    assert result['value']==60.
    # Revalidate after the value changes. The saved address stays the same.
    rig.adapter.write_value(rig.base+32,5.,'f32')
    result=rig.execute(rig.service.mapping_revalidate(rig.session['id'],recipe['id']))
    assert result['value']==5. and result['address']!=hex(60)


def test_observe_rejects_receipt_after_recipe_revision_changes(rig):
    recipe,_=rig.save()
    pending=rig.service.mapping_revalidate(rig.session['id'],recipe['id']);rig.execute(pending)
    MappingStore(rig.service.store).revise(recipe['id'],name='revised field')
    with pytest.raises(ValueError,match='current-revision'):
        rig.service.mapping_observe(rig.session['id'],recipe['id'],pending['operation_id'])


def test_repeat_ticks_have_durable_receipts_and_stop_disables_schedule(rig):
    recipe,_=rig.save();sid=rig.session['id']
    start=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.,disable_value=60.)
    assert rig.execute(start)['value']==0.
    for _ in range(3):
        rig.adapter.write_value(rig.base+32,60.,'f32')
        schedule=rig.service.store.get('game_feature_schedule',sid+':timer')
        rig.service.store.put('game_feature_schedule',{**schedule,'next_tick':0},id=schedule['id'])
        rig.service._pump_features()
        schedule=rig.service.store.get('game_feature_schedule',sid+':timer')
        assert rig.execute({'operation_id':schedule['last_operation_id']})['value']==0.
    rig.service.stop(sid,feature_id='timer')
    assert rig.service.store.get('game_feature_schedule',sid+':timer')['enabled'] is False
    before=len(rig.service.broker.operations(sid));rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==before
    receipt=rig.runtime.dispatch({**rig.service.broker.get_operation(start['operation_id'])['arguments'],
       'runtime_action':'feature_stop','session_id':'native','task_session_id':sid,'identity':rig.adapter.identity.to_wire()})
    assert receipt['state']=='stopped' and receipt['cleanup_status']=='pass'
    assert struct.unpack('<f',rig.adapter.read_bytes(rig.base+32,4))[0]==60.


def test_live_start_requires_runtime_guards_before_any_enqueue(rig):
    recipe,_=rig.save();sid=rig.session['id']
    descriptor=rig.service.store.get('game_runtime_session',sid)['descriptor']
    rig.service.store.put('game_runtime_session',{'descriptor':{**descriptor,'fixture':False}},id=sid)
    with pytest.raises(ValueError,match='guards'):
        rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.)
    assert not rig.service.store.list('game_feature_schedule')


def test_unknown_tick_is_never_replayed(rig):
    recipe,_=rig.save();sid=rig.session['id']
    start=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.)
    rig.execute(start)
    schedule=rig.service.store.get('game_feature_schedule',sid+':timer')
    rig.service.store.put('game_feature_schedule',{**schedule,'next_tick':0},id=schedule['id'])
    rig.service._pump_features()
    schedule=rig.service.store.get('game_feature_schedule',sid+':timer')
    op=rig.service.broker.claim('executor',0,operation_id=schedule['last_operation_id'])
    op=rig.service.broker.dispatched(op['id'],'executor',0,op['attempt_id'],op['revision'])
    rig.service.broker.complete(op['id'],'executor',0,op['attempt_id'],op['revision'],status='outcome_unknown',error={'code':'lost_reply'})
    rig.service._pump_features()
    assert rig.service.store.get('game_feature_schedule',sid+':timer')['enabled'] is False
    assert next(f for f in rig.service.broker.features(sid) if f['feature_id']=='timer')['state']=='stopping'
    count=len(rig.service.broker.operations(sid));rig.service._pump_features()
    assert count==len(rig.service.broker.operations(sid))


def test_pointer_discovery_retains_shorter_absolute_path():
    from bughunter.game_runtime.pointers import discover_pointer_paths
    adapter=FakeAdapter();base=adapter.region_base
    adapter.memory[0x100:0x108]=struct.pack('<Q',base+0x400)
    paths=discover_pointer_paths(adapter,target_address=base+0x400,depth=6,offset_window=0)
    assert any(path.root==hex(base+0x100).replace('0x','abs:0x',1) and path.depth==1 for path in paths)


def test_runtime_guard_change_stops_tick_before_write(rig):
    recipe,_=rig.save();sid=rig.session['id']
    guard_recipe,_=rig.save(64,'round guard')
    saved=MappingStore(rig.service.store).get(recipe['id'])
    guard=MappingStore(rig.service.store).get(guard_recipe['id'])
    args={'session_id':'native','task_session_id':sid,'identity':rig.adapter.identity.to_wire(),
          'fixture_module_bases':{'Fake':hex(rig.base)},'recipe':saved.to_contract(),
          'runtime_action':'feature_start','feature_id':'guarded','value':0.,
          'guards':[{'name':'round_identity','recipe':guard.to_contract(),'expected':0.}]}
    rig.runtime.dispatch(args)
    rig.adapter.write_value(rig.base+32,60.,'f32')
    rig.adapter.write_value(rig.base+64,1.,'f32')
    with pytest.raises(RuntimeError,match='runtime guard changed'):
        rig.runtime.dispatch({**args,'runtime_action':'feature_tick'})
    assert struct.unpack('<f',rig.adapter.read_bytes(rig.base+32,4))[0]==60.


def test_duplicate_start_cannot_overwrite_existing_schedule(rig):
    recipe,_=rig.save();sid=rig.session['id']
    first=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.)
    before=len(rig.service.broker.operations(sid))
    with pytest.raises(ValueError,match='previous feature'):
        rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=3.)
    assert len(rig.service.broker.operations(sid))==before
    assert rig.service.store.get('game_feature_schedule',sid+':timer')['last_operation_id']==first['operation_id']


def test_invalidation_stops_scheduling_at_same_revision(rig):
    recipe,_=rig.save();sid=rig.session['id']
    start=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.)
    rig.execute(start)
    MappingStore(rig.service.store).invalidate(recipe['id'],reason='build drift',author='test')
    before=len(rig.service.broker.operations(sid));rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==before
    assert not rig.service.store.get('game_feature_schedule',sid+':timer')['enabled']
    assert next(f for f in rig.service.broker.features(sid) if f['feature_id']=='timer')['state']=='stopping'


def test_native_receipt_survives_stop_revision_race(rig):
    sid=rig.session['id'];b=rig.service.broker
    op=b.enqueue(sid,'write','mutating',{'address':hex(rig.base+32),'vtype':'float','value':7.})
    claimed=b.claim('executor',0,operation_id=op['id'])
    dispatched=b.dispatched(op['id'],'executor',0,claimed['attempt_id'],claimed['revision'])
    b.stop(sid)
    receipt=rig.service.private('gamelab.native.complete',{'credential_hex':'a'*64,'operation_id':op['id'],
        'executor_id':'executor','executor_epoch':0,'attempt_id':dispatched['attempt_id'],
        'revision':dispatched['revision'],'status':'succeeded','result':{'verified':True,'value':7.}})
    assert receipt['operation']['status']=='succeeded'
    assert b.get_session(sid)['state']=='stopping'


def test_feature_refuses_initial_object_changed_since_observation(rig):
    sid=rig.session['id'];base=rig.base
    rig.adapter.memory[0x100:0x108]=struct.pack('<Q',base+32)
    saved=rig.service.mapping_save(sid,name='dynamic object',root='mod:Fake+0x100',
          steps=[{'op':'deref','offset':'0x0','width':8}],value_type='f32')
    pending=rig.service.mapping_revalidate(sid,saved['id']);rig.execute(pending)
    rig.service.mapping_observe(sid,saved['id'],pending['operation_id'])
    rig.adapter.memory[0x100:0x108]=struct.pack('<Q',base+64)
    rig.adapter.write_value(base+64,60.,'f32')
    pending=rig.service.feature_start(sid,recipe_id=saved['id'],feature_id='dynamic',apply_value=0.)
    with pytest.raises(RuntimeError,match='differs from native observation'):rig.execute(pending)
    assert struct.unpack('<f',rig.adapter.read_bytes(base+64,4))[0]==60.


def test_clean_feature_can_start_new_lifecycle_without_replaying_old_one(rig):
    recipe,_=rig.save();sid=rig.session['id'];b=rig.service.broker
    first=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.,disable_value=60.)
    rig.execute(first)
    rig.service.stop(sid,feature_id='timer')
    b.acknowledge_quiescence(sid,'executor',0)
    cleanup=next(b.get_operation(r['operation_id']) for r in b.cleanup_operations(sid))
    b.authorize_cleanup(cleanup['id'],'executor',0,process_instance_id=rig.session['process_instance_id'],target_fingerprint='a'*64,preconditions_passed=True)
    rig.execute({'operation_id':cleanup['id']})
    b.finish_stop(sid,'timer')
    second=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=3.,disable_value=60.)
    assert rig.execute(second)['value']==3.
    old=b.get_operation(first['operation_id'])['arguments']
    with pytest.raises(ValueError,match='obsolete feature lifecycle'):
        rig.runtime.dispatch({**old,'runtime_action':'feature_stop','session_id':'native','task_session_id':sid,'identity':rig.adapter.identity.to_wire()})


def test_stdio_drains_inflight_handlers_before_teardown(monkeypatch):
    import io
    import json
    import threading
    from bughunter.__main__ import stdio
    started=threading.Event();release=threading.Event();eof=threading.Event()
    class App:
        count=0
        closed=False
        lock=threading.Lock()
        def dispatch(self,method,params):
            with self.lock:
                self.count+=1
                if self.count==8:started.set()
            assert release.wait(3)
            assert not self.closed
            return {'ok':True}
        def shutdown(self):self.closed=True
    class Input:
        def __iter__(self):
            for index in range(8):yield json.dumps({'id':index,'method':'test'})+'\n'
            assert started.wait(3)
            for index in range(8,24):yield json.dumps({'id':index,'method':'test'})+'\n'
            eof.set()
    app=App();monkeypatch.setattr('sys.stdin',Input());monkeypatch.setattr('sys.stdout',io.StringIO())
    worker=threading.Thread(target=stdio,args=(app,));worker.start()
    assert eof.wait(3)
    assert not app.closed
    release.set();worker.join(3)
    assert not worker.is_alive() and app.closed


@pytest.mark.skipif(__import__('sys').platform!='darwin',reason='native macOS feature acceptance')
def test_external_native_feature_repeats_and_stops_on_guard_change(tmp_path):
    from bughunter.game_runtime.testing.fixture_target import Fixture
    from bughunter.memory_engine.mac_adapter import MacMemoryAdapter
    with Fixture() as fixture:
        identity=fixture.identity();fp=identity.module_fingerprint
        assert identity.start_time_ns==read_process_start_time_ns(identity.pid)
        adapter=MacMemoryAdapter(identity.pid,identity=ProcessIdentity(pid=identity.pid,start_time_ns=identity.start_time_ns,target_fingerprint=fp))
        server=MemoryEngineServer(ServiceConfig(ownership=ExecutorRegistry()))
        runtime=RuntimeOperations(server)
        try:
            server.attach(adapter,session_id='native')
            mappings=MappingStore(Store(tmp_path/'native-data'))
            def saved(name,offset,type):
                return mappings.save_new(name=name,target_fingerprint=fp,value_type=type,root='mod:region_0+0x0',
                    steps=[{'op':'add','offset':hex(offset),'width':4}]).to_contract()
            recipe=saved('native timer',0x20,'u32');guard=saved('alive guard',0x10,'u8')
            args={'session_id':'native','task_session_id':'task','identity':adapter.identity.to_wire(),
                'recipe':recipe,'fixture_module_bases':{'region_0':hex(identity.region('region_0').base)},
                'feature_id':'native-timer','lifecycle_id':'first','runtime_action':'feature_start','value':0,
                'guards':[{'name':'alive','recipe':guard,'expected':1}]}
            assert runtime.dispatch(args)['value']==0
            for _ in range(3):
                fixture.set('countdown',60)
                assert runtime.dispatch({**args,'runtime_action':'feature_tick'})['value']==0
                assert fixture.get('countdown')==0
            fixture.set('alive_flag',0);fixture.set('countdown',60)
            with pytest.raises(RuntimeError,match='guard changed'):
                runtime.dispatch({**args,'runtime_action':'feature_tick'})
            # Failure must also resume the target, or this independent RPC times out.
            assert fixture.get('countdown')==60
            runtime.dispatch({**args,'runtime_action':'feature_stop'})
        finally:
            runtime.close();server.shutdown()


def test_scheduler_waits_after_receipt_and_slow_tick(rig,monkeypatch):
    recipe,_=rig.save();sid=rig.session['id']
    clock=[100.]
    monkeypatch.setattr('bughunter.game_runtime.service.time.monotonic',lambda:clock[0])
    rig.service._last_poll=100.
    first=rig.service.feature_start(sid,recipe_id=recipe['id'],feature_id='timer',apply_value=0.)
    rig.execute(first)
    count=len(rig.service.broker.operations(sid));rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==count
    clock[0]=100.49;rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==count
    clock[0]=100.5;rig.service._pump_features()
    schedule=rig.service.store.get('game_feature_schedule',sid+':timer')
    assert schedule['last_operation_id']!=first['operation_id']
    clock[0]=105.;rig.execute({'operation_id':schedule['last_operation_id']})
    count=len(rig.service.broker.operations(sid));rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==count
    clock[0]=105.5;rig.service._pump_features()
    assert len(rig.service.broker.operations(sid))==count+1


def test_relocated_bundle_matches_every_measured_input(tmp_path):
    import hashlib
    from bughunter.game_runtime.live_target import verify_running_bundle
    expected=tmp_path/'installed.app';relocated=tmp_path/'relocated.app'
    inputs=[]
    for role,relative,content in [('main','Game',b'game'),('UnityFramework','Frameworks/UnityFramework',b'unity'),
                                 ('metadata','Data/metadata',b'metadata')]:
        for root in (expected,relocated):
            path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        inputs.append({'role':role,'path':str(expected/relative),'sha256':hashlib.sha256(content).hexdigest()})
    measured=verify_running_bundle(relocated/'Game',expected,inputs)
    assert len(measured)==3 and all(Path(row['path']).is_relative_to(relocated) for row in measured)
    (relocated/'Data/metadata').write_bytes(b'changed metadata')
    with pytest.raises(ValueError,match='metadata'):
        verify_running_bundle(relocated/'Game',expected,inputs)
