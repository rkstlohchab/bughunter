from pathlib import Path

from bughunter import tool_readiness


def test_generic_burp_command_is_not_burp_suite(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_readiness.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(tool_readiness.shutil, 'which',
                        lambda cmd: '/tmp/burp' if cmd == 'burp' else None)
    assert not next(item for item in tool_readiness.inventory() if item['name'] == 'Burp Suite')['installed']
