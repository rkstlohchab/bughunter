#!/usr/bin/env python3
"""Capture fixture or live feature observations without claiming gameplay acceptance.

The live path discovers and hashes the running executable, records attach failures,
and requires an observed module-relative recipe before any write. Without a saved
recipe, discovery is diagnostic only: no candidate is selected for mutation.
Enabled sampling schedules repeated conditional writes through FeatureRun; each
sample revalidates the saved object path. A successful live capture remains pending
separate gameplay, round/death/disconnect and three-launch evidence. This CLI is
standalone and is not controlled by the app's Stop button.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path


def _load_recipe(store, profile):
    from bughunter.game_runtime.mappings import MappingStore
    ms = MappingStore(store)
    p = Path(profile)
    if p.is_file():
        data = json.loads(p.read_text())
        return ms, ms.get(data["id"])
    return ms, ms.get(profile)


def _render_gameplay_template(report_dir: Path, recipe, apply_value):
    body = f"""# Gameplay observations — {recipe.name}

> Fill this in during the live acceptance run. Mark each cell
> **YES** or **NO** so a reviewer can promote P06 beyond the
> code-only gate.

## Setup

- Local mode: Freeplay / Impostor local lobby / other: ________________
- Match setting (kill cooldown seconds): ________________
- Which Mac / build / date: ________________

## 1. Baseline (feature OFF, ≥3 kill cycles)

- Did you observe the kill button go READY exactly after the full
  cooldown each time? **YES / NO**
- Observed average cooldown length in seconds: ________________
- Any external pauses or map transitions: ________________

## 2. Enabled (apply_value = {apply_value}, ≥3 kill cycles)

- Do the native receipts show a readback matching apply_value? **YES / NO**
- Did the kill button go READY **earlier than baseline** after each kill? **YES / NO**
- Describe the observed timing (seconds between kill and next ready): __________
- Did the game accept the earlier repeat kill action? **YES / NO**

## 3. Disabled (feature stopped, ≥3 kill cycles)

- Did baseline cooldown return after Stop? **YES / NO**
- Did the stop require a round restart? **YES / NO** (if YES, explain)
- Do the CLI receipts show stopped writes and clean adapter shutdown? **YES / NO**

## Decision — fill each box after the run

- [ ] P06.baseline observed — normal cooldown prevents the repeated kill.
- [ ] P06.readback observed — the live field reflects the write.
- [ ] P06.behavior observed — kills accepted earlier than baseline
      across ≥3 cycles.
- [ ] P06.disable observed — normal behavior returns after stop.
- [ ] P06.lifecycle observed — round transitions / disconnects /
      process exit stop writes against stale objects.
- [ ] P06.restarts observed — three fresh launches reproduce the
      resolver (`scripts/verify_game_profile_restarts.py`).

Attach supporting evidence (video snippets, console logs, screenshots)
to this directory.
"""
    (report_dir / "gameplay-observations.md").write_text(body)


def _summarise_cycles(samples: list[dict], cooldown_hint: float | None) -> dict:
    values = [(s["t"], s["value"]) for s in samples if "value" in s]
    if not values:
        return {"cycle_events": [], "min_value": None, "max_value": None,
                "sample_count": 0}
    t0 = values[0][0]
    cycles: list[dict] = []
    for i in range(1, len(values)):
        if values[i][1] - values[i - 1][1] >= 3.0:
            cycles.append({
                "t_s": values[i][0] - t0,
                "before": values[i - 1][1], "after": values[i][1],
                "matches_hint": (cooldown_hint is not None
                                 and abs(values[i][1] - cooldown_hint) <= 1.0),
            })
    vs = [v for _, v in values]
    return {
        "cycle_events": cycles,
        "min_value": min(vs), "max_value": max(vs),
        "sample_count": len(vs),
    }


def main(argv=None) -> int:
    import math
    import signal
    from contextlib import ExitStack
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile')
    parser.add_argument('--feature', default='no_cooldown', choices=['no_cooldown'])
    parser.add_argument('--report-dir', required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--fixture', action='store_true')
    target.add_argument('--pid', type=int)
    parser.add_argument('--duration-s', type=float, default=30.0)
    parser.add_argument('--apply-value', type=float, default=0.0)
    parser.add_argument('--cooldown-hint', type=float)
    parser.add_argument('--max-candidates', type=int, default=32)
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration_s) or not 0 < args.duration_s <= 600:
        parser.error('--duration-s must be finite and in (0, 600]')
    if not math.isfinite(args.apply_value) or args.apply_value != 0:
        parser.error('no_cooldown requires --apply-value 0')
    if args.cooldown_hint is not None and (not math.isfinite(args.cooldown_hint) or not 0 < args.cooldown_hint <= 120):
        parser.error('--cooldown-hint must be in (0, 120]')
    if not 1 <= args.max_candidates <= 1024:
        parser.error('--max-candidates must be in 1..1024')
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    if any((report_dir / name).exists() for name in ('summary.json', 'observations.jsonl', 'gameplay-observations.md')):
        print('Use a fresh report directory; existing evidence is never overwritten.', file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from bughunter.store import Store
    from bughunter.game_runtime.mappings import MappingStore
    from bughunter.game_runtime.features import FeatureRun, FeatureContext
    from bughunter.game_runtime.among_us import find_among_us_pid, SUPPORTED_BUILD_FINGERPRINT
    from bughunter.game_runtime.live_target import process_image, module_lookup
    from bughunter.memory_engine.mac_adapter import MacMemoryAdapter
    from bughunter.memory_engine.protocol import MemoryEngineUnavailable
    summary = {'verdict': 'blocked', 'scope': 'fixture' if args.fixture else 'live',
               'gameplay_verified': False, 'runtime_path': 'standalone_cli', 'stage': 'target'}
    run = ctx = None
    old_term = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('termination requested')
    signal.signal(signal.SIGTERM, interrupted)
    code = 2
    try:
        with ExitStack() as stack:
            store = Store(os.environ.get('BUGHUNTER_DATA_DIR') or None)
            mappings = MappingStore(store)
            if args.fixture:
                from bughunter.game_runtime.testing.fixture_target import Fixture
                fixture = stack.enter_context(Fixture())
                identity = fixture.identity()
                pid, birth = identity.pid, identity.start_time_ns
                fingerprint = identity.module_fingerprint
                lookup = lambda name: identity.region(name).base
                recipe = _load_recipe(store, args.profile)[1] if args.profile else mappings.save_new(
                    name='fixture countdown', target_fingerprint=fingerprint, value_type='u32',
                    root='mod:region_0+0x0', steps=[{'op':'add', 'offset':'0x20', 'width':4}])
            else:
                pid = args.pid or find_among_us_pid()
                if pid is None:
                    raise ValueError('Among Us is not running')
                path, fingerprint, birth = process_image(pid)
                summary['executable'] = str(path)
                if path.name != 'AmongUs' or fingerprint != SUPPORTED_BUILD_FINGERPRINT:
                    raise ValueError('running executable has no qualified Among Us field layout')
                recipe = _load_recipe(store, args.profile)[1] if args.profile else None
            summary.update(pid=pid, start_time_ns=birth, target_fingerprint=fingerprint, stage='attach')
            adapter = stack.enter_context(MacMemoryAdapter(pid))
            if not args.fixture:
                lookup = module_lookup(adapter)
            summary['stage'] = 'resolver'
            if recipe is None:
                from bughunter.game_runtime.among_us import scan_for_local_player_candidates, validate_candidates_read_only
                candidates = scan_for_local_player_candidates(adapter, max_candidates=args.max_candidates,
                                                               kill_cooldown_setting=args.cooldown_hint)
                survivors = validate_candidates_read_only(adapter, candidates, cooldown_hint=args.cooldown_hint)
                (report_dir / 'candidates.json').write_text(json.dumps({
                    'candidates':[c.__dict__ for c in candidates], 'countdown_candidates':[c.__dict__ for c in survivors],
                    'complete':candidates.complete, 'reason':candidates.reason,
                    'skipped_regions':candidates.skipped_regions, 'picked':None}, indent=2))
                raise ValueError('measured LocalPlayer root is missing; candidates are diagnostic only and no write was attempted')
            if not args.fixture and (not recipe.root.startswith('mod:') or recipe.verification_status not in ('observed', 'restart_verified')):
                raise ValueError('live test requires an observed module-relative LocalPlayer recipe; no candidate writes')
            summary.update(profile_id=recipe.id, recipe_revision=recipe.revision, feature_id=args.feature)
            _render_gameplay_template(report_dir, recipe, args.apply_value)
            ctx = FeatureContext(adapter, lookup, fingerprint, f'{pid}:{birth}')
            run = FeatureRun(recipe, args.feature, {'apply_value':args.apply_value})
            # Registered last: stop precedes adapter close and fixture shutdown on every exit.
            def cleanup():
                if run is not None and not run._stop_requested:
                    run.stop(ctx, write_cleanup=False)
            stack.callback(cleanup)
            with (report_dir / 'observations.jsonl').open('x') as output:
                run.start(ctx)
                def sample(phase, maintain=False):
                    summary['stage'] = phase
                    output.write(json.dumps({'phase':phase, 't':time.monotonic()})+'\n')
                    rows = []
                    end = time.monotonic()+args.duration_s
                    while time.monotonic() < end:
                        value = run.maintain(ctx) if maintain else run.inspect(ctx)
                        row = {'phase':phase, 't':time.monotonic(), 'value':value}
                        rows.append(row); output.write(json.dumps(row)+'\n'); output.flush()
                        time.sleep(min(0.2, max(0, end-time.monotonic())))
                    return rows
                baseline = sample('baseline')
                if args.fixture and run.baseline_value != 60:
                    raise RuntimeError('fixture baseline does not match independent u32 oracle')
                apply_value = int(args.apply_value) if recipe.value_type.startswith(('u','i')) else args.apply_value
                run.apply(ctx, apply_value)
                enabled = sample('enabled', maintain=True)
                run.stop(ctx, write_cleanup=False)
                if args.fixture:
                    # The target performs its own reset, analogous to a new round.
                    fixture.set('countdown', 60)
                disabled = sample('disabled')
                if args.fixture and fixture.get('countdown') != 60:
                    raise RuntimeError('independent fixture reset was not observed')
            summary.update(baseline=_summarise_cycles(baseline,args.cooldown_hint),
                           enabled=_summarise_cycles(enabled,args.cooldown_hint),
                           disabled=_summarise_cycles(disabled,args.cooldown_hint),
                           verdict='fixture_pass' if args.fixture else 'pending_gameplay_review',
                           cleanup_status=run.cleanup_status, stage='complete',
                           reason='Live gameplay, lifecycle and three-launch acceptance require separate evidence.')
            code = 0 if args.fixture else 2
    except KeyboardInterrupt:
        summary.update(verdict='interrupted', reason='operator stopped verification')
        code = 130
    except MemoryEngineUnavailable as exc:
        summary.update(verdict='blocked', reason=str(exc))
        code = 2
    except Exception as exc:
        summary.update(verdict='failed' if run else 'blocked', reason=f'{type(exc).__name__}: {exc}')
        code = 1 if run else 2
    finally:
        signal.signal(signal.SIGTERM, old_term)
        if run is not None:
            summary['feature_events'] = [e.__dict__ for e in run.events]
            summary['cleanup_status'] = run.cleanup_status
        (report_dir / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
    print(json.dumps({'verdict':summary['verdict'], 'report':str(report_dir/'summary.json')}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
