#!/usr/bin/env python3
"""P05 verify-script: resolve a saved mapping recipe across three
fresh target processes and promote it to ``restart_verified`` only if
every restart produced the same value.

Usage::

    verify_game_profile_restarts.py \\
        --profile <recipe-id-or-path-to-json> \\
        --restarts 3 \\
        --report-dir <output-directory> \\
        [--launcher '<argv>' | --fixture]
        [--build-fingerprint <sha256>]

``--fixture`` uses the P00 disposable fixture process and can create its own saved recipe. A real game is
driven by ``--launcher`` which gets invoked once per restart; the
script expects the launched process to print ``bh-runtime-ready
<pid>`` on stdout within 10 seconds.

The script DOES NOT elevate status if:

  * fewer than ``--restarts`` fresh processes were seen (same
    ``start_time_ns`` twice — e.g. PID reuse without a fresh spawn),
  * the build fingerprint differed between restarts,
  * any resolve failed, OR
  * the resolved value varied without the operator passing
    ``--value-may-change``.

Exit 0 on promotion, 2 on blocked, 1 on failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _load_recipe(store, profile_id: str, profile_path):
    from bughunter.game_runtime.mappings import MappingStore
    ms = MappingStore(store)
    if profile_path is not None and profile_path.is_file():
        data = json.loads(profile_path.read_text())
        profile_id = data["id"]
    return ms, ms.get(profile_id)


def _stop_launcher(proc):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=2)
    finally:
        if proc.stdout:
            proc.stdout.close()


def _spawn_launcher(launcher_argv, timeout=10.0):
    import selectors
    proc = subprocess.Popen(launcher_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + timeout
        buffer = b''
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline-time.monotonic())):
                    break
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 65536:
                    raise RuntimeError('launcher output exceeded 64 KiB')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n',1)
                    parts = line.split()
                    if len(parts) == 2 and parts[0] == b'bh-runtime-ready' and parts[1].isdigit():
                        pid = int(parts[1])
                        if pid <= 0:
                            raise RuntimeError('invalid launcher PID')
                        return proc, pid
        raise RuntimeError(f'launcher did not emit bh-runtime-ready <pid> within {timeout}s')
    except BaseException:
        _stop_launcher(proc)
        raise


def _observe_once(adapter, module_base_lookup, target_fingerprint, recipe):
    from bughunter.game_runtime.pointers import resolve_recipe
    return resolve_recipe(recipe, adapter=adapter,
                          module_base_lookup=module_base_lookup,
                          target_fingerprint=target_fingerprint)


def _write_report(report_dir, report):
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / "report.json"
    out.write_text(json.dumps(report, indent=2))
    return out


def main(argv=None):
    import shlex
    from contextlib import ExitStack
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile')
    parser.add_argument('--restarts', type=int, default=3)
    parser.add_argument('--report-dir', required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--fixture', action='store_true')
    target.add_argument('--launcher')
    parser.add_argument('--build-fingerprint', help='assert expected measured fingerprint; never overrides it')
    parser.add_argument('--value-may-change', action='store_true')
    args = parser.parse_args(argv)
    if not 3 <= args.restarts <= 20:
        parser.error('--restarts must be in 3..20')
    if not args.fixture and not args.profile:
        parser.error('a live restart test requires a saved --profile')
    directory = Path(args.report_dir)
    if (directory/'report.json').exists():
        print('Use a fresh report directory.', file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
    from bughunter.store import Store
    from bughunter.game_runtime.mappings import MappingStore, Observation
    from bughunter.game_runtime.live_target import process_image, module_lookup
    from bughunter.memory_engine.mac_adapter import MacMemoryAdapter
    from bughunter.memory_engine.protocol import MemoryEngineUnavailable
    store = Store(os.environ.get('BUGHUNTER_DATA_DIR') or None)
    mappings = MappingStore(store)
    recipe = None
    report = {'phase_id':'P05', 'verdict':'blocked', 'scope':'fixture' if args.fixture else 'live',
              'observations':[], 'restarts_requested':args.restarts}
    try:
        if args.profile:
            path = Path(args.profile)
            recipe = mappings.get(json.loads(path.read_text())['id'] if path.is_file() else args.profile)
        seen = set()
        records = []
        for i in range(args.restarts):
            observation = {'restart':i+1}
            report['observations'].append(observation)
            with ExitStack() as stack:
                if args.fixture:
                    from bughunter.game_runtime.testing.fixture_target import Fixture
                    fixture = stack.enter_context(Fixture())
                    identity = fixture.identity()
                    pid, birth = identity.pid, identity.start_time_ns
                    fingerprint = identity.module_fingerprint
                    lookup = lambda name: identity.region(name).base
                    if recipe is None:
                        recipe = mappings.save_new(name='fixture restart recipe', target_fingerprint=fingerprint,
                            value_type='u32', root='mod:region_0+0x0', steps=[{'op':'add','offset':'0x20','width':4}])
                else:
                    launcher, pid = _spawn_launcher(shlex.split(args.launcher))
                    stack.callback(_stop_launcher, launcher)
                    path, fingerprint, birth = process_image(pid)
                    observation['executable'] = str(path)
                observation.update(pid=pid, start_time_ns=birth, target_fingerprint=fingerprint)
                if birth <= 0 or (pid,birth) in seen:
                    raise ValueError('restart did not produce a fresh process identity')
                seen.add((pid,birth))
                if args.build_fingerprint and fingerprint != args.build_fingerprint:
                    raise ValueError('measured build differs from --build-fingerprint')
                if not recipe.root.startswith('mod:'):
                    raise ValueError('restart verification requires a saved module-relative root')
                adapter = stack.enter_context(MacMemoryAdapter(pid))
                if not args.fixture:
                    lookup = module_lookup(adapter)
                resolved = _observe_once(adapter, lookup, fingerprint, recipe)
                observation.update(value=resolved.value, final_address=hex(resolved.final_address),
                                   recipe_revision=recipe.revision)
                records.append(Observation(session_id=f'restart-{i}',target_fingerprint=fingerprint,
                    process_instance_id=f'{pid}:{birth}',resolved_value=resolved.value,
                    resolved_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),recipe_revision=recipe.revision))
        values = [o.resolved_value for o in records]
        if not args.value_may_change and any(v != values[0] for v in values[1:]):
            raise ValueError('resolved values differ; no restart promotion')
        mappings.mark_restart_verified(recipe.id, observations=records)
        report.update(verdict='restart_verified', profile_id=recipe.id, recipe_revision=recipe.revision,
                      reason='same saved recipe resolved across three or more fresh measured processes')
        _write_report(directory, report)
        return 0
    except Exception as exc:
        report.update(verdict='blocked' if isinstance(exc, MemoryEngineUnavailable) else 'failed',
                      reason=f'{type(exc).__name__}: {exc}')
        _write_report(directory, report)
        return 2 if isinstance(exc, MemoryEngineUnavailable) else 1


if __name__ == '__main__':
    raise SystemExit(main())
