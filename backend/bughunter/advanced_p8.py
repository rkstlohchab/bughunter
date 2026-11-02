"""P8 advanced capability workbench: reviewed MLX training and web evidence coverage.

Training is an explicit desktop-only operation with a fixed subprocess argument
list. It never trains from raw downloaded pages or automatically selects a
model. The web report performs no network requests or transaction attempts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from .store import new_id, now

MAX_ZIP = 40_000_000
MAX_LINES = 6000
MODEL_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def read_dataset(zip_path: Path):
    """Validate a provenance-checked P2 dataset without extracting its archive."""
    if not zip_path.is_file() or zip_path.stat().st_size > MAX_ZIP:
        raise ValueError('Select an exported P2 dataset ZIP under 40 MB.')
    with zipfile.ZipFile(zip_path) as archive:
        items = set(archive.namelist())
        if not {'manifest.json', 'train.jsonl', 'evaluation.jsonl', 'provenance.jsonl'} <= items:
            raise ValueError('Dataset must contain P2 manifest, train, evaluation, and provenance files.')
        if any(x.file_size > MAX_ZIP or x.compress_size > MAX_ZIP for x in archive.infolist()):
            raise ValueError('Dataset contains an oversized member.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema') != 'bughunter.p2.dataset.v1' or manifest.get('training_started') is not False or manifest.get('weight_changes') is not False:
            raise ValueError('Unsupported dataset provenance manifest.')
        rows = {}
        for name in ('train', 'evaluation'):
            content = archive.read(name + '.jsonl')
            if len(content) > MAX_ZIP:
                raise ValueError('Dataset split exceeds its size limit.')
            entries = [json.loads(line) for line in content.splitlines() if line.strip()]
            if len(entries) > MAX_LINES or not entries:
                raise ValueError('Both reviewed training and independent evaluation examples are required.')
            for example in entries:
                messages = example.get('messages', [])
                if not isinstance(messages, list) or len(messages) != 2 or [m.get('role') for m in messages] != ['user', 'assistant']:
                    raise ValueError('Invalid two-message training example.')
                if any(not isinstance(m.get('content'), str) or not m['content'].strip() for m in messages):
                    raise ValueError('Empty or invalid model training message.')
            rows[name] = entries
        if (manifest.get('counts', {}).get('train') != len(rows['train']) or
                manifest.get('counts', {}).get('evaluation') != len(rows['evaluation']) or
                manifest.get('holdout_present') is not True):
            raise ValueError('Manifest counts or independent evaluation do not match dataset.')
        provenance = [json.loads(x) for x in archive.read('provenance.jsonl').splitlines() if x.strip()]
        if len(provenance) != len(rows['train']) + len(rows['evaluation']):
            raise ValueError('Dataset provenance count does not match its examples.')
        # Reject source-family overlap across train/evaluation even if a ZIP was edited.
        source_by_split = {'train': set(), 'evaluation': set()}
        for row in provenance:
            if row.get('split') not in source_by_split or not row.get('source'):
                raise ValueError('Invalid source provenance.')
            source_by_split[row['split']].add(str(row['source']))
        if source_by_split['train'] & source_by_split['evaluation']:
            raise ValueError('Train/evaluation source overlap detected.')
        return manifest, rows


def find_mlx_python():
    """Find a real MLX-capable interpreter, including in PyInstaller sidecars.

    The packaged backend's sys.executable is the frozen sidecar, not Python.
    """
    candidates = []
    if not getattr(sys, 'frozen', False):
        candidates.append(sys.executable)
    for name in ('python3', 'python3.12', 'python'):
        value = shutil.which(name)
        if value:
            candidates.append(value)
    for interpreter in dict.fromkeys(candidates):
        try:
            result = subprocess.run([interpreter, '-c', 'import mlx_lm'],
                                    capture_output=True, timeout=6, check=False)
            if result.returncode == 0:
                return interpreter
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def valid_model(model: str):
    if not isinstance(model, str) or not model.strip() or len(model) > 240:
        raise ValueError('Specify an MLX-compatible Hugging Face model or local model directory.')
    model = model.strip()
    if MODEL_REPO.fullmatch(model):
        return model
    path = Path(model).expanduser()
    if not path.is_absolute() or not path.is_dir() or not (path / 'config.json').is_file():
        raise ValueError('Local model paths must exist and contain config.json, or use a Hugging Face org/model identifier.')
    return str(path.resolve())


class AdvancedP8:
    def __init__(self, app, recover=True):
        self.app = app
        self.store = app.store
        self.lock = threading.RLock()
        self.processes = {}
        if recover:
            for row in self.store.list('p8_training_run'):
                if row.get('status') in {'running', 'queued', 'stopping'}:
                    self.store.put('p8_training_run', {**row, 'status': 'interrupted', 'finished_at': now(),
                        'detail': 'Backend restarted; model training was not confirmed completed.'}, id=row['id'])

    def datasets(self):
        root = (self.store.root / 'exports').resolve()
        result = []
        for path in sorted(root.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            try:
                manifest, rows = read_dataset(path)
            except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                continue
            result.append({'filename': path.name, 'train': len(rows['train']),
                           'evaluation': len(rows['evaluation']), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'project_id': manifest.get('project_id')})
        return result

    def preflight(self):
        return {'apple_silicon': platform.system() == 'Darwin' and platform.machine() == 'arm64',
                'mlx_installed': find_mlx_python() is not None,
                'datasets': self.datasets(), 'training_jobs': len(self.store.list('p8_training_run')),
                'note': 'Only operator-started LoRA jobs on Apple Silicon train weights. An adapter is not automatically deployed.'}

    def _dataset_path(self, filename):
        if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}\.zip', filename):
            raise ValueError('Choose a listed dataset ZIP by filename.')
        root = (self.store.root / 'exports').resolve()
        target = (root / filename).resolve()
        if target.parent != root:
            raise ValueError('Dataset must be in BugHunter exports.')
        return target

    def _save(self, id, **changes):
        with self.lock:
            previous = self.store.get('p8_training_run', id)
            if previous:
                return self.store.put('p8_training_run', {**previous, **changes}, id=id)

    def prepare(self, filename, model, iters=100, approved=False, dry_run=False):
        self.store.require_desktop_owner()
        if approved is not True:
            raise ValueError('Confirm training, resource usage, and dataset review before starting.')
        if self.store.setting('emergency_stop', False):
            raise ValueError('Emergency stop is active.')
        if type(iters) is not int or not 1 <= iters <= 2000:
            raise ValueError('Choose 1–2000 LoRA iterations.')
        model = valid_model(model)
        dataset = self._dataset_path(filename)
        manifest, rows = read_dataset(dataset)
        if dry_run is not True and (platform.system() != 'Darwin' or platform.machine() != 'arm64'):
            raise ValueError('Actual MLX training requires Apple Silicon macOS. Use dry-run to validate preparation elsewhere.')
        mlx_python = find_mlx_python() if dry_run is not True else (sys.executable if not getattr(sys,'frozen',False) else shutil.which('python3'))
        if dry_run is not True and mlx_python is None:
            raise ValueError('Install mlx-lm[train] into a Python interpreter available on your Mac PATH.')
        with self.lock:
            if any(x.get('status') in {'running','queued','stopping'} for x in self.store.list('p8_training_run')):
                raise ValueError('Finish or stop the existing training job first.')
            identifier = new_id('mlx')
            # All job paths are generated by the backend and constrained to its data directory.
            base = self.store.root / 'training_runs_p8' / identifier
            data = base / 'data'
            data.mkdir(parents=True, exist_ok=False)
            os.chmod(base, 0o700)
            with zipfile.ZipFile(dataset) as archive:
                (data / 'train.jsonl').write_bytes(archive.read('train.jsonl'))
                (data / 'valid.jsonl').write_bytes(archive.read('evaluation.jsonl'))
            for path in data.glob('*.jsonl'):
                os.chmod(path, 0o600)
            command = [mlx_python or 'python3', '-m', 'mlx_lm', 'lora', '--model', model,
                       '--train', '--data', str(data), '--iters', str(iters),
                       '--fine-tune-type', 'lora', '--adapter-path', str(base / 'adapter'),
                       '--batch-size', '1', '--num-layers', '8', '--steps-per-eval', '25',
                       '--steps-per-report', '10', '--save-every', str(min(iters, 100))]
            row = self.store.put('p8_training_run', {
                'id': identifier, 'filename': filename, 'model': model, 'iters': iters,
                'project_id': manifest.get('project_id'), 'train_count': len(rows['train']),
                'evaluation_count': len(rows['evaluation']), 'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
                'status': 'prepared' if dry_run else 'queued', 'detail': 'Validated dataset and command.' if dry_run else 'Queued local MLX LoRA training.',
                'adapter_path': str(base / 'adapter'), 'output_tail': '', 'exit_code': None,
                'training_performed': False, 'started_at': now()})
            if not dry_run:
                threading.Thread(target=self._worker, args=(identifier, command), daemon=True).start()
            return row

    def _worker(self, id, command):
        proc = None
        try:
            if self.store.setting('emergency_stop', False):
                self._save(id, status='cancelled', detail='Emergency stop active.')
                return
            # Avoid writing raw training examples or provider secrets into logs.
            base = Path(self.store.get('p8_training_run',id)['adapter_path']).parent
            with (base / 'train.log').open('w', encoding='utf-8') as logfile:
                with self.lock:
                    previous = self.store.get('p8_training_run', id)
                    if not previous or previous.get('status') != 'queued':
                        return  # Stop raced the start; do not launch a cancelled job.
                    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, text=True)
                    self.processes[id] = proc
                    self._save(id, status='running', detail='MLX LoRA process started.', training_performed=True)
                while proc.poll() is None:
                    with self.lock:
                        row = self.store.get('p8_training_run',id)
                    if self.store.setting('emergency_stop',False) or row['status'] in ('stopping','cancelled'):
                        proc.terminate()
                        try: proc.wait(timeout=6)
                        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=6)
                        break
                    time.sleep(.5)
                code = proc.wait()
            row = self.store.get('p8_training_run', id)
            if row.get('status') in ('stopping','cancelled') or self.store.setting('emergency_stop',False):
                status = 'cancelled'
            else:
                status = 'completed' if code == 0 and (base/'adapter'/'adapters.safetensors').is_file() else 'failed'
            # Output redaction: report only final status + log path, never the raw log.
            self._save(id, status=status, exit_code=code, finished_at=now(),
                       detail=('Adapter created.' if status=='completed' else 'Training stopped.' if status=='cancelled'
                               else 'Training did not produce a confirmed adapter; inspect local train.log.'))
        except Exception as exc:
            self._save(id, status='failed', detail=f'Training worker failed: {type(exc).__name__}', finished_at=now())
        finally:
            with self.lock:
                self.processes.pop(id,None)

    def stop(self,id,approved=False):
        self.store.require_desktop_owner()
        if approved is not True:
            raise ValueError('Confirm stopping the training process.')
        with self.lock:
            row = self.store.get('p8_training_run',id)
            if not row: raise ValueError('Training job not found.')
            if row['status'] not in {'running','queued','stopping'}:
                raise ValueError('Job is not running.')
            self._save(id,status='stopping',detail='Termination requested.')
            proc = self.processes.get(id)
            if proc:
                proc.terminate()
            else:
                # A queued job has not launched and can be cancelled immediately.
                self._save(id,status='cancelled',detail='Training cancelled before subprocess launch.',finished_at=now())
            return self.store.get('p8_training_run',id)

    def list(self):
        # Do not expose raw logs or source content to model/tools.
        return self.store.list('p8_training_run')[:50]

    def web_coverage(self,project_id):
        """Read-only OWASP-style evidence coverage; does not claim exploitation."""
        if not self.store.get('project', project_id):
            raise ValueError('Select an existing project.')
        traffic = self.store.list('traffic',project_id=project_id)
        tasks = [x for x in self.store.list('work_task') if x.get('project_id')==project_id]
        pricing = [x for x in traffic if re.search(r'checkout|price|cart|basket|quote|coupon|order',
                   str((x.get('request') or {}).get('url','')),re.I)]
        staging = [x for x in tasks if x.get('kind') in {'commerce_staging','staging_price_integrity'} or
                   isinstance(x.get('result'),dict) and 'cases' in x['result'] and 'evidence_ids' in x['result']]
        return {'project_id':project_id,'captured_requests':len(traffic),'checkout_requests_observed':len(pricing),
                'staging_tasks_observed':len(staging), 'checks':[
                 {'id':'proxy_inventory','status':'observed' if traffic else 'missing',
                  'detail':'Captured HTTP traffic exists.' if traffic else 'No Burp/gateway observations yet.'},
                 {'id':'pricing_fields','status':'lead' if pricing else 'missing',
                  'detail':'Checkout/price-like paths found; field presence alone is not a vulnerability.'},
                 {'id':'staging_invariants','status':'observed' if staging else 'missing',
                  'detail':'Inspect the dedicated staging quote receipts for actual server assertions.'},
                 {'id':'access_control','status':'not_verified','detail':'No cross-account authorization proof available from passive requests.'},
                 {'id':'transaction_integrity','status':'not_verified','detail':'Production order/payment changes are not attempted.'}],
                'assessment':'Evidence inventory only, not a penetration-test certificate or proof of exploitation.'}

    def dispatch(self,method,p):
        actions = {
            'advanced.p8.preflight':lambda:self.preflight(),
            'advanced.p8.datasets':lambda:self.datasets(),
            'advanced.p8.train':lambda:self.prepare(p['filename'],p['model'],p.get('iters',100),p.get('approved') is True,p.get('dry_run') is True),
            'advanced.p8.jobs':lambda:self.list(),
            'advanced.p8.stop':lambda:self.stop(p['id'],p.get('approved') is True),
            'advanced.p8.web_coverage':lambda:self.web_coverage(p['project_id']),
        }
        if method not in actions:raise ValueError('Unknown advanced P8 operation.')
        return actions[method]()
