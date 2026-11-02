"""User-initiated model import and LoRA utilities (no network or GPU required)."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kaggle_import_requires_single_gguf_and_no_shell_injection(tmp_path, monkeypatch):
    util = load_script('install_kaggle_gguf')
    (tmp_path / 'small.gguf').write_bytes(b'GGUF')
    monkeypatch.setitem(sys.modules, 'kagglehub', types.SimpleNamespace(model_download=lambda _: str(tmp_path)))
    monkeypatch.setattr(util.shutil, 'which', lambda _: '/usr/bin/ollama')
    calls = []
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        assert 'FROM ' + str((tmp_path/'small.gguf').resolve()) in Path(args[-1]).read_text()
    monkeypatch.setattr(util.subprocess, 'run', fake_run)
    util.install('owner/model/framework/variation', 'my-gguf:latest')
    assert calls[0][0][:3] == ['ollama', 'create', 'my-gguf:latest']
    with pytest.raises(ValueError, match='handle'):
        util.install('owner/model; echo BAD/framework/variation', 'test')
    (tmp_path/'second.gguf').write_bytes(b'GGUF')
    with pytest.raises(ValueError, match='specify one'):
        util.install('owner/model/framework/variation', 'test')
    with pytest.raises(ValueError, match='requested'):
        util.install('owner/model/framework/variation', 'test', '../outside.gguf')


def test_train_script_dry_run_and_input_check(tmp_path, monkeypatch, capsys):
    util = load_script('train_mlx_adapter')
    (tmp_path/'manifest.json').write_text(json.dumps({'training_performed':False, 'domain':'burp_suite'}))
    (tmp_path/'train.jsonl').write_text(json.dumps({'messages':[{'role':'user','content':'hello'},{'role':'assistant','content':'world'}]})+'\n')
    util.train(tmp_path, 'mlx-compatible', tmp_path/'out', 12, dry_run=True)
    assert 'mlx_lm.lora' in capsys.readouterr().out
    with pytest.raises(ValueError, match='Iteration'):
        util.train(tmp_path, 'mlx-compatible', tmp_path/'out', 0, dry_run=True)
