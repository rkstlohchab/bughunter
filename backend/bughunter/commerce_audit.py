"""Evidence-backed, bounded cart regression runner for BugHunter's OWN loopback fixture.

A single explicit UI action authorizes the fixed batch on the disposable shop.
Never accepts a user-supplied URL, hostname, or untrusted test payload. Real
sites continue to use the separate one-request-per-approval policy gateway.
"""
from __future__ import annotations

import json
import math
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
    fixed_total: float | None = None
    fixed_coupons: tuple[str, ...] = ()
    fixed_error: str | None = None
    route: str = 'cart'

CASES = (
    Case('Client-controlled product price', {'product_id': 'excluded', 'quantity': 1, 'unit_price': 1}, 'Client-controlled price accepted', 200),
    Case('Zero-price product', {'product_id': 'excluded', 'quantity': 1, 'unit_price': 0}, 'Client-controlled price accepted', 200),
    Case('Negative product price', {'product_id': 'excluded', 'quantity': 1, 'unit_price': -5}, 'Client-controlled price accepted', 200),
    Case('Excluded product coupon', {'product_id': 'excluded', 'quantity': 1, 'coupon': 'ELIGIBLE10'}, 'Excluded item received coupon', 200),
    Case('Minimum spend bypass', {'product_id': 'regular', 'quantity': 1, 'coupon': 'MINIMUM20'}, 'Minimum-spend restriction bypassed', 100),
    Case('Coupon stacking', {'product_id': 'regular', 'quantity': 3, 'coupon': ['ELIGIBLE10', 'MINIMUM20']}, 'Prohibited coupon stacking accepted', 270, ('ELIGIBLE10',)),
    Case('Invalid quantity', {'product_id': 'regular', 'quantity': -1}, 'Invalid quantity accepted', fixed_error='invalid_quantity'),
    Case('Zero quantity', {'product_id': 'regular', 'quantity': 0}, 'Invalid quantity accepted', fixed_error='invalid_quantity'),
    Case('Fractional quantity', {'product_id': 'regular', 'quantity': 0.5}, 'Invalid quantity accepted', fixed_error='invalid_quantity'),
    Case('Quantity above maximum', {'product_id': 'regular', 'quantity': 21}, 'Invalid quantity accepted', fixed_error='invalid_quantity'),
    Case('Food menu price', {'product_id': 'bowl', 'quantity': 1, 'client_unit_price_minor': 1}, 'Client-controlled menu price accepted', 20.5, route='food-cart'),
    Case('Food addon price', {'product_id': 'bowl', 'quantity': 1, 'addons': ['drink'], 'client_addon_total_minor': 0}, 'Client-controlled addon price accepted', 23.5, route='food-cart'),
    Case('Food delivery fee', {'product_id': 'bowl', 'quantity': 1, 'delivery_fee_minor': 0}, 'Client-controlled delivery fee accepted', 20.5, route='food-cart'),
    Case('Food minimum spend', {'product_id': 'bowl', 'quantity': 1, 'coupon': 'FOOD10'}, 'Food minimum-spend restriction bypassed', 20.5, route='food-cart'),
    Case('Food bundle exclusion', {'product_id': 'family-meal', 'quantity': 1, 'coupon': 'FOOD10'}, 'Excluded meal received coupon', 44.5, route='food-cart'),
    Case('Food coupon replay', {'product_id': 'bowl', 'quantity': 1, 'addons': ['drink'], 'coupon': ['FOOD10', 'FOOD10']}, 'Food coupon replay accepted', 21.4, ('FOOD10',), route='food-cart'),
    Case('Food negative quantity', {'product_id': 'bowl', 'quantity': -1}, 'Invalid food quantity accepted', fixed_error='invalid_quantity', route='food-cart'),
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

    def _observed_response(self, action, result):
        """Require the exact action's stored, trusted loopback receipt before scoring."""
        saved = self.store.get('action', action['id'])
        if (not saved or result.get('id') != action['id'] or result.get('status') != 'completed'
                or result.get('lab_verified') is not True or not result.get('evidence_ids')
                or any(result.get(key) != saved.get(key) for key in
                       ('project_id', 'session_id', 'request', 'response', 'evidence_ids', 'status', 'lab_verified'))
                or any(saved.get(key) != action.get(key) for key in ('project_id', 'session_id', 'request'))):
            raise RuntimeError('No correlated, verified lab action receipt.')
        for evidence_id in result['evidence_ids']:
            receipt = self.store.get('traffic', evidence_id)
            if (not receipt or receipt.get('trusted_observation') is not True
                    or receipt.get('origin') != 'gateway' or receipt.get('action_id') != action['id']
                    or any(receipt.get(key) != result.get(key) for key in
                           ('project_id', 'session_id', 'request', 'response'))):
                raise RuntimeError('Lab evidence does not match this action and response.')
        response = result.get('response') or {}
        if response.get('truncated'):
            raise ValueError('Truncated lab response cannot establish a control result.')
        if type(response.get('status')) is not int:
            raise ValueError('Lab response has no valid HTTP status.')
        try:
            data = json.loads(response.get('body', ''), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
        except (ValueError, TypeError) as exc:
            raise ValueError('Lab response must contain valid JSON.') from exc
        if (not isinstance(data, dict) or data.get('lab_evidence') is not True
                or not isinstance(data.get('violations'), list)
                or any(not isinstance(value, str) for value in data['violations'])):
            raise ValueError('Lab response does not match the evidence contract.')
        return response['status'], data

    @staticmethod
    def _score(scenario, mode, status, response):
        if mode == 'fixed' and scenario.fixed_error:
            return (status == 400 and response.get('error_code') == scenario.fixed_error
                    and isinstance(response.get('error'), str) and bool(response['error'])
                    and not response['violations']), False
        valid = (status == 200 and 'error' not in response
                 and response.get('account') == 'alice'
                 and response.get('product_id') == scenario.body['product_id']
                 and response.get('quantity') == scenario.body['quantity']
                 and all(type(response.get(key)) in (int, float) and math.isfinite(response[key])
                         for key in ('accepted_total', 'canonical_total')))
        if scenario.route == 'food-cart':
            valid = (valid and response.get('fixture') == 'food-ordering-v1'
                and response.get('quote_only') is True and response.get('order_created') is False
                and response.get('payment_initiated') is False
                and all(type(response.get(key + '_minor')) is int
                        and response[key + '_minor'] / 100 == response[key]
                        for key in ('accepted_total', 'canonical_total')))
        observed = valid and scenario.failure in response['violations']
        if mode == 'vulnerable':
            return observed, observed
        return (valid and not response['violations']
                and response['accepted_total'] == response['canonical_total'] == scenario.fixed_total
                and response.get('accepted_coupons') == list(scenario.fixed_coupons)), observed

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
                        'method': 'POST', 'url': f'{base}/{mode}/{scenario.route}',
                        'headers': {'content-type': 'application/json', 'x-test-account': 'alice'},
                        'body': json.dumps(scenario.body)}, session_id='cart-lab-'+task_id[:12])
                    # Approved batch applies ONLY to app-owned loopback test fixtures.
                    self.app.policy.approve(action['id'])
                    result = self.app.policy.execute(action['id'])
                    status, response = self._observed_response(action, result)
                    passed, observed = self._score(scenario, mode, status, response)
                    if mode == 'vulnerable':
                        if observed: report['observed_failures'] += 1
                    else:
                        if passed: report['passed_controls'] += 1
                    entry = {'case': scenario.name, 'variant': mode, 'route': scenario.route, 'passed': passed, 'observed_violation': observed,
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
