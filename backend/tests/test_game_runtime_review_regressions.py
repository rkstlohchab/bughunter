"""Regressions from the independent P04-P06 implementation review."""
import importlib.util
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bughunter.game_runtime import scans, among_us
from bughunter.game_runtime.features import FeatureRun
from bughunter.game_runtime.mappings import MappingStore, Observation
from bughunter.game_runtime.pointers import PointerResolveError, resolve_recipe, discover_pointer_paths
from bughunter.game_runtime.migrations import migrate_legacy_mappings, rollback
from bughunter.store import Store
from test_game_runtime_scans_pointers import FakeAdapter
from test_game_runtime_features import _setup_recipe_and_adapter

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'data')


def recipe(ms, **changes):
    return ms.save_new(**dict(name='review recipe', target_fingerprint='a'*64, value_type='u32',
                              root='mod:FakeModule+0x0', steps=[{'op':'add','offset':'0x0','width':4}], **changes))


def test_scan_matches_cross_chunk_boundaries(monkeypatch):
    monkeypatch.setattr(scans, '_CHUNK_BYTES', 32)
    adapter = FakeAdapter(); adapter.region_size = 64
    adapter.memory[30:35] = b'ABCDE'
    scan = scans.ScanSession(adapter,'bytes')
    assert [m.address for m in scan.scan_exact(None,byte_pattern=b'ABCDE')] == [adapter.region_base+30]
    assert scan.progress.bytes_read == 64


def test_scan_short_read_retains_later_chunks_and_marks_partial(monkeypatch):
    monkeypatch.setattr(scans, '_CHUNK_BYTES', 32)
    adapter = FakeAdapter(); adapter.region_size = 64
    original = adapter.read_bytes
    adapter.write_value(adapter.region_base+40,1234,'u32')
    adapter.read_bytes = lambda a,n: original(a,n)[:5] if a == adapter.region_base else original(a,n)
    scan = scans.ScanSession(adapter,'u32')
    assert adapter.region_base+40 in [m.address for m in scan.scan_exact(1234)]
    assert scan.progress.phase == 'partial' and scan.progress.bytes_skipped == 27


def test_new_scan_resets_previous_candidates_and_exact_refine_updates_baseline():
    adapter = FakeAdapter(); adapter.region_size = 128
    base=adapter.region_base
    adapter.write_value(base,1,'u32'); adapter.write_value(base+32,2,'u32')
    scan=scans.ScanSession(adapter,'u32')
    scan.scan_exact(1); scan.scan_exact(2)
    adapter.write_value(base+32,3,'u32')
    assert [m.address for m in scan.refine('exact',needle=3)] == [base+32]
    assert [m.address for m in scan.refine('unchanged')] == [base+32]


def test_unknown_snapshot_is_disk_backed_and_cancel_is_terminal(monkeypatch):
    adapter=FakeAdapter(); adapter.region_size=64
    scan=scans.ScanSession(adapter,'u32')
    try:
        scan.scan_unknown_initial()
        assert scan._value_cache == {} and scan._snapshot_bytes == 64
        scan.refine('unchanged',progress=lambda _: scan.cancel())
        assert scan.progress.phase == 'cancelled'
    finally:
        path=scan._snapshot_dir;scan.close()
        assert not path.exists()


def test_checkpoint_writes_actual_coverage(monkeypatch):
    monkeypatch.setattr(scans,'_CHECKPOINT_SECONDS',0)
    adapter=FakeAdapter();adapter.region_size=64
    scan=scans.ScanSession(adapter,'u32')
    try:
        scan.scan_exact(100)
        checkpoint=json.loads((scan._snapshot_dir/'checkpoint.json').read_text())
        assert checkpoint['progress']['bytes_read']==64
    finally: scan.close()


def test_restart_promotion_rejects_observations_from_previous_revision(store):
    ms=MappingStore(store);r=recipe(ms)
    ms.revise(r.id,name='revised recipe')
    observations=[Observation(str(i),'a'*64,f'{i}:123',1,'now',1) for i in range(3)]
    with pytest.raises(ValueError,match='revision'):
        ms.mark_restart_verified(r.id,observations=observations)
    revised=ms.get(r.id)
    assert revised.history[-1]['recipe']['name']=='review recipe'
    assert revised.verification_status=='candidate'


def test_mapping_concurrent_updates_cannot_silently_overwrite(store):
    ms=MappingStore(store);r=recipe(ms);stale=ms.get(r.id)
    ms.revise(r.id,name='new revision')
    with pytest.raises(ValueError,match='concurrently'):
        ms._put(stale,author='test',reason='stale update')


def test_resolver_rejects_zero_module_null_pointer_and_stale_recipe(store):
    ms=MappingStore(store);r=recipe(ms);adapter=FakeAdapter()
    with pytest.raises(PointerResolveError,match='module base'):
        resolve_recipe(r,adapter=adapter,module_base_lookup=lambda _:0,target_fingerprint='a'*64)
    r=ms.revise(r.id,steps=[{'op':'deref','offset':'0x8','width':8}])
    with pytest.raises(PointerResolveError,match='null pointer'):
        resolve_recipe(r,adapter=adapter,module_base_lookup=lambda _:adapter.region_base,target_fingerprint='a'*64)
    r=ms.invalidate(r.id,author='test',reason='drift')
    with pytest.raises(PointerResolveError,match='stale'):
        resolve_recipe(r,adapter=adapter,module_base_lookup=lambda _:adapter.region_base,target_fingerprint='a'*64)


def test_absolute_root_requires_original_process_and_cannot_be_restart_verified(store):
    ms=MappingStore(store);r=recipe(ms);r=ms.revise(r.id,root='abs:0x100000',preconditions=['process_instance:1:100'])
    adapter=FakeAdapter()
    with pytest.raises(PointerResolveError,match='identity'):
        resolve_recipe(r,adapter=adapter,module_base_lookup=lambda _:1,target_fingerprint='a'*64,process_instance_id='2:200')
    with pytest.raises(ValueError,match='module-relative'):
        ms.mark_restart_verified(r.id,observations=[])


def test_pointer_discovery_reads_large_regions_in_chunks(monkeypatch):
    monkeypatch.setattr(scans,'_CHUNK_BYTES',32)
    adapter=FakeAdapter();adapter.region_size=96
    original=adapter.read_bytes
    def bounded(address,size):
        assert size<=32
        return original(address,size)
    adapter.read_bytes=bounded
    target=adapter.region_base+80
    adapter.write_value(adapter.region_base+40,target,'u64')
    result=discover_pointer_paths(adapter,target_address=target,depth=1,offset_window=0,
                                  module_ranges=[('FakeModule',adapter.region_base,96)])
    assert any(p.root=='mod:FakeModule+0x28' for p in result)
    assert result.complete


def test_pointer_discovery_does_not_fabricate_empty_path():
    adapter=FakeAdapter();adapter.region_size=64
    assert not discover_pointer_paths(adapter,target_address=adapter.region_base+32,depth=1,offset_window=0)


def test_legacy_migration_preserves_add_before_dereference(store):
    adapter=FakeAdapter();base=adapter.region_base
    adapter.write_value(base+8,base+64,'u64');adapter.write_value(base+68,42,'u32')
    store.put('pointer_candidate',{'process_label':'test','label':'field','base':'mod:X+0x0','offsets':[8,4],'value_type':'uint'})
    report=migrate_legacy_mappings(store,target_fingerprint_lookup=lambda _:'a'*64)
    assert not report.errors
    r=MappingStore(store).list_all()[0]
    assert resolve_recipe(r,adapter=adapter,module_base_lookup=lambda _:base,target_fingerprint='a'*64).value==42
    rollback(store,report.backup_path)
    assert MappingStore(store).list_all()==[]


def test_migration_conflict_rolls_back_whole_batch(store):
    store.put('pointer_candidate',{'label':'good','base':'mod:X+0x0','offsets':[0]})
    store.put('pointer_candidate',{'label':'bad','base':'mod:X+0x0','offsets':[0],'pointer.offsets':[8]})
    report=migrate_legacy_mappings(store)
    assert report.errors and not report.migrated_ids
    assert MappingStore(store).list_all()==[]


def test_candidates_reject_constant_zero_nan_and_steady_values(monkeypatch):
    candidates=[among_us.LocalPlayerCandidate(4096,0,'candidate')]
    for values in ([0.0]*6,[20.0]*6,[20.,19.8,float('nan'),19.4,19.2,19.0]):
        monkeypatch.setattr(among_us,'observe_kill_timer_over_time',lambda *a,**k:list(enumerate(values)))
        assert among_us.validate_candidates_read_only(None,candidates)==[]


def test_layout_refuses_an_unmeasured_build(store):
    with pytest.raises(ValueError,match='not qualified'):
        among_us.build_kill_cooldown_recipe(store,target_fingerprint='b'*64)


def test_feature_stop_prevents_subsequent_writes(store):
    adapter,ms,r,ctx,base,instance=_setup_recipe_and_adapter(store)
    run=FeatureRun(r,'no_cooldown',{})
    with pytest.raises(RuntimeError,match='apply not valid'):
        run.apply(ctx,0.)
    run.start(ctx);run.apply(ctx,0.);run.stop(ctx)
    with pytest.raises(RuntimeError,match='apply not valid'):
        run.apply(ctx,7.)


def test_feature_tracks_repeated_timer_resets(store):
    adapter,ms,r,ctx,base,instance=_setup_recipe_and_adapter(store)
    run=FeatureRun(r,'no_cooldown',{});run.start(ctx);run.apply(ctx,0.)
    for _ in range(3):
        adapter.write_value(instance+0xb0,20.,'f32')
        assert run.maintain(ctx)==0.
    run.stop(ctx)
    adapter.write_value(instance+0xb0,20.,'f32')
    with pytest.raises(RuntimeError):run.maintain(ctx)
    assert run.inspect(ctx)==20.


def test_feature_never_restores_into_replacement_player_with_same_value(store):
    adapter,ms,r,ctx,base,instance=_setup_recipe_and_adapter(store)
    run=FeatureRun(r,'manual',{});run.start(ctx);run.apply(ctx,7.,disable_value=20.)
    replacement=base+0x800
    adapter.memory[0x100:0x108]=struct.pack('<Q',replacement)
    adapter.write_value(replacement+0xb0,7.,'f32')
    with pytest.raises(RuntimeError,match='object_changed'):run.stop(ctx)
    assert run.state=='failed' and run.cleanup_status=='fail'
    assert struct.unpack('<f',adapter.read_bytes(replacement+0xb0,4))[0]==7.


def test_feature_reports_cleanup_write_failure(store):
    adapter,ms,r,ctx,base,instance=_setup_recipe_and_adapter(store)
    run=FeatureRun(r,'manual',{});run.start(ctx);run.apply(ctx,7.,disable_value=20.)
    def broken(*args):raise RuntimeError('native helper unavailable')
    adapter.compare_write_value=broken
    with pytest.raises(RuntimeError,match='unavailable'):run.stop(ctx)
    assert run.cleanup_status=='fail' and run.state=='failed'


def load_script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_silent_launcher_obeys_timeout():
    script=load_script('verify_game_profile_restarts')
    start=time.monotonic()
    with pytest.raises(RuntimeError,match='within'):
        script._spawn_launcher([sys.executable,'-c','import time; time.sleep(30)'],timeout=.15)
    assert time.monotonic()-start < 3


@pytest.mark.skipif(sys.platform!='darwin',reason='native macOS memory adapter')
def test_native_cli_fixture_uses_correct_scalar_and_independent_reset(tmp_path):
    env={**os.environ,'BUGHUNTER_DATA_DIR':str(tmp_path/'data'),'BUGHUNTER_FORCE_LLDB':'1'}
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_game_feature.py'),'--fixture',
        '--duration-s','.3','--report-dir',str(tmp_path/'report')],env=env,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    summary=json.loads((tmp_path/'report/summary.json').read_text())
    assert summary['verdict']=='fixture_pass' and not summary['gameplay_verified']
    assert summary['baseline']['min_value']==60
    assert summary['enabled']['max_value']==0
    assert summary['disabled']['min_value']==60


@pytest.mark.skipif(sys.platform!='darwin',reason='native macOS memory adapter')
def test_native_restart_cli_reuses_same_saved_recipe(tmp_path):
    env={**os.environ,'BUGHUNTER_DATA_DIR':str(tmp_path/'data'),'BUGHUNTER_FORCE_LLDB':'1'}
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_game_profile_restarts.py'),'--fixture',
                          '--report-dir',str(tmp_path/'report')],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr+(tmp_path/'report/report.json').read_text()
    report=json.loads((tmp_path/'report/report.json').read_text())
    assert report['verdict']=='restart_verified'
    assert len({(r['pid'],r['start_time_ns']) for r in report['observations']})==3
    assert all(r['value']==60 for r in report['observations'])


@pytest.mark.skipif(sys.platform!='darwin',reason='native macOS debugger state')
def test_persistent_debugger_preserves_operator_paused_target():
    import signal
    from bughunter.game_runtime.testing.fixture_target import Fixture
    from bughunter.memory_engine.mac_adapter import _PersistentLldbSession, _target_stopped
    import shutil
    with Fixture() as target:
        identity=target.identity()
        os.kill(identity.pid,signal.SIGSTOP)
        try:
            end=time.monotonic()+2
            while not _target_stopped(identity.pid) and time.monotonic()<end:time.sleep(.01)
            from bughunter.memory_engine.protocol import MemoryEngineUnavailable
            with pytest.raises(MemoryEngineUnavailable,match='already paused'):
                _PersistentLldbSession(identity.pid,shutil.which('lldb'))
            assert _target_stopped(identity.pid)
        finally:os.kill(identity.pid,signal.SIGCONT)


def test_runtime_status_reports_unimplemented_chat_capabilities(tmp_path):
    from bughunter.app import Application
    app=Application(tmp_path/'app',primary=False)
    try:
        capabilities=app.game_runtime.status()['capabilities']
        assert capabilities['chat_scans'] is False
        assert capabilities['scan_result_values_implemented'] is True
        assert capabilities['chat_no_cooldown_lifecycle'] is False
    finally:app.close()
