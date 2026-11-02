"""Explicitly authorized, quote-only staging regression through policy/Burp gateway.

No request to an actual cart/checkout/payment API is ever constructed. All
requests use the operator's fixed test origin and a dedicated instrumented
endpoint with a staging manifest. There is no generic URL or payload executor.
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import urlsplit

from .policy import canonical_url, validate_scope, classify_effect, normalized_request
from .store import now

MANIFEST_PATH = '/.well-known/bughunter-price-audit.json'
QUOTE_PATH = '/__bughunter__/quote'
SUITE = (
    ('baseline', None, 1),
    ('client_price_lowered', 1, 1),
    ('client_price_zero', 0, 1),
)


def _valid_origin(value: str) -> str:
    url = canonical_url(value)
    u = urlsplit(url)
    if u.path != '/' or u.query:
        raise ValueError('Provide a staging origin, not a checkout path or URL with query parameters.')
    if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in {'localhost', '127.0.0.1'}):
        raise ValueError('Only HTTPS staging origins or loopback test servers are supported.')
    return url.rstrip('/')


def validate_configuration(project: dict) -> dict:
    if not project.get('authorization') or not project.get('scope'):
        raise ValueError('Select an authorized project with an explicit allowed scope.')
    cfg = project.get('staging_quote')
    if not isinstance(cfg, dict) or cfg.get('environment') != 'staging' or cfg.get('acknowledge_no_real_orders') is not True:
        raise ValueError('Configure a staging-only quote integration and acknowledge that no real orders are possible.')
    origin = _valid_origin(cfg.get('origin', ''))
    challenge = cfg.get('challenge')
    sku = cfg.get('test_sku')
    expected = cfg.get('expected_unit_price_minor')
    if not isinstance(challenge, str) or not re.fullmatch(r'[A-Za-z0-9_-]{24,80}', challenge):
        raise ValueError('Configure a 24–80 character random deployment challenge.')
    if not isinstance(sku, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,48}', sku):
        raise ValueError('Configure a synthetic, non-purchasable test SKU.')
    if type(expected) is not int or not 1 <= expected <= 10_000_000:
        raise ValueError('Specify a positive integer price in minor currency units.')
    for path, method in ((MANIFEST_PATH, 'GET'), (QUOTE_PATH, 'POST')):
        url = origin + path
        validate_scope(project, url, resolve=False)
        effect, _ = classify_effect(project, normalized_request({'method': method, 'url': url}))
        if effect != 'read_only':
            raise ValueError(f'Declare {method} {path} as an explicitly read-only staging API in the project first.')
    return {'origin': origin, 'challenge': challenge, 'test_sku': sku, 'expected_unit_price_minor': expected}


class StagingPriceAudit:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._active: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def _save(self, task_id, **changes):
        with self._lock:
            old = self.store.get('work_task', task_id)
            if old:
                self.store.put('work_task', {**old, **changes}, id=task_id)

    def _event(self, task_id, kind, message):
        with self._lock:
            old = self.store.get('work_task', task_id)
            if old:
                events = (old.get('events', []) + [{'kind': kind, 'text': message[:350], 'time': now()}])[-60:]
                self.store.put('work_task', {**old, 'events': events}, id=task_id)

    def start(self, project_id: str, approved=False):
        self.store.require_desktop_owner()
        if approved is not True:
            raise ValueError('Explicitly approve the staging-only, quote-only request batch.')
        if self.store.setting('emergency_stop', False):
            raise ValueError('Emergency stop is active.')
        project = self.app.policy.project(project_id)
        cfg = validate_configuration(project)
        with self._lock:
            if self._active:
                raise ValueError('A staging quote test is already running.')
            task = self.store.put('work_task', {
                'kind': 'staging_price', 'workflow': 'staging_price', 'project_id': project_id,
                'title': 'Staging price integrity (quote-only)', 'status': 'starting',
                'events': [{'kind': 'approval_granted', 'text': 'Approved three quote-only staging checks; no checkout or payment operations.', 'time': now()}],
                'result': {'cases': [], 'assessment': 'Not yet verified'},
            })
            stop = threading.Event()
            self._active[task['id']] = stop
            threading.Thread(target=self._run, args=(task['id'], project_id, cfg, stop), daemon=True).start()
            return self.app.tasks.describe(task)

    def cancel(self, task_id):
        with self._lock:
            event = self._active.get(task_id)
            if not event:
                raise ValueError('No active staging quote task exists with that ID.')
            event.set()
            self._event(task_id, 'notice', 'Stop requested. No additional requests will be submitted.')
            return self.app.tasks.describe(self.store.get('work_task', task_id))

    def _request(self, task_id, project_id, url, method, body, stop):
        if stop.is_set() or self.store.setting('emergency_stop', False):
            raise InterruptedError('Operator stopped the run.')
        request = {'method': method, 'url': url, 'headers': {'accept':'application/json', 'content-type':'application/json'}, 'body': json.dumps(body, sort_keys=True) if body is not None else ''}
        # Project-specified read-only paths and scope are evaluated again by the policy service.
        action = self.app.policy.queue(project_id, request, session_id='staging-quote-'+task_id[:12])
        if action['effect'] != 'read_only' or action['status'] != 'ready':
            raise ValueError('Staging endpoint is not explicitly approved as non-mutating.')
        # The gateway is required for real sites; its ticket confirms the exact on-wire request.
        result = self.app.policy.execute(action['id'])
        if result.get('status') != 'completed' or not result.get('evidence_ids'):
            raise RuntimeError('No verified HTTP receipt from the policy gateway.')
        response = result.get('response') or {}
        if not (200 <= int(response.get('status', 0)) < 300):
            raise RuntimeError('Quote endpoint returned a non-success HTTP status.')
        raw = str(response.get('body',''))
        if len(raw) > 20000:
            raise ValueError('Quote response exceeds the allowed test size.')
        try: data = json.loads(raw)
        except ValueError as exc: raise ValueError('Quote endpoint did not return JSON.') from exc
        if not isinstance(data, dict):
            raise ValueError('Quote endpoint returned an invalid JSON structure.')
        return data, result['evidence_ids']

    def _run(self, task_id, project_id, cfg, stop):
        report = {'cases': [], 'assessment': 'Unverified', 'evidence_ids': [], 'transactional_actions': 0, 'source': 'Staging quote-only API through approved policy gateway'}
        try:
            self._save(task_id, status='running')
            self._event(task_id, 'tool_call', 'Reading staging deployment manifest.')
            manifest, ids = self._request(task_id, project_id, cfg['origin'] + MANIFEST_PATH, 'GET', None, stop)
            report['evidence_ids'].extend(ids)
            # Require a deployed, explicitly non-transactional test contract.
            expected = {'protocol': 'bughunter-price-quote-v1', 'environment': 'staging',
                        'quote_only': True, 'orders_enabled': False, 'payments_enabled': False,
                        'challenge': cfg['challenge'], 'quote_path': QUOTE_PATH}
            if any(manifest.get(k) != v for k, v in expected.items()):
                raise ValueError('The staging ownership/test-contract manifest does not match the configured challenge and quote-only protections.')
            self._event(task_id, 'tool_result', 'Staging quote-only contract checked. No order or payment endpoint will be called.')
            for label, supplied_price, qty in SUITE:
                if stop.is_set() or self.store.setting('emergency_stop', False):
                    raise InterruptedError('Operator stopped the run.')
                body = {'test_sku': cfg['test_sku'], 'quantity': qty, 'client_unit_price_minor': cfg['expected_unit_price_minor'] if supplied_price is None else supplied_price,
                        'dry_run': True, 'challenge': cfg['challenge']}
                response, ids = self._request(task_id, project_id, cfg['origin'] + QUOTE_PATH, 'POST', body, stop)
                report['evidence_ids'].extend(ids)
                if (response.get('quote_only') is not True or response.get('order_created') is not False
                        or response.get('payment_initiated') is not False):
                    raise ValueError('The staging API did not confirm non-transactional execution; assessment aborted.')
                observed = response.get('total_minor')
                if type(observed) is not int or observed < 0:
                    raise ValueError('Quote result did not contain a valid integer total.')
                matches = observed == cfg['expected_unit_price_minor'] * qty
                row = {'case': label, 'expected_total_minor': cfg['expected_unit_price_minor'] * qty,
                       'observed_total_minor': observed, 'quote_matches_catalog': matches,
                       'evidence_ids': ids, 'status': 'pass' if matches else 'review_needed'}
                report['cases'].append(row)
                self._save(task_id, result=report)
                self._event(task_id, 'tool_result', f'{label}: {row["status"]}. Quote-only receipt recorded.')
            report['assessment'] = ('All quote totals matched the configured staging catalog.' if all(c['quote_matches_catalog'] for c in report['cases'])
                else 'Quote total differs from configured catalog. Review staging calculation; no live checkout exploit has been verified.')
            report['completed_at'] = now()
            self._save(task_id, status='completed', result=report)
            self._event(task_id, 'completed', f'{len(report["cases"])} staging quote checks completed; no orders or payments attempted.')
        except InterruptedError:
            self._save(task_id, status='cancelled', result=report)
            self._event(task_id, 'cancelled', 'Staging quote verification stopped; no further requests.')
        except Exception as exc:
            self._save(task_id, status='failed', result=report, error=f'{type(exc).__name__}: {str(exc)[:240]}')
            self._event(task_id, 'error', 'Test halted: '+str(exc)[:200])
        finally:
            with self._lock:
                self._active.pop(task_id, None)

    def dispatch(self, method, params):
        if method == 'commerce.staging.run':
            return self.start(project_id=params['project_id'], approved=params.get('approved') is True)
        if method == 'commerce.staging.stop':
            return self.cancel(params['id'])
        raise ValueError('Unknown staging quote operation.')
