import concurrent.futures
import pytest
from bughunter.store import Store
from bughunter.game_runtime.broker import GameRuntimeBroker, BrokerError

@pytest.fixture
def setup(tmp_path):
    clock=[1000.]
    b=GameRuntimeBroker(Store(tmp_path),clock=lambda:clock[0])
    s=b.create_session('target','harness','100:200','fingerprint')
    e=b.register_executor('executor','host','101:201','100:200','fingerprint')
    return b,s,e,clock

def queue(b,s,kind='write'):
    return b.enqueue(s['id'],kind,'mutating' if kind=='write' else 'read_only',{'address':'0x1000','vtype':'uint','value':7})

def take(b,e,op):
    c=b.claim(e['executor_id'],e['executor_epoch'],operation_id=op['id'])
    return b.dispatched(c['id'],e['executor_id'],e['executor_epoch'],c['attempt_id'],c['revision'])

def complete(b,op,**kw):
    return b.complete(op['id'],op['executor_id'],op['executor_epoch'],op['attempt_id'],op['revision'],**kw)

def test_racing_claims_one_winner(setup):
    b,s,e,_=setup;queue(b,s)
    def run(_):return GameRuntimeBroker(b.store,clock=b.clock).claim(e['executor_id'],e['executor_epoch'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:claims=list(pool.map(run,range(8)))
    assert sum(c is not None for c in claims)==1

def test_dispatch_and_receipt_bind_attempt_revision_epoch(setup):
    b,s,e,_=setup;queue(b,s);c=b.claim('executor',0)
    for epoch,attempt,revision in [(1,c['attempt_id'],c['revision']),(0,'forged',c['revision']),(0,c['attempt_id'],0)]:
        with pytest.raises(BrokerError):b.dispatched(c['id'],'executor',epoch,attempt,revision)
    d=b.dispatched(c['id'],'executor',0,c['attempt_id'],c['revision'])
    kwargs={'status':'succeeded','result':{'verified':True}}
    first=complete(b,d,**kwargs);assert complete(b,d,**kwargs)==first
    with pytest.raises(BrokerError):complete(b,d,status='succeeded',result={'verified':False})
    assert any(r['type']=='game_runtime.conflicting_receipt' for r in b.store.events())

def test_stop_before_dispatch_requires_claimed_acknowledgement(setup):
    b,s,e,_=setup;op=queue(b,s);c=b.claim('executor',0);queued=queue(b,s,'read')
    b.stop(s['id']);assert b.get_operation(queued['id'])['status']=='cancelled_before_dispatch'
    with pytest.raises(BrokerError):b.dispatched(c['id'],'executor',0,c['attempt_id'],c['revision'])
    assert b.get_operation(op['id'])['status']=='claimed'
    b.acknowledge_quiescence(s['id'],'executor',0);b.finish_stop(s['id'])
    assert b.get_operation(op['id'])['status']=='cancelled_before_dispatch'
    with pytest.raises(BrokerError):queue(b,s)

def test_stop_during_write_records_result_without_reviving(setup):
    b,s,e,_=setup;d=take(b,e,queue(b,s));b.stop(s['id'])
    with pytest.raises(BrokerError):complete(b,d,status='succeeded',result={'verified':True})
    done=complete(b,b.get_operation(d['id']),status='succeeded',result={'verified':True})
    assert done['status']=='succeeded' and b.get_session(s['id'])['state']=='stopping'
    b.acknowledge_stop(s['id'],'executor',0);assert b.get_session(s['id'])['state']=='stopped'

def test_crash_after_dispatch_unknown_never_replayed(setup):
    b,s,e,clock=setup;d=take(b,e,queue(b,s));clock[0]+=6
    b=GameRuntimeBroker(b.store,clock=b.clock);b.recover('executor',0)
    assert b.get_operation(d['id'])['status']=='outcome_unknown'
    with pytest.raises(BrokerError):b.register_executor('other','host','102:202','100:200','fingerprint')
    b.recover('executor',0,helper_death_confirmed=True,death_evidence={'waited':True})
    assert b.get_session(s['id'])['state']=='interrupted'
    new=b.register_executor('executor','host','102:202','100:200','fingerprint')
    assert new['executor_epoch']==1 and b.claim('executor',1) is None
    with pytest.raises(BrokerError):complete(b,d,status='succeeded',result={})

def test_mutator_owner_shared_reader_and_dead_helper(setup):
    b,s,e,_=setup;queue(b,s);second=b.create_session('target','harness2','100:200','fingerprint')
    with pytest.raises(BrokerError,match='owns mutation'):queue(b,second)
    read=queue(b,second,'read');b.recover('executor',0,helper_death_confirmed=True)
    assert b.get_session(second['id'])['state']=='interrupted'
    assert b.get_operation(read['id'])['status']=='cancelled_before_dispatch'

def test_declared_cleanup_after_quiescence_and_once(setup):
    b,s,e,_=setup;parent=b.enqueue(s['id'],'write','mutating',{'value':7},feature_id='sample')
    cleanup=b.declare_cleanup(parent['id'],kind='write',effect_class='mutating',arguments={'value':60},preconditions=['value=7'],feature_id='sample')
    complete(b,take(b,e,parent),status='succeeded',result={'verified':True});assert b.claim('executor',0) is None
    b.stop(s['id'])
    with pytest.raises(BrokerError):b.authorize_cleanup(cleanup['id'],'executor',0,process_instance_id='100:200',target_fingerprint='fingerprint',preconditions_passed=True)
    b.acknowledge_quiescence(s['id'],'executor',0)
    b.authorize_cleanup(cleanup['id'],'executor',0,process_instance_id='100:200',target_fingerprint='fingerprint',preconditions_passed=True)
    complete(b,take(b,e,cleanup),status='succeeded',result={'verified':True});b.finish_stop(s['id'])
    assert b.features(s['id'])[0]['cleanup_status']=='pass' and b.claim('executor',0) is None

def test_cleanup_timeout_failed(setup):
    b,s,e,clock=setup;parent=b.enqueue(s['id'],'write','mutating',{},feature_id='f')
    c=b.declare_cleanup(parent['id'],kind='write',effect_class='mutating',arguments={},preconditions=['same'],feature_id='f')
    b.stop(s['id']);b.acknowledge_quiescence(s['id'],'executor',0);clock[0]+=3;b.finish_stop(s['id'])
    assert b.get_operation(c['id'])['status']=='cancelled_before_dispatch'
    assert b.features(s['id'])[0]['cleanup_status']=='fail'

def test_feature_stop_preserves_shared_reader_and_other_features(setup):
    b,s,e,_=setup
    other=b.create_session('target','reader','100:200','fingerprint')
    mutation=b.enqueue(s['id'],'write','mutating',{},feature_id='writer')
    complete(b,take(b,e,mutation),status='succeeded',result={'verified':True})
    same=b.enqueue(s['id'],'read','read_only',{},feature_id='observer')
    shared=queue(b,other,'read')
    b.stop(s['id'],'writer');b.acknowledge_quiescence(s['id'],'executor',0);b.finish_stop(s['id'],'writer')
    assert b.get_session(s['id'])['state']==b.get_session(other['id'])['state']=='running'
    complete(b,take(b,e,same),status='succeeded',result={'value':7})
    assert take(b,e,shared)['status']=='dispatched'
    with pytest.raises(BrokerError):b.enqueue(s['id'],'write','mutating',{},feature_id='writer')

def test_atomic_cleanup_and_repeated_stop_keep_original_window(setup):
    b,s,e,clock=setup
    op=b.enqueue_with_cleanup(s['id'],{'value':7},{'value':60},'f')
    assert len(b.cleanup_operations(s['id']))==1
    complete(b,take(b,e,op),status='succeeded',result={'verified':True})
    b.stop(s['id']);generation=b.get_session(s['id'])['stop_generation'];clock[0]+=.5
    b.stop(s['id']);assert b.get_session(s['id'])['stop_generation']==generation
    cleanup=b.cleanup_operations(s['id'])[0]['operation_id']
    assert b.get_operation(cleanup)['status']=='queued'

def test_global_stop_blocks_old_work_but_new_session_uses_new_generation(setup):
    b,s,e,_=setup
    queue(b,s);b.stop()
    new=b.create_session('target','new-task','100:200','fingerprint')
    read=queue(b,new,'read')
    assert take(b,e,read)['status']=='dispatched'
    assert all(op['status']=='cancelled_before_dispatch' for op in b.operations(s['id']))

@pytest.mark.parametrize('identity,fingerprint', [('100:999','fingerprint'),('100:200','other-build')])
def test_cleanup_never_rebinds_to_changed_target(setup,identity,fingerprint):
    b,s,e,_=setup
    parent=b.enqueue_with_cleanup(s['id'],{'value':7},{'value':60},'f')
    complete(b,take(b,e,parent),status='succeeded',result={'verified':True})
    b.stop(s['id']);b.acknowledge_quiescence(s['id'],'executor',0)
    cleanup=b.cleanup_operations(s['id'])[0]['operation_id']
    with pytest.raises(BrokerError):b.authorize_cleanup(cleanup,'executor',0,process_instance_id=identity,target_fingerprint=fingerprint,preconditions_passed=True)
    assert b.claim('executor',0) is None
    b.finish_stop(s['id'])
    assert b.features(s['id'])[0]['cleanup_status']=='fail'
