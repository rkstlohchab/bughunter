"""A running game is attachable whatever engine built it, not only Unity bundles."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bughunter.game_runtime import targets as T

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process discovery')


@pytest.fixture
def running():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    for _ in range(50):
        if T._proc_pidpath(proc.pid):
            break
        time.sleep(0.05)
    yield proc
    proc.kill()
    proc.wait(timeout=5)


def test_pidpath_resolves_absolute_path_for_any_launch(running):
    path = T._proc_pidpath(running.pid)
    assert path is not None and path.is_absolute() and path.is_file()


def test_non_unity_process_becomes_a_valid_target(running):
    target = T._macos_inspect_process(running.pid, Path(sys.executable))
    assert target is not None
    assert target.platform == 'macos' and target.runtime == 'native process'
    assert target.build_fingerprint.extras['process_id'] == running.pid
    assert target.build_fingerprint.extras['layout'] == 'running_process'
    assert 'running_process' in target.capabilities
    assert len(target.build_fingerprint.sha256_main) == 64


def test_helpers_and_system_binaries_are_filtered():
    assert T._PROC_SKIP_WORDS.search('Code Helper (Plugin)')
    assert T._PROC_SKIP_WORDS.search('MyApp Renderer')
    assert not T._PROC_SKIP_WORDS.search('supertux2')
    assert '/System/Library/x'.startswith(T._PROC_SKIP_PREFIXES)


def test_discovery_dedupes_identical_executables(monkeypatch, tmp_path):
    same = tmp_path / 'fakegame'
    same.write_bytes(b'x')
    monkeypatch.setattr(T, '_proc_pidpath', lambda pid: same)
    monkeypatch.setattr(T.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='1\n2\n3\n', stderr=''))
    found = T._running_macos_processes()
    assert len(found) == 1


def test_installed_bundles_win_over_a_duplicate_running_entry(monkeypatch):
    monkeypatch.setattr(T, '_discover_macos', lambda roots: [])
    captured = []
    monkeypatch.setattr(T, '_discover_running_macos', lambda: captured or [])
    assert T.discover_installed(platform_filter='macos') == []
