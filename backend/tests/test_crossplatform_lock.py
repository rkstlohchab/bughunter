"""Desktop lease stays exclusive without hard Unix import assumptions."""
import sys
import types

import pytest


def test_exclusive_desktop_lock(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'keyring', types.SimpleNamespace(get_password=lambda *_: None))
    from bughunter.app import Application
    first = Application(tmp_path, primary=True)
    try:
        with pytest.raises(ValueError, match='already open'):
            Application(tmp_path, primary=True)
        first.store.require_desktop_owner()
    finally:
        first.knowledge.shutdown()
        first._owner_file.close()
    with pytest.raises(ValueError, match='owner has exited'):
        first.store.require_desktop_owner()
