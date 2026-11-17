import plistlib
from pathlib import Path

from bughunter import tool_readiness


def test_generic_burp_command_is_not_burp_suite(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_readiness.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(tool_readiness.shutil, 'which',
                        lambda cmd: '/tmp/burp' if cmd == 'burp' else None)
    # Search only an empty directory, so the result does not depend on what this host has installed.
    monkeypatch.setattr(tool_readiness, '_mac_candidates', lambda: iter([]))
    monkeypatch.setattr(tool_readiness, '_spotlight_candidates', lambda: iter([]))
    assert not next(item for item in tool_readiness.inventory() if item['name'] == 'Burp Suite')['installed']
    # A real bundle in the same search is still detected.
    bundle = tmp_path / 'Burp Suite Professional.app'
    (bundle / 'Contents' / 'MacOS').mkdir(parents=True)
    (bundle / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(
        {'CFBundleIdentifier': 'com.portswigger.burp.pro', 'CFBundleName': 'Burp Suite Professional'}))
    monkeypatch.setattr(tool_readiness, '_mac_candidates', lambda: iter([bundle]))
    assert next(item for item in tool_readiness.inventory() if item['name'] == 'Burp Suite')['installed']
