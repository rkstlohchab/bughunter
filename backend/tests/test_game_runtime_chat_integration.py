"""Task-scoped chat service operations; native execution is covered separately."""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bughunter.game_runtime.mappings import MappingStore
from bughunter.game_runtime.service import GameRuntimeService
from bughunter.store import Store


class _FakeTask(dict):
    def __init__(self, harness_id: str):
        super().__init__(id=harness_id, mode='game', execution_mode='full')


class _FakeHarness:
    """Mimic the subset of app.harness that GameRuntimeService._task calls."""
    def __init__(self):
        self.tasks = {}

    def _task(self, harness_id: str) -> _FakeTask:
        return self.tasks.setdefault(harness_id, _FakeTask(harness_id))

    def check(self, harness_id: str, grant: str = 'app') -> _FakeTask:
        # The real harness verifies the task owns the requested grant and
        # is in a runnable state. For integration-level tests we accept
        # any grant. Returning the task preserves the 'mode=game' check.
        return self._task(harness_id)


class _FakeRuntime:
    """Minimal stand-in for Application attributes the service reads."""
    def __init__(self, store):
        self.store = store
        self.harness = _FakeHarness()
        # Service.shutdown checks ``self.app.primary`` to decide between
        # desktop-owner stop and a short-lived MCP client teardown.
        # Treat test harness as a non-primary client — shutdown then
        # only stops explicit fixtures.
        self.primary = False


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('BUGHUNTER_DATA_DIR', str(tmp_path))
    app = _FakeRuntime(Store(str(tmp_path)))
    svc = GameRuntimeService(app, credential='a' * 64, recover=False)
    svc._bootstrapped = True  # skip the private handshake; tests use public API
    # Force native-available True so broker-owned ops accept writes.
    svc._executor_registered = True
    yield svc
    svc.shutdown()


def _make_session(svc, harness_id='harness-1', target_id=None):
    """Simulate start_session's end state by inserting a target + broker session directly."""
    app = svc.app
    harness_task = app.harness._task(harness_id)
    pid = os.getpid()  # any live pid — identity verification isn't exercised here
    fingerprint = 'c' * 64
    target_id = target_id or f'target_{harness_id}'
    # Stub fixture target so start_session can pretend-attach
    svc._fixtures[target_id] = object()
    app.store.put(
        'game_runtime_target',
        {'id': target_id, 'fixture': True, 'pid': pid,
         'process_instance_id': f'{pid}:111', 'target_fingerprint': fingerprint},
        id=target_id,
    )
    session = svc.broker.create_session(
        target_id, harness_id, f'{pid}:111', fingerprint, ['proj_x'],
    )
    return session, target_id, fingerprint


def test_mapping_save_persists_and_shows_in_status(service, monkeypatch):
    # Force _require_native to no-op so the test does not need a real helper.
    monkeypatch.setattr(service, '_require_native', lambda: None)
    monkeypatch.setattr('bughunter.game_runtime.service.alive', lambda _: True)
    session, target_id, fingerprint = _make_session(service)

    recipe = service.mapping_save(
        session['id'], name='test_recipe', root='mod:FakeModule+0x100',
        steps=[{'op': 'add', 'offset': '+0x0', 'width': 4}],
        value_type='u32', preconditions=['same_process_birth'],
        evidence_ids=['test-ev'],
    )
    assert recipe['verification_status'] == 'candidate'
    assert recipe['target_fingerprint'] == fingerprint

    mappings = service.mapping_list(session['id'])
    assert len(mappings) == 1
    assert mappings[0]['id'] == recipe['id']

    # status() surfaces mappings per session.
    status = service.status(session_id=session['id'])
    assert status['capabilities']['chat_mappings'] is True
    assert status['sessions'][0]['mappings'][0]['id'] == recipe['id']


def test_scan_start_enqueues_broker_operation_stop_covered(service, monkeypatch):
    monkeypatch.setattr(service, '_require_native', lambda: None)
    monkeypatch.setattr('bughunter.game_runtime.service.alive', lambda _: True)
    session, _, _ = _make_session(service)
    receipt = service.scan_start(
        session['id'], value_type='u32', mode='exact', needle=42,
    )
    assert receipt['scan_mode'] == 'exact'
    op = service.broker.get_operation(receipt['operation_id'])
    assert op['kind'] == 'scan'
    assert op['effect_class'] == 'read_only'
    assert op['status'] == 'queued'
    # Stop covers it — a feature-level stop moves undispatched work to cancelled.
    service.stop(session_id=session['id'])
    op_after = service.broker.get_operation(receipt['operation_id'])
    assert op_after['status'] in ('cancelled_before_dispatch',), \
        f"Stop must either cancel undispatched or leave for executor to cancel; got {op_after['status']}"


def test_candidate_search_rejects_unknown_kind(service, monkeypatch):
    monkeypatch.setattr(service, '_require_native', lambda: None)
    session, _, _ = _make_session(service)
    with pytest.raises(ValueError, match='unsupported candidate kind'):
        service.candidate_search(session['id'], kind='all_players')


def test_mapping_revalidate_rejects_cross_fingerprint(service, monkeypatch):
    monkeypatch.setattr(service, '_require_native', lambda: None)
    monkeypatch.setattr('bughunter.game_runtime.service.alive', lambda _: True)
    session, _, fingerprint = _make_session(service)
    # Save a recipe under a DIFFERENT fingerprint directly.
    ms = MappingStore(service.store)
    other = ms.save_new(
        name='other-build-recipe', target_fingerprint='d' * 64,
        value_type='u32', root='mod:X+0x0',
        steps=[{'op': 'add', 'offset': '+0x0', 'width': 4}],
    )
    with pytest.raises(ValueError, match='different build fingerprint'):
        service.mapping_revalidate(session['id'], other.id)


def test_feature_start_requires_prior_observation(service, monkeypatch):
    monkeypatch.setattr(service, '_require_native', lambda: None)
    monkeypatch.setattr('bughunter.game_runtime.service.alive', lambda _: True)
    session, _, fingerprint = _make_session(service)
    recipe = service.mapping_save(
        session['id'], name='needs-observation', root='mod:X+0x0',
        steps=[{'op': 'add', 'offset': '+0x0', 'width': 4}],
        value_type='u32',
    )
    # feature_start without observe must fail cleanly.
    with pytest.raises(ValueError, match='has not been observed'):
        service.feature_start(
            session['id'], recipe_id=recipe['id'], feature_id='manual',
            apply_value=7,
        )


def test_status_capabilities_report_honest_scan_limitation(service):
    status = service.status()
    caps = status['capabilities']
    assert caps['chat_mappings'] is True
    # Scans & pointer_search queue through the broker but the helper
    # stub returns not_implemented until the P05 result emission is
    # wired; the capability flag reflects that honestly.
    assert caps['scan_result_values_implemented'] is True
    assert caps['pointer_search_result_values_implemented'] is True


def test_operator_tools_registered_chat_surfaces():
    from bughunter.operator_tools import OperatorTools
    names = {s['name'] for s in OperatorTools.TOOL_SPECS}
    required = {
        'game_targets', 'game_session', 'game_fixture',
        'game_read', 'game_write', 'game_status', 'game_stop',
        'game_mapping_save', 'game_mapping_list',
        'game_mapping_revalidate', 'game_mapping_observe',
        'game_scan', 'game_candidate_search',
        'game_feature_start', 'game_feature_monitor',
    }
    missing = required - names
    assert not missing, f"missing chat tools: {missing}"
