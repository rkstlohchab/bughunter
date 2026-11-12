"""AdapterRegistry: register, activate, import_from, delete."""
import json
import zipfile
from pathlib import Path
import pytest

from bughunter.app import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path, primary=False)
    yield application
    application.shutdown()


def _fake_adapter(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "adapter_model.safetensors").write_bytes(b"\x00" * 128)
    (path / "adapter_config.json").write_text(json.dumps(
        {"base_model_name_or_path": "qwen2.5", "r": 16}))


def test_register_and_activate(app, tmp_path):
    src = tmp_path / "scratch_adapter"
    _fake_adapter(src)
    rec = app.adapters_registry.register(
        name="web-v1", domain="web-audit",
        base_model="Qwen/Qwen2.5-Coder-7B-Instruct",
        path=str(src), rank=16, alpha=32,
    )
    assert rec["status"] == "staged"
    assert Path(rec["path"]).exists()
    # activation
    app.adapters_registry.set_active([rec["id"]])
    active = app.adapters_registry.active()
    assert [a["id"] for a in active] == [rec["id"]]


def test_import_zip(app, tmp_path):
    src = tmp_path / "scratch_adapter"
    _fake_adapter(src)
    archive = tmp_path / "adapter.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in src.rglob("*"):
            z.write(f, f.relative_to(src.parent))
    rec = app.adapters_registry.import_from(
        archive=str(archive), name="imported", domain="codebase",
        base_model="Qwen/Qwen2.5-Coder-7B-Instruct",
    )
    assert rec["status"] == "staged"
    assert Path(rec["path"]).exists()


def test_delete_removes_files(app, tmp_path):
    src = tmp_path / "scratch_adapter"
    _fake_adapter(src)
    rec = app.adapters_registry.register(
        name="x", domain="web-audit", base_model="b", path=str(src))
    stored = Path(rec["path"])
    assert stored.exists()
    app.adapters_registry.delete(rec["id"])
    assert not stored.exists()
    assert app.adapters_registry.get(rec["id"]) is None
