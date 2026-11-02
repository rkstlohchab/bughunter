"""CrucibleLLM (on-device iPad/iPhone) provider wiring.

CrucibleLLM is an OpenAI-compatible server reached over the local network by
plain HTTP. These cover the guard that keeps that endpoint on-device and the
settings validation; the prompt-based tool protocol it relies on is covered in
test_providers_local.py.
"""
import pytest

import bughunter.providers as providers
from bughunter.providers import LOCAL_PROVIDERS, ProviderSettings, is_local_network_host, probe_provider
from bughunter.store import Store


@pytest.fixture
def settings(tmp_path):
    return ProviderSettings(Store(tmp_path))


def test_crucible_is_a_local_provider():
    assert "crucible" in LOCAL_PROVIDERS


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "192.168.1.50", "10.0.0.8", "172.16.3.4", "169.254.1.2", "ipad.local"])
def test_local_network_hosts_accepted(host):
    assert is_local_network_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "api.openai.com", "ollama.com", "", "1.1.1.1"])
def test_public_or_empty_hosts_rejected(host):
    assert is_local_network_host(host) is False


def test_save_accepts_lan_http_and_defaults_model_to_local(settings):
    saved = settings.save({"provider": "crucible", "model": "", "api_base_url": "http://192.168.1.50:8080"})
    assert saved["provider"] == "crucible"
    assert saved["model"] == "local"


def test_save_rejects_public_crucible_endpoint(settings):
    with pytest.raises(ValueError, match="local-network address"):
        settings.save({"provider": "crucible", "api_base_url": "http://example.com:8080"})
    # Even HTTPS to a public host is rejected: on-device must stay on-device.
    with pytest.raises(ValueError, match="local-network address"):
        settings.save({"provider": "crucible", "api_base_url": "https://example.com"})


def test_crucible_rejects_application_managed_key(settings):
    with pytest.raises(ValueError, match="does not use an application-managed API key"):
        settings.secret("crucible", "sk-should-not-be-stored")


def test_non_crucible_remote_http_still_requires_https(settings):
    # The relaxed rule is scoped to crucible; other providers keep the HTTPS guard.
    with pytest.raises(ValueError, match="HTTPS"):
        settings.save({"provider": "openai_compatible", "api_base_url": "http://10.0.0.5:1234/v1"})


# --- probe_provider: readiness check (no billed calls; local = free reachability) ---

def test_probe_crucible_requires_an_address():
    result = probe_provider({"provider": "crucible", "model": "local", "api_base_url": ""})
    assert result["ok"] is False
    assert "address" in result["detail"]


def test_probe_crucible_rejects_public_host_without_network():
    result = probe_provider({"provider": "crucible", "model": "local", "api_base_url": "http://example.com:8080"})
    assert result["ok"] is False
    assert "local-network" in result["detail"]


def test_probe_hosted_reports_missing_config_and_never_bills(monkeypatch):
    monkeypatch.setattr(providers.keyring, "get_password", lambda *a, **k: None)
    result = probe_provider({"provider": "openai", "model": "", "api_base_url": "", "pricing_confirmed": False})
    assert result["ok"] is False
    assert result["checked"] == "configuration"
    for token in ("API key", "model id", "confirmed pricing"):
        assert token in result["detail"]
    # OpenAI has a known default endpoint; a custom self-hosted endpoint does not.
    custom = probe_provider({"provider":"openai_compatible", "model":"", "api_base_url":"", "pricing_confirmed":False})
    assert "base URL" in custom["detail"]
    assert "confirmed pricing" not in custom["detail"]


def test_probe_hosted_complete_config_is_ok_without_a_billed_call(monkeypatch):
    monkeypatch.setattr(providers.keyring, "get_password", lambda *a, **k: "sk-test")
    result = probe_provider({"provider": "anthropic", "model": "claude-x", "pricing_confirmed": True})
    assert result["ok"] is True
    assert result["checked"] == "configuration"
