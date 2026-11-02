"""P3: resumable, evidence-first research sessions using the local Tor Browser.

One operator-driven advance reads at most three same-origin pages. A challenge
pauses the queue; the human resolves it in the visible browser before resume.
No form submission, CAPTCHA solving, hidden browser fallback, or auto-publish.
"""
from __future__ import annotations

import re
import threading
from urllib.parse import urldefrag, urljoin

from .store import now
from .tor_research import _origin, validated_url

MAX_PAGES = 25
MAX_DEPTH = 2
MAX_QUEUE = 100
MAX_HISTORY = 80
ACTIVE = {'ready', 'running', 'waiting_for_human', 'interrupted'}


def detect_interruption(page: dict) -> str | None:
    """Heuristic, not a CAPTCHA solver. Require strong page-level signals."""
    title = str(page.get('title') or '').lower()[:250]
    text = str(page.get('excerpt') or '').lower()[:2500]
    front = text[:650]
    if re.search(r'^(captcha|verify you are human|human verification|security challenge|robot check|just a moment)(?:\b|$)', title):
        return 'captcha'
    if re.search(r'verify you are human|complete the security challenge|rotate the image|checking your browser|captcha (required|challenge|verification|code|image)', front):
        return 'captcha'
    if re.search(r'^(sign in|log in|login|authentication required|session expired)\b', title):
        return 'login'
    if re.search(r'^(sign in|log in)\b.{0,100}(password|username|account)', front, re.S):
        return 'login'
    if not text.strip() and re.search(r'(access denied|rate limit|request blocked)', title):
        return 'access_denied'
    return None


class ResearchBrowserP3:
    def __init__(self, tor_service):
        self.tor = tor_service
        self.store = tor_service.store
        self.lock = threading.RLock()
        # A browser process dies on app restart. Do not claim in-flight work continues.
        for session in self.store.list('research_browser_p3'):
            if session.get('status') in {'running', 'waiting_for_human'}:
                self._save(session, status='interrupted', blocker='Browser session must be reopened after restart.')

    def _save(self, session, **fields):
        return self.store.put('research_browser_p3', {**session, **fields, 'last_activity_at': now()}, id=session['id'])

    def _get(self, id):
        row = self.store.get('research_browser_p3', id)
        if not row:
            raise ValueError('Research browser session was not found.')
        return row

    def _summary(self, row):
        # No full content or credentials in the overview.
        return {k: v for k, v in row.items() if k not in {'queue', 'seen'}} | {
            'queued': len(row.get('queue', [])),
            'next_url': (row.get('queue') or [{}])[0].get('url'),
            'visited_count': len(row.get('seen', [])),
        }

    def list(self, project_id='*'):
        sessions = self.store.list('research_browser_p3')
        if project_id != '*':
            sessions = [s for s in sessions if s.get('project_id') in (None, project_id)]
        return [self._summary(s) for s in sessions[:100]]

    def status(self, id):
        return self._summary(self._get(id))

    def start(self, source_id: str, max_pages: int = 10, max_depth: int = 1):
        if type(max_pages) is not int or not (1 <= max_pages <= MAX_PAGES):
            raise ValueError('Choose 1–25 pages.')
        if type(max_depth) is not int or not (0 <= max_depth <= MAX_DEPTH):
            raise ValueError('Choose a same-site link depth from 0 to 2.')
        source = self.store.get('tor_source', source_id)
        if not source:
            raise ValueError('Choose a saved research source.')
        with self.lock:
            if any(s.get('status') in ACTIVE for s in self.store.list('research_browser_p3')):
                raise ValueError('Finish or stop the existing research browser session first.')
            url = validated_url(source['url'])
            row = self.store.put('research_browser_p3', {
                'source_id': source_id, 'project_id': source.get('project_id'),
                'source_title': source.get('title', ''), 'origin': url,
                'status': 'ready', 'blocker': None, 'max_pages': max_pages,
                'max_depth': max_depth, 'queue': [{'url': url, 'depth': 0}],
                'seen': [], 'captured_ids': [], 'history': [],
                'last_url': None, 'last_title': None, 'error': None,
                'last_activity_at': now(),
            })
            self.store.event('research.p3.started', {'session_id': row['id'], 'source_id': source_id})
            return self._summary(row)

    def _append(self, row, message, **fields):
        history = (row.get('history') or [])[-(MAX_HISTORY - 1):]
        history.append({'at': now(), 'message': message[:220]})
        saved = self._save(row, history=history, **fields)
        self.store.event('research.p3.event', {'session_id': saved['id'], 'status': saved['status'], 'event': message[:90]})
        return saved

    def _navigate_or_read(self, row, target):
        browser = self.tor.browser
        browser_status = browser.status()
        if not browser_status['running']:
            page = browser.open(row['source_id'])
        elif browser_status.get('source_id') != row['source_id']:
            raise ValueError('Another source is open in Tor Browser. Close it before resuming.')
        else:
            page = browser.read_current()
        # A site may redirect to a login/CAPTCHA page: pause, don't recrawl it.
        if detect_interruption(page):
            return page
        if page['url'] != target:
            page = browser.navigate(target)
        return page

    def _one_step(self, row, after_human=False):
        if self.store.setting('emergency_stop', False):
            raise ValueError('Task execution is stopped in Settings.')
        if row['status'] in {'completed', 'stopped'}:
            return row
        if row['status'] == 'waiting_for_human' and not after_human:
            return row
        if not row['queue'] or len(row['captured_ids']) >= row['max_pages']:
            return self._append(row, 'Research queue completed.', status='completed', blocker=None)
        head = row['queue'][0]
        try:
            page = self._navigate_or_read(row, head['url'])
            interruption = detect_interruption(page)
            if interruption:
                return self._append(row, 'Browser needs manual intervention: ' + interruption,
                                    status='waiting_for_human', blocker=interruption,
                                    last_url=page['url'], last_title=page['title'], error=None)
            # A successful manual challenge needs to return to the requested URL.
            if page['url'] != head['url']:
                page = self.tor.browser.navigate(head['url'])
                interruption = detect_interruption(page)
                if interruption:
                    return self._append(row, 'Browser needs manual intervention: ' + interruption,
                                        status='waiting_for_human', blocker=interruption,
                                        last_url=page['url'], last_title=page['title'], error=None)
            saved = self.tor.browser.capture()
            seen = [*row['seen'], head['url']]
            captured = [*row['captured_ids'], saved['id']]
            queue = list(row['queue'][1:])
            if head['depth'] < row['max_depth'] and len(captured) < row['max_pages']:
                queued = {q['url'] for q in queue} | set(seen)
                for link in page.get('links', []):
                    try:
                        target = validated_url(urldefrag(urljoin(page['url'], link['url'])).url)
                    except (TypeError, ValueError, KeyError):
                        continue
                    if _origin(target) != _origin(row['origin']) or target in queued:
                        continue
                    if len(queue) >= MAX_QUEUE:
                        break
                    queued.add(target)
                    queue.append({'url': target, 'depth': head['depth'] + 1})
            status = 'completed' if len(captured) >= row['max_pages'] or not queue else 'running'
            return self._append(row, 'Captured unreviewed source page.', status=status,
                                queue=queue, seen=seen, captured_ids=captured,
                                last_url=page['url'], last_title=page['title'], blocker=None, error=None)
        except Exception as exc:
            # A redirect or failed WebDriver call may leave the browser blank. Close
            # it so the next resume can safely reopen the bookmarked origin.
            try:
                if self.tor.browser.status().get('source_id') == row['source_id']:
                    self.tor.browser.close()
            except Exception:
                pass
            # Keep the head of queue intact for a real retry, no phantom capture.
            return self._append(row, 'Browser step failed.', status='interrupted', error=str(exc)[:300],
                                blocker='Check Tor and browser connectivity, then try again.')

    def advance(self, id, steps=1):
        if type(steps) is not int or not (1 <= steps <= 3):
            raise ValueError('Run 1–3 page steps at a time.')
        with self.lock:
            row = self._get(id)
            for _ in range(steps):
                if row['status'] in ('stopped', 'completed', 'waiting_for_human'):
                    break
                row = self._one_step(row)
            return self._summary(row)

    def resume(self, id):
        with self.lock:
            row = self._get(id)
            if row['status'] not in {'waiting_for_human', 'interrupted'}:
                raise ValueError('This session is not waiting for a manual resume.')
            row = self._one_step(row, after_human=True)
            return self._summary(row)

    def stop(self, id):
        with self.lock:
            row = self._get(id)
            if row['status'] == 'stopped':
                return self._summary(row)
            row = self._append(row, 'Research session stopped by operator.', status='stopped', blocker=None)
            if self.tor.browser.status().get('source_id') == row['source_id']:
                self.tor.browser.close()
            return self._summary(row)

    def dispatch(self, method, p):
        actions = {
            'tor.p3.list': lambda: self.list(p.get('project_id', '*')),
            'tor.p3.start': lambda: self.start(**p),
            'tor.p3.status': lambda: self.status(p['id']),
            'tor.p3.advance': lambda: self.advance(p['id'], p.get('steps', 1)),
            'tor.p3.resume': lambda: self.resume(p['id']),
            'tor.p3.stop': lambda: self.stop(p['id']),
        }
        if method not in actions:
            raise ValueError('Unknown P3 research browser operation.')
        return actions[method]()
