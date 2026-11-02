"""Cheat Engine's Windows executable in a standard CrossOver bottle is discoverable,
while the unrelated built-in macOS memory bridge does not imply CE is installed."""
from pathlib import Path

from bughunter import tool_readiness


def test_crossover_detected(tmp_path, monkeypatch):
    ce_exe = (tmp_path / 'Library' / 'Application Support' / 'CrossOver' / 'Bottles'
              / 'Test Bottle' / 'drive_c' / 'Program Files' / 'Cheat Engine 7.5'
              / 'Cheat Engine.exe')
    ce_exe.parent.mkdir(parents=True)
    ce_exe.write_bytes(b'MZ')
    monkeypatch.setattr(tool_readiness.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(tool_readiness.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(tool_readiness, '_mac_candidates', lambda: iter(()))
    monkeypatch.setattr(tool_readiness, '_spotlight_candidates', lambda: iter(()))
    monkeypatch.setattr(tool_readiness.shutil, 'which', lambda _: None)
    installed, proof = tool_readiness._found('Cheat Engine')
    assert installed['location'] == str(ce_exe)
    assert installed['runtime'].startswith('Windows')
    assert proof == 'detected in CrossOver bottle'
    burp, _ = tool_readiness._found('Burp Suite')
    assert burp is None
