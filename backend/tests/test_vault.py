"""Vault: put/get/substitute/delete, no secrets in entity DB."""
import json
import pytest
from bughunter.app import Application


@pytest.fixture
def app(tmp_path, monkeypatch):
    # provide a hermetic fake keyring that does NOT touch the real Keychain
    class FakeKeyring:
        _store = {}
        @classmethod
        def set_password(cls, service, name, value):
            cls._store[(service, name)] = value
        @classmethod
        def get_password(cls, service, name):
            return cls._store.get((service, name))
        @classmethod
        def delete_password(cls, service, name):
            cls._store.pop((service, name), None)
    import bughunter.providers as providers
    monkeypatch.setattr(providers, "keyring", FakeKeyring)
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def test_put_and_substitute(app):
    app.vault.put(name="burp.key", secret="hunter2", kind="api_key")
    rec = app.vault.get_meta(name="burp.key")
    assert rec["name"] == "burp.key"
    assert rec["kind"] == "api_key"
    out = app.vault.substitute("hello {{VAULT:burp.key}}!")
    assert out == "hello hunter2!"


def test_vault_entity_contains_no_secret(app):
    app.vault.put(name="t.one", secret="my-secret-value", kind="token")
    for entry in app.store.list("vault_entry"):
        blob = json.dumps(entry)
        assert "my-secret-value" not in blob


def test_delete_removes_entry(app):
    app.vault.put(name="to.del", secret="x", kind="token")
    assert app.vault.get_meta(name="to.del") is not None
    app.vault.delete(name="to.del")
    assert app.vault.get_meta(name="to.del") is None


def test_refs_in(app):
    out = app.vault.refs_in(text="use {{VAULT:a}} and {{VAULT:b}} and {{VAULT:a}}")
    assert out == ["a", "b"]
