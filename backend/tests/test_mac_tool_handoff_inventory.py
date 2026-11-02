"""Native app handoff needs trustworthy *installation* evidence.

A GUI running/proxy connection must never be inferred from a filename.
"""
import plistlib

import pytest
from bughunter import tool_readiness


def test_lookalike_burp_folder_is_not_a_valid_application(tmp_path):
    fake = tmp_path / 'Burp Suite.app'
    fake.mkdir()
    with pytest.raises(ValueError, match='not a recognized'):
        tool_readiness.validate_tool_path('Burp Suite', str(fake))


def test_manual_windows_ce_exe_is_not_a_native_mac_app(tmp_path):
    executable = tmp_path / 'Cheat Engine.exe'
    executable.write_bytes(b'MZ')
    selected = tool_readiness.validate_tool_path('Cheat Engine', str(executable))
    assert selected['runtime'] == 'Windows executable (Wine/CrossOver required)'
    assert selected['location'] == str(executable)


def test_native_burp_is_detected_but_not_described_as_connected(monkeypatch, tmp_path):
    path = tmp_path / 'Burp Suite Professional.app'
    contents = path / 'Contents'
    contents.mkdir(parents=True)
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleIdentifier':'net.portswigger.BurpSuitePro','CFBundleName':'Burp Suite Professional'}, f)
    monkeypatch.setattr(tool_readiness, '_found', lambda name, paths=None:
                        (tool_readiness.validate_tool_path(name, str(path)), 'selected manually')
                        if name == 'Burp Suite' else (None, ''))
    result = next(t for t in tool_readiness.inventory() if t['name']=='Burp Suite')
    assert result['installed'] is True
    assert result['native_ready'] is True
    assert result['verified'] is False
    assert result['status'] == 'detected'
    assert 'active connection not tested' in result['detail']


def test_unrecognized_tool_cannot_be_located(tmp_path):
    program = tmp_path / 'bash'
    program.write_text('#!/bin/sh\n')
    with pytest.raises(ValueError):
        tool_readiness.validate_tool_path('Terminal', str(program))
