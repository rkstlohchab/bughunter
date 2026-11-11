"""Run the app's fixed regression suites and retain a machine-readable report.

No user-supplied commands or targets are accepted. Each suite runs even if an
earlier one fails; missing dependencies and platform restrictions stay visible.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from bughunter.security import redact_text

MAX_LOG_BYTES = 4 * 1024 * 1024
_active_process = None


def terminate(process):
    if process is None or process.poll() is not None: return
    try:
        if os.name == 'posix': os.killpg(process.pid, signal.SIGTERM)
        else: process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL)
        else: process.kill()
        process.wait(timeout=3)


def checks():
    python = str(ROOT / 'backend/.venv/bin/python')
    if not Path(python).exists():
        python = sys.executable
    return [
        ('backend', 'Backend and knowledge regressions', [python, '-m', 'pytest', '-q', 'backend/tests'], 600),
        ('support', 'Packaging and desktop contracts', [python, '-m', 'pytest', '-q', 'tests', '--ignore=tests/mac_mcp'], 180),
        ('frontend', 'Frontend types and production build', ['npm', 'run', 'build'], 180),
        ('routing', 'Chat task routing', ['npm', 'run', 'test:routing'], 90),
        ('ui', 'Browser interaction regressions', ['npm', 'run', 'test:ui'], 600),
        ('native', 'Native shell unit tests', ['cargo', 'test', '--offline', '--manifest-path', 'src-tauri/Cargo.toml'], 600),
        ('memory', 'Local memory bridge contracts', [python, '-m', 'unittest', 'discover', '-s', 'tests/mac_mcp'], 180),
    ]


def unavailable(check_id, command):
    if not shutil.which(command[0]):
        return f'{command[0]} is not installed.'
    if check_id in {'frontend', 'routing', 'ui'} and not (ROOT / 'node_modules/typescript').is_dir():
        return 'Install frontend dependencies with npm ci.'
    if check_id == 'native' and sys.platform != 'darwin':
        return 'Native macOS shell verification requires macOS.'
    return None


def run_check(spec, directory, env):
    global _active_process
    check_id, title, command, timeout = spec
    row = {'id': check_id, 'title': title, 'status': 'running', 'command': command,
           'exit_code': None, 'log': check_id + '.log'}
    print(f'CHECK {check_id}: {title}', flush=True)
    reason = unavailable(check_id, command)
    if reason:
        row.update(status='blocked', detail=reason)
        print(f'BLOCKED {check_id}: {reason}', flush=True)
        return row
    began = time.monotonic()
    # The raw temporary file is removed at the end of this check; the retained
    # log is redacted and bounded, with truncation recorded in the report.
    with tempfile.TemporaryFile() as capture:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=capture, stderr=subprocess.STDOUT, start_new_session=True)
        _active_process = process
        try:
            process.wait(timeout=timeout)
            row.update(status='passed' if process.returncode == 0 else 'failed', exit_code=process.returncode)
        except subprocess.TimeoutExpired:
            terminate(process)
            row.update(status='timed_out', detail=f'Exceeded the {timeout}s check limit.', exit_code=process.returncode)
        finally:
            if process.poll() is None:
                terminate(process)
            _active_process = None
        capture.seek(0, 2)
        size = capture.tell()
        capture.seek(0)
        output = redact_text(capture.read(MAX_LOG_BYTES).decode('utf-8', errors='replace'))
    row['log_truncated'] = size > MAX_LOG_BYTES
    if row['log_truncated']:
        output += '\n[Log exceeded 4 MiB; remaining output omitted.]\n'
    log = directory / row['log']
    log.write_text(output, encoding='utf-8')
    log.chmod(0o600)
    row['duration_seconds'] = round(time.monotonic() - began, 2)
    # Keep the live feed concise; full per-check logs stay in the report folder.
    print(f"{row['status'].upper()} {check_id} ({row['duration_seconds']}s)", flush=True)
    print(output[-5000:], flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    directory = args.output_dir or ROOT / 'artifacts' / ('verification-' + uuid.uuid4().hex[:12])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'schema': 'bughunter-app-checks/v1', 'started_at': datetime.now(timezone.utc).isoformat(),
              'status': 'running', 'checks': [], 'live_integrations_verified': False}
    def save():
        draft = directory / 'report.json.tmp'
        draft.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        draft.chmod(0o600)
        draft.replace(directory / 'report.json')
    save()
    def stop(_signal, _frame):
        terminate(_active_process)
        report.update(status='cancelled', finished_at=datetime.now(timezone.utc).isoformat())
        save()
        raise SystemExit(130)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix='bughunter-checks-') as workspace:
        env = {**os.environ, 'BUGHUNTER_DATA_DIR': workspace, 'PYTHONPATH': str(ROOT / 'backend'),
               'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
        for spec in checks():
            try:
                row = run_check(spec, directory, env)
            except OSError as exc:
                row = {'id': spec[0], 'title': spec[1], 'status': 'failed', 'detail': str(exc)}
                print(f'FAILED {spec[0]}: {exc}', flush=True)
            report['checks'].append(row)
            save()
    states = {r['status'] for r in report['checks']}
    report['status'] = 'failed' if states & {'failed', 'timed_out'} else 'incomplete' if 'blocked' in states else 'passed'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print(f"REPORT {report['status']}: {directory / 'report.json'}", flush=True)
    return 0 if report['status'] == 'passed' else 2 if report['status'] == 'incomplete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
