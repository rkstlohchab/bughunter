"""Evidence-backed, bounded cart regression runner for BugHunter's OWN loopback fixture.

A single explicit UI action authorizes the fixed batch on the disposable shop.
Never accepts a user-supplied URL, hostname, or untrusted test payload. Real
sites continue to use the separate one-request-per-approval policy gateway.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from .store import now

# Fixed fixture tests: no checkout/payment, external hostname, open redirects,
# discovered endpoints, or arbitrary model-generated request payloads.
@dataclass(frozen=True)
class Case:
    name: str
    body: dict
    failure: str

CASES = (
    Case('Client-controlled product price', {'product_id': 'excluded', 'quantity': 1, 'unit_price': 1}, 'Client-controlled price accepted'),
    Case('Zero-price product', {'product_id': 'excluded', 'quantity': 1, 'unit_price': 0}, 'Client-controlled price accepted'),
    Case('Negative product price', {'product_id': 'excluded', 'quantity': 1, 'unit_price': -5}, 'Client-controlled price accepted'),
    Case('Excluded product coupon', {'product_id': 'excluded', 'quantity': 1, 'coupon': 'ELIGIBLE10'}, 'Excluded item received coupon'),
    Case('Minimum spend bypass', {'product_id': 'regular', 'quantity': 1, 'coupon': 'MINIMUM20'}, 'Minimum-spend restriction bypassed'),
    Case('Coupon stacking', {'product_id': 'regular', 'quantity': 3, 'coupon': ['ELIGIBLE10', 'MINIMUM20']}, 'Prohibited coupon stacking accepted'),
    Case('Invalid quantity', {'product_id': 'regular', 'quantity': -1}, 'Invalid quantity accepted'),
    Case('Zero quantity', {'product_id': 'regular', 'quantity': 0}, 'Invalid quantity accepted'),
    Case('Fractional quantity', {'product_id': 'regular', 'quantity': 0.5}, 'Invalid quantity accepted'),
)

class CommerceAudit:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._stop: dict[str, threading.Event] = {}
        self._mutex = threading.RLock()

    def _record(self, task_id: str, kind: str, message: str, **extra):
        with self._mutex:
            prior = self.store.get('work_task', task_id)
            if not prior: return
            events = (prior.get('events', []) + [{'kind': kind, 'text': message[:500], 'time': now(), **extra}])[-150:]
            self.store.put('work_task', {**prior, 'events': events})

    def _update(self, task_id: str, **changes):
        with self._mutex:
            prior = self.store.get('work_task', task_id)
            if prior: self.store.put('work_task', {**prior, **changes})

    def start(self, approved=False):
        if approved is not True:
            raise ValueError('Click the explicit authorization button to run the disposable local shop tests.')
        if self.store.setting('emergency_stop', False):
            raise ValueError('Emergency stop is active.')
        # Serialize starting a run with fixture initialization: a second click
        # must not reset the shop while the first run is collecting evidence.
        with self._mutex:
            if self._stop:
                raise ValueError('A cart regression job is already active.')
            # A live, private loopback fixture is the only permitted destination.
            state = self.app.lab.setup()
            project, runtime = state['project'], state['runtime']
            url = urlsplit(runtime['url'])
            if (url.scheme, url.hostname) != ('http', '127.0.0.1') or not project.get('lab_project'):
                raise ValueError('Cart automation is restricted to the bundled loopback lab.')
            task = self.store.put('work_task', {
                'kind': 'cart_lab', 'title': 'Autonomous cart regression — local test shop',
                'project_id': project['id'], 'status': 'starting',
                'source': 'observed_loopback_fixture', 'events': [
                    {'kind': 'created', 'text': 'Operator authorized fixed test cases on the disposable local shop.', 'time': now()}],
                'result': {'cases': [], 'passed_controls': 0, 'observed_failures': 0},
            })
            stop = threading.Event()
            self._stop[task['id']] = stop
            threading.Thread(target=self._run, args=(task['id'], project['id'], runtime['url'], stop), daemon=True).start()
        return self.app.tasks.describe(task)

    def cancel(self, task_id):
        with self._mutex:
            stop = self._stop.get(task_id)
            if not stop: raise ValueError('This cart lab run is no longer active.')
            stop.set()
            self._record(task_id, 'notice', 'Stop requested. Active request may finish; remaining cases will not run.')
            return self.app.tasks.describe(self.store.get('work_task', task_id))

    def _run(self, task_id, project_id, base, stop):
        self._update(task_id, status='running')
        report = {'cases': [], 'passed_controls': 0, 'observed_failures': 0, 'findings': []}
        try:
            for scenario in CASES:
                # Compare an intentionally vulnerable implementation with its fixed counterpart.
                for mode in ('vulnerable', 'fixed'):
                    if stop.is_set() or self.store.setting('emergency_stop', False):
                        self._update(task_id, status='cancelled', result=report)
                        self._record(task_id, 'cancelled', 'Stopped before the next HTTP request.')
                        return
                    self._record(task_id, 'tool_call', f'Lab cart POST: {scenario.name} / {mode}')
                    # Explicitly configured and guarded local transport; no remote execution fallback.
                    action = self.app.policy.queue(project_id=project_id, request={
                        'method': 'POST', 'url': f'{base}/{mode}/cart',
                        'headers': {'content-type': 'application/json', 'x-test-account': 'alice'},
                        'body': json.dumps(scenario.body)}, session_id='cart-lab-'+task_id[:12])
                    # Approved batch applies ONLY to app-owned loopback test fixtures.
                    self.app.policy.approve(action['id'])
                    result = self.app.policy.execute(action['id'])
                    if result['status'] != 'completed' or not result.get('evidence_ids'):
                        raise RuntimeError(f'{scenario.name}: no verified {mode} HTTP observation')
                    response = json.loads(result['response']['body'])
                    observed = scenario.failure in response.get('violations', [])
                    if mode == 'vulnerable':
                        passed = observed
                        if observed: report['observed_failures'] += 1
                    else:
                        passed = not observed
                        if passed: report['passed_controls'] += 1
                    entry = {'case': scenario.name, 'variant': mode, 'passed': passed, 'observed_violation': observed,
                             'http_status': result['response']['status'], 'evidence_ids': result['evidence_ids'],
                             'action_id': action['id']}
                    report['cases'].append(entry)
                    self._update(task_id, result=report)
                    self._record(task_id, 'tool_result', f"{scenario.name} / {mode}: {'expected outcome' if passed else 'unexpected outcome'}", evidence_id=result['evidence_ids'][0])
                    if mode == 'vulnerable' and observed:
                        finding = self.app.reports.save({
                            'project_id': project_id, 'title': 'LAB: ' + scenario.name, 'status': 'observed',
                            'summary': 'The disposable vulnerable shop accepted a forbidden cart state. This is intentionally vulnerable lab code, not a finding on an external site.',
                            'impact': 'Illustrates server-side cart validation failures in a controlled fixture.',
                            'steps': [f'Execute the fixed {scenario.name} fixture in the local vulnerable variant.',
                                      'Repeat the same test on the fixed control.'],
                            'evidence_ids': result['evidence_ids'], 'reviewed': False,
                            'controls': 'The fixed variant must reject or ignore the manipulated state.'})
                        report['findings'].append(finding['id'])
            report['completed_at'] = now()
            self._update(task_id, status='completed' if all(e['passed'] for e in report['cases']) else 'failed', result=report)
            self._record(task_id, 'completed', f"Compared {len(report['cases'])} observed responses; {report['observed_failures']} vulnerable behaviors, {report['passed_controls']} fixed controls.")
        except Exception as exc:
            if stop.is_set() or self.store.setting('emergency_stop', False):
                self._update(task_id, status='cancelled', result=report,
                             error='Operator stop; inspect the last action before considering a new run.')
                self._record(task_id, 'cancelled', 'Cart lab stopped; no uncertain request was retried.')
            else:
                self._update(task_id, status='failed', result=report, error=f'{type(exc).__name__}: {str(exc)[:220]}')
                self._record(task_id, 'error', 'Cart lab aborted; inspect the incomplete evidence before restarting.')
        finally:
            with self._mutex: self._stop.pop(task_id, None)

    def dispatch(self, method, params):
        if method == 'commerce.lab.run': return self.start(**params)
        if method == 'commerce.lab.stop': return self.cancel(params['id'])
        raise ValueError('Unknown commerce automation method')
