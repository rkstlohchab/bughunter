"""P10: operator-owned evidence casebook and temporal entity observations.

No new process attachment, HTTP request, command execution or browsing occurs here.
The service consumes bounded observations and gateway-corroborated P9 evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .store import now
from .verification_p9 import _observed_snapshot

MAX_FRAMES = 12
MAX_CASES = 75


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def analyze_entity_timeline(frames: list) -> dict:
    """Measure *decoded observations*; entity IDs and layouts remain untrusted hypotheses."""
    if not isinstance(frames, list) or not 3 <= len(frames) <= MAX_FRAMES:
        raise ValueError('Timeline requires 3–12 observed radar frames.')
    decoded = [_observed_snapshot(frame) for frame in frames]
    pids = [metadata['pid'] for metadata, _ in decoded]
    process_changed = len(set(pids)) != 1
    tracks: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for index, (_, entities) in enumerate(decoded):
        for ident, data in entities.items():
            tracks[ident].append((index, data))
    summaries = []
    for ident, observations in sorted(tracks.items())[:64]:
        distances = []
        health_changes = []
        team_changes = 0
        health_change_events = 0
        discontinuities = 0
        gaps = 0
        for (i, left), (j, right) in zip(observations, observations[1:]):
            if j != i + 1:
                gaps += j - i - 1
                continue
            if pids[i] != pids[j]:
                discontinuities += 1
                continue
            distances.append(math.hypot(right['x'] - left['x'], right['y'] - left['y']))
            if 'health' in left and 'health' in right:
                change = right['health'] - left['health']
                health_changes.append(change)
                if abs(change) > 1e-5:
                    health_change_events += 1
            if 'team' in left and 'team' in right and left['team'] != right['team']:
                team_changes += 1
        # Never interpolate through missing frames or between distinct process instances.
        summaries.append({'id': ident, 'observed_frames': len(observations),
                          'missing_frames': len(frames) - len(observations),
                          'gaps': gaps, 'pid_discontinuities': discontinuities, 'adjacent_pairs': len(distances),
                          'travel_xy': round(sum(distances), 5),
                          'largest_step_xy': round(max(distances, default=0), 5),
                          'net_health_change': round(sum(health_changes), 5) if health_changes else None,
                          'health_change_events': health_change_events,
                          'team_changes': team_changes,
                          'identity_warning': bool(process_changed or gaps or team_changes or discontinuities)})
    complete = [t for t in summaries if t['observed_frames'] == len(frames) and not t['identity_warning']]
    movements = sum(t['travel_xy'] > 0.00001 for t in summaries)
    changed_health = sum(t['health_change_events'] > 0 for t in summaries)
    warnings = ['Decoded layouts and entity IDs are unverified; this is not an ESP accuracy certification.']
    if process_changed:
        warnings.append('Process PID changed. IDs do not prove identity across restarts.')
    if any(t['identity_warning'] for t in summaries):
        warnings.append('Missing frames, team changes or PID changes weaken entity continuity.')
    if not movements and not changed_health:
        warnings.append('No change observed; confirm the test game state actually changed.')
    return {'type': 'entity_timeline', 'frame_count': len(frames),
            'process_changed': process_changed,
            'frame_hashes': [m['sha256'] for m, _ in decoded],
            'observed_entity_count': len(summaries), 'continuous_tracks': len(complete),
            'movement_tracks': movements, 'health_change_tracks': changed_health,
            'tracks': summaries, 'warnings': warnings,
            'verification_level': 'decoded_observation_only',
            'assessment': 'requires_identity_review' if process_changed or any(t['identity_warning'] for t in summaries)
                          else 'changes_observed' if movements or changed_health else 'no_changes_observed',
            'next_actions': ['Repeat on the offline fixture while deliberately changing player position or health.',
                             'Confirm the entity layout and stable pointers independently before using any overlay.']}


def _actions_for(report: dict) -> tuple[str, list[str]]:
    typ = report.get('type')
    if typ == 'staging_quote_evidence':
        status = report.get('assessment', 'insufficient_evidence')
        if not report.get('receipts_correlated'):
            return 'unverified', ['Re-run the dedicated staging quote test and correlate gateway receipts.']
        if status == 'staging_price_integrity_review_needed':
            return 'review_needed', ['Inspect server-side authoritative quote calculations in staging.',
                                     'Document which test case diverged from its expected total.']
        return 'observed', ['Review additional coupon, quantity and promotion invariants in staging.']
    if typ == 'entity_snapshot_comparison':
        return 'observed', ['Capture 3–12 observations with the same PID and compare the timeline.',
                            'Validate source entity layout before interpreting coordinates.']
    if typ == 'entity_timeline':
        return ('review_needed' if report.get('process_changed') else 'observed'), list(report.get('next_actions', []))
    raise ValueError('Unsupported evidence type.')


class InvestigationP10:
    def __init__(self, app):
        self.store = app.store

    def _project(self, project_id):
        if not isinstance(project_id, str) or not self.store.get('project', project_id):
            raise ValueError('Choose an existing project.')
        return project_id

    def _review(self, project_id, review_id):
        report = self.store.get('p9_evidence_review', review_id)
        if not report or report.get('project_id') != project_id:
            raise ValueError('P9 evidence does not belong to this project.')
        return report

    def _write(self):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop', False):
            raise ValueError('Emergency stop is active.')

    def create_from_review(self, project_id, review_id, title=''):
        project_id = self._project(project_id)
        self._write()
        review = self._review(project_id, review_id)
        if not isinstance(title, str) or len(title) > 140:
            raise ValueError('Title must be at most 140 characters.')
        if len(self.store.list('investigation_p10', project_id=project_id)) >= MAX_CASES:
            raise ValueError('Casebook is full; close and archive old cases before adding more.')
        report = review['report']
        level, actions = _actions_for(report)
        record = {'project_id': project_id, 'title': title.strip() or report['type'].replace('_', ' ').title(),
                  'source_kind': 'p9_evidence_review', 'source_id': review_id,
                  'source_hash': _sha(report), 'source_type': report['type'],
                  'assessment': report.get('assessment', report.get('result', 'unassessed')),
                  'evidence_level': report.get('verification_level', 'unverified'),
                  'state': 'open', 'evidence_state': level, 'next_actions': actions,
                  'checked_at': now(), 'events': [{'type': 'created', 'at': now()}]}
        return self.store.put('investigation_p10', record)

    def create_timeline(self, project_id, frames, title=''):
        project_id = self._project(project_id)
        self._write()
        if not isinstance(title, str) or len(title) > 140:
            raise ValueError('Title must be at most 140 characters.')
        if len(self.store.list('investigation_p10', project_id=project_id)) >= MAX_CASES:
            raise ValueError('Casebook is full.')
        report = analyze_entity_timeline(frames)
        result = self.store.put('p10_timeline', {'project_id': project_id, 'report': report})
        level, actions = _actions_for(report)
        return self.store.put('investigation_p10', {
            'project_id': project_id, 'title': title.strip() or 'Entity timeline investigation',
            'source_kind': 'p10_timeline', 'source_id': result['id'],
            'source_hash': _sha(report), 'source_type': 'entity_timeline',
            'assessment': report['assessment'], 'evidence_level': report['verification_level'],
            'state': 'open', 'evidence_state': level, 'next_actions': actions,
            'checked_at': now(), 'events': [{'type': 'created', 'at': now()}]})

    def _case(self, project_id, id):
        case = self.store.get('investigation_p10', id)
        if not case or case.get('project_id') != self._project(project_id):
            raise ValueError('Investigation not found in this project.')
        return case

    def _source(self, case):
        source = self.store.get(case['source_kind'], case['source_id'])
        if not source or source.get('project_id') != case['project_id']:
            return None
        return source.get('report')

    def get(self, project_id, id):
        case = self._case(project_id, id)
        report = self._source(case)
        current = bool(report is not None and _sha(report) == case['source_hash'])
        return {**case, 'evidence_current': current, 'source_report': report if current else None,
                'evidence_state': case['evidence_state'] if current else 'stale'}

    def refresh(self, project_id, id):
        case = self._case(project_id, id)
        self._write()
        report = self._source(case)
        matches = bool(report is not None and _sha(report) == case['source_hash'])
        state = case['evidence_state'] if matches else 'stale'
        events = list(case.get('events', []))[-29:]
        if not matches and state != case.get('evidence_state'):
            events.append({'type': 'evidence_stale', 'at': now()})
        return self.store.put('investigation_p10', {**case, 'evidence_state': state,
                              'checked_at': now(), 'events': events}, id=id)

    def set_state(self, project_id, id, state):
        case = self._case(project_id, id)
        self._write()
        if state not in ('open', 'reviewed', 'closed'):
            raise ValueError('Invalid case state.')
        events = [*case.get('events', [])[-29:], {'type': 'state_changed', 'state': state, 'at': now()}]
        return self.store.put('investigation_p10', {**case, 'state': state, 'events': events}, id=id)

    def list(self, project_id):
        project_id = self._project(project_id)
        rows = self.store.list('investigation_p10', project_id=project_id)[:MAX_CASES]
        result = []
        for row in rows:
            current = self._source(row)
            result.append({'id': row['id'], 'title': row['title'], 'source_type': row['source_type'],
                           'assessment': row['assessment'], 'state': row['state'],
                           'evidence_state': row['evidence_state'] if current is not None and _sha(current) == row['source_hash'] else 'stale',
                           'created_at': row['created_at'], 'next_actions': row['next_actions'][:3]})
        return result

    def summary(self, project_id):
        rows = self.list(project_id)
        return {'phase': 'P10', 'case_count': len(rows), 'cases': rows[:15],
                'note': 'Casebook assessments concern stored observations, not target-wide verification. The model may inspect but cannot create or approve cases.'}

    def export(self, project_id):
        project_id = self._project(project_id)
        # Metadata-only export: raw memory coordinates and HTTP responses are not exported.
        return {'schema': 'bughunter.p10.casebook.v1', 'project_id': project_id,
                'exported_at': now(), 'cases': self.list(project_id)}

    def dispatch(self, method, p):
        project = p.get('project_id')
        if method == 'investigation.p10.create_from_review':
            return self.create_from_review(project, p.get('review_id'), p.get('title', ''))
        if method == 'investigation.p10.create_timeline':
            return self.create_timeline(project, p.get('frames'), p.get('title', ''))
        if method == 'investigation.p10.refresh':
            return self.refresh(project, p.get('id'))
        if method == 'investigation.p10.set_state':
            return self.set_state(project, p.get('id'), p.get('state'))
        if method == 'investigation.p10.list':
            return self.list(project)
        if method == 'investigation.p10.get':
            return self.get(project, p.get('id'))
        if method == 'investigation.p10.summary':
            return self.summary(project)
        if method == 'investigation.p10.export':
            return self.export(project)
        raise ValueError('Unknown P10 casebook operation.')
