"""Bounded autonomous, read-only website audit through the existing policy gateway.

This worker does not invent payloads, use credentials, follow redirects, or change site
state. It only dispatches explicitly predeclared GET-safe project paths through the
normal queue + gateway + evidence machinery. No request is silently auto-approved.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .policy import canonical_url, classify_effect, validate_scope
from .store import now

BLOCKED_ROUTE = re.compile(
    r"(?:^|[/?._-])(logout|logoff|signout|sign-out|checkout|purchase|payment|pay|buy|buy-now|confirm|submit|commit|"
    r"order|refund|delete|remove|update|create|reset|unsubscribe|redeem|apply|"
    r"admin|login|register|password|token|callback|oauth|activate|disable|"
    r"cart|transfer|subscribe)(?:$|[/?._-])", re.I
)
SKIP_EXT = re.compile(r"\.(?:zip|tar|gz|exe|dmg|msi|pdf|docx?|xlsx?|jpg|jpeg|png|gif|svg|webp|ico|woff2?|ttf|mp[34]|avi|mov|css|js|map)(?:$|[?])", re.I)
MAX_HTML = 350_000
MAX_PAGES = 30
MAX_DEPTH = 3


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.forms: list[dict] = []
        self._form: dict | None = None
        self.password_fields = 0
        self.external_resources = 0
        self.resources = []
        self.meta_csp = False
        self.meta_referrer = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            if str(attrs.get('http-equiv', '')).lower() == 'content-security-policy' and attrs.get('content'):
                self.meta_csp = True
            if str(attrs.get('name', '')).lower() == 'referrer' and attrs.get('content'):
                self.meta_referrer = True
        if tag == 'script' and attrs.get('src'):
            self.resources.append({'url': attrs['src'], 'kind': 'script', 'integrity': bool(attrs.get('integrity'))})
        if tag == 'link' and attrs.get('href') and attrs.get('rel') in {'stylesheet', 'modulepreload', 'manifest'}:
            self.resources.append({'url': attrs['href'], 'kind': attrs['rel'], 'integrity': bool(attrs.get('integrity'))})
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        elif tag == 'form':
            self._form = {'method': str(attrs.get('method') or 'GET').upper()[:12],
                          'action': str(attrs.get('action') or '')[:500],
                          'fields': [], 'password': False}
        elif tag in {'input', 'select', 'textarea'} and self._form is not None:
            kind = str(attrs.get('type') or '').lower()
            # Never record form values, access tokens, hidden-field contents or cookies.
            if kind == 'password': self._form['password'] = True
            if len(self._form['fields']) < 20:
                self._form['fields'].append({'type': kind[:24], 'name': str(attrs.get('name') or '')[:70]})

    def handle_endtag(self, tag):
        if tag == 'form' and self._form is not None:
            if len(self.forms) < 20: self.forms.append(self._form)
            self._form = None


def eligible(project: dict, url: str) -> bool:
    """Fail closed on ambiguous paths and any request not explicitly marked read-only."""
    try:
        url = canonical_url(url)
        part = urlsplit(url)
        if part.query or part.fragment or BLOCKED_ROUTE.search(part.path) or SKIP_EXT.search(part.path):
            return False
        validate_scope(project, url, resolve=False)
        effect, _ = classify_effect(project, {'method': 'GET', 'url': url, 'headers': {}, 'body': ''})
        return effect == 'read_only'
    except (ValueError, TypeError, KeyError):
        return False


def public_resource_allowed(project: dict, seed: str, url: str, method: str = 'GET') -> bool:
    """Browser subresources must satisfy the same project rules as page GETs.

    Static file extensions are allowed here; unknown routes and origins are not.
    This is an extra restriction before the independently enforced gateway.
    """
    try:
        part, origin = urlsplit(canonical_url(url)), urlsplit(canonical_url(seed))
        if method != 'GET' or part.query or part.fragment or (part.scheme, part.netloc) != (origin.scheme, origin.netloc):
            return False
        if BLOCKED_ROUTE.search(part.path): return False
        validate_scope(project, url, resolve=False)
        effect, _ = classify_effect(project, {'method': 'GET', 'url': url, 'headers': {}, 'body': ''})
        return effect == 'read_only'
    except (ValueError, TypeError, KeyError):
        return False


def inspect_asset(url: str, response: dict) -> dict:
    """Static indicators in observed public bytes; no execution or URL probing."""
    body = str(response.get('body') or '')[:2 * 1024 * 1024]
    headers = {str(k).lower(): str(v) for k, v in (response.get('headers') or {}).items()}
    ctype = headers.get('content-type', '')
    indicators = {}
    if 'javascript' in ctype or urlsplit(url).path.endswith('.js'):
        for label, pattern in {
            'HTML insertion APIs': r'\b(?:innerHTML|outerHTML|insertAdjacentHTML|dangerouslySetInnerHTML)\b',
            'Dynamic code APIs': r'\beval\s*\(|\bnew\s+Function\s*\(',
            'Browser storage APIs': r'\b(?:localStorage|sessionStorage)\b',
            'Message listeners': r'addEventListener\s*\(\s*[\"\x27]message[\"\x27]',
        }.items():
            count = len(re.findall(pattern, body))
            if count: indicators[label] = count
    return {'path': urlsplit(url).path[:250], 'status': response.get('status'), 'content_type': ctype[:120],
            'captured_characters': len(body), 'truncated': bool(response.get('truncated')),
            'indicators': indicators, 'source_map_reference': bool(re.search(r'[#@]\s*sourceMappingURL=', body)),
            'assessment': 'Static indicators only. Library code may contain these APIs; no attacker-controlled data flow or exploit is established.'}


def inspect_page(url: str, response: dict) -> tuple[list[str], list[dict], dict]:
    """Analyze actual observed response metadata, not AI-inferred vulnerabilities."""
    headers = {str(k).lower(): str(v) for k, v in (response.get('headers') or {}).items()}
    status = int(response.get('status', 0))
    content_type = headers.get('content-type', '').lower()
    text = response.get('body') or ''
    parser = PageLinks()
    if 'text/html' in content_type and isinstance(text, str) and len(text) <= MAX_HTML:
        try: parser.feed(text)
        except (ValueError, AssertionError): pass
    observations = []
    if urlsplit(url).scheme == 'https' and 'strict-transport-security' not in headers:
        observations.append({'kind':'configuration', 'title':'HSTS header not observed', 'detail':'Check whether HSTS applies to this host and deployment.'})
    if 'text/html' in content_type and 'content-security-policy' not in headers and not parser.meta_csp:
        observations.append({'kind':'configuration', 'title':'Content Security Policy header not observed', 'detail':'Review whether a CSP is appropriate for this page.'})
    if 'text/html' in content_type and 'x-content-type-options' not in headers:
        observations.append({'kind':'configuration', 'title':'X-Content-Type-Options header not observed', 'detail':'Review MIME-sniffing protections.'})
    if 'text/html' in content_type and 'x-frame-options' not in headers and not re.search(r'(?:^|;)\s*frame-ancestors\s', headers.get('content-security-policy', ''), re.I):
        observations.append({'kind':'configuration', 'title':'Framing restriction not observed', 'detail':'Neither X-Frame-Options nor an enforced CSP frame-ancestors directive was observed. Framing impact has not been tested.'})
    if 'text/html' in content_type and 'referrer-policy' not in headers and not parser.meta_referrer:
        observations.append({'kind':'configuration', 'title':'Explicit Referrer-Policy not observed', 'detail':'Browser defaults apply; determine whether the application needs a stricter policy.'})
    if urlsplit(url).scheme == 'https':
        for form in parser.forms:
            action = urljoin(url, form['action'])
            if urlsplit(action).scheme == 'http':
                observations.append({'kind':'transport', 'title':'HTTPS page has an HTTP form action', 'detail':'Form action may transmit submitted data without TLS.'})
                break
    # Cookie attributes are only evaluated when the response explicitly supplies Set-Cookie.
    # HTTP libraries may coalesce repeated Set-Cookie headers; treat this as a lead only.
    cookie = headers.get('set-cookie', '')
    if cookie and 'secure' not in cookie.lower() and urlsplit(url).scheme == 'https':
        observations.append({'kind':'session', 'title':'Cookie without Secure attribute observed', 'detail':'Review whether the cookie is security sensitive.'})
    return parser.links[:120], observations[:10], {'status':status, 'forms':parser.forms[:12],
        'content_type':content_type[:120], 'size':len(text) if isinstance(text,str) else 0,
        'resources': [{'path': urlsplit(urljoin(url, r['url'])).path[:250],
                       'origin': urlsplit(urljoin(url, r['url'])).scheme+'://'+urlsplit(urljoin(url, r['url'])).netloc,
                       'kind': r['kind'], 'integrity': r['integrity']} for r in parser.resources[:60]],
        'truncated': bool(response.get('truncated'))}


class WebsiteAudit:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._mutex = threading.RLock()
        self._active: dict[str, threading.Event] = {}

    def _update(self, id, **changes):
        with self._mutex:
            previous = self.store.get('work_task', id)
            if previous: self.store.put('work_task', {**previous, **changes}, id=id)

    def _event(self, id, kind, text, **extras):
        with self._mutex:
            previous = self.store.get('work_task', id)
            if previous:
                events = (previous.get('events', []) + [{'time': now(), 'kind': kind, 'text': text[:400], **extras}])[-200:]
                self.store.put('work_task', {**previous, 'events': events}, id=id)

    def start(self, project_id: str, approved: bool = False, max_pages: int = 12, seed_url: str | None = None, render_public: bool = False):
        self.store.require_desktop_owner()
        if approved is not True:
            raise ValueError('Review and explicitly authorize the read-only audit first.')
        if self.store.setting('emergency_stop', False): raise ValueError('Emergency stop is active.')
        project = self.app.policy.project(project_id)
        if not project.get('scope') or not project.get('authorization','').strip():
            raise ValueError('Add an authorized website target to your project.')
        if not project.get('read_only_paths'):
            raise ValueError('Configure known read-only GET paths in Targets first. The audit will not guess which site actions are safe.')
        if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
            raise ValueError('Choose 1–30 pages.')
        if type(render_public) is not bool: raise ValueError('render_public must be boolean.')
        if render_public and project.get('lab_project'):
            raise ValueError('Public rendering requires a verified gateway project.')
        start_url = canonical_url(seed_url or project['scope'][0])
        if not eligible(project, start_url):
            raise ValueError('Start URL must be explicitly in-scope and classified read-only (and without query parameters).')
        # The policy gateway is the only network transport for real websites.
        if not project.get('lab_project'):
            gw = self.store.setting('gateway', {})
            if not gw.get('verified_at') or time.time() - gw['verified_at'] > 300:
                raise ValueError('Connect the built-in policy gateway (or verified Burp route) in Settings before running this audit.')
        with self._mutex:
            if any(self.store.get('work_task', tid) and self.store.get('work_task', tid).get('project_id') == project_id
                   for tid in self._active):
                raise ValueError('An automated website audit is already running in this project.')
            task = self.store.put('work_task', {
                'kind':'website_audit', 'project_id':project_id,
                'title':'Read-only website audit', 'status':'starting',
                'source':'policy_gateway_observed', 'events':[
                    {'time':now(), 'kind':'created', 'text':'Operator approved bounded read-only audit via policy gateway.'}],
                'result':{'pages':[], 'observations':[], 'links_discovered':0, 'findings':[], 'requests_sent':0, 'skipped':0},
            })
            cancel = threading.Event()
            self._active[task['id']] = cancel
            thread = threading.Thread(target=self._run, args=(task['id'], project_id, start_url, max_pages, cancel, render_public), daemon=True)
            thread.start()
        return self.app.tasks.describe(task)

    def cancel(self, id: str):
        with self._mutex:
            event = self._active.get(id)
            if event is None: raise ValueError('Website audit is not running.')
            event.set()
            self._event(id, 'notice', 'Stop requested. No further requests will be sent after the current request.')
        return self.app.tasks.describe(self.store.get('work_task', id))

    def _run(self, id: str, project_id: str, seed: str, max_pages: int, cancel: threading.Event, render_public=False):
        report = {'pages': [], 'observations': [], 'links_discovered':0, 'findings':[], 'requests_sent':0, 'skipped':0}
        queue = deque([(seed, 0)])
        visited = set()
        seen_issue_types = set()
        self._update(id, status='running')
        try:
            while queue and len(visited) < max_pages:
                if cancel.is_set() or self.store.setting('emergency_stop',False):
                    self._update(id, status='cancelled', result=report)
                    self._event(id, 'cancelled', 'Audit stopped before next request.')
                    return
                url, depth = queue.popleft()
                if url in visited: continue
                # Recheck scope and effect per request, including project edits and stop state.
                project = self.app.policy.project(project_id)
                if not eligible(project,url):
                    report['skipped'] += 1
                    continue
                visited.add(url)
                self._event(id, 'tool_call', 'Read-only GET: ' + urlsplit(url).path[:160])
                # Gateway independently validates DNS, scope, action integrity, timeouts and budgets.
                action = self.app.policy.queue(project_id, {'method':'GET', 'url':url, 'headers':{'accept':'text/html,application/xhtml+xml'}, 'body':''},
                                               session_id='website-audit-'+id[:12])
                if action.get('status') != 'ready':
                    # Do not create orphaned approval requests or escalate uncertain operations.
                    self.app.policy.cancel(action['id'])
                    report['skipped'] += 1
                    continue
                result = self.app.policy.execute(action['id'])
                report['requests_sent'] += 1
                if result.get('status') != 'completed' or not result.get('evidence_ids'):
                    self._event(id, 'error', 'Request did not produce verified evidence; audit stopped.')
                    raise RuntimeError('Gateway request did not complete with trustworthy evidence.')
                response = result.get('response') or {}
                urls, observations, page = inspect_page(url, response)
                page.update({'path':urlsplit(url).path[:250], 'evidence_ids':result['evidence_ids'], 'action_id':action['id']})
                report['pages'].append(page)
                self._event(id, 'tool_result', f"HTTP {page['status']} · {page['path']}", evidence_id=result['evidence_ids'][0])
                for issue in observations:
                    issue.update({'path':page['path'], 'evidence_ids':result['evidence_ids']})
                    report['observations'].append(issue)
                    # One review card per issue type; keep all affected pages in the report.
                    signature = (issue['kind'], issue['title'])
                    if signature in seen_issue_types: continue
                    seen_issue_types.add(signature)
                    # Heuristics are always hypotheses, never automatically confirmed vulnerabilities.
                    finding = self.app.reports.save({
                        'project_id':project_id, 'title':'REVIEW: '+issue['title'], 'status':'hypothesis',
                        'summary':issue['detail']+' Observed at '+page['path'],
                        'impact':'Potential configuration issue; security impact not established.',
                        'steps':['Inspect the saved gateway response.', 'Check application requirements and relevant controls.'],
                        'evidence_ids':result['evidence_ids'], 'reviewed':False,
                    }, agent=True)
                    report['findings'].append(finding['id'])
                if depth < MAX_DEPTH:
                    for href in urls:
                        if len(href) > 1000 or href.startswith(('#','javascript:','mailto:','tel:','data:')):
                            report['skipped'] += 1
                            continue
                        try: next_url = canonical_url(urljoin(url, href))
                        except ValueError:
                            report['skipped'] += 1
                            continue
                        if next_url not in visited and eligible(project, next_url) and len(queue) < 100:
                            queue.append((next_url,depth+1))
                            report['links_discovered'] += 1
                self._update(id, result=report)
            if render_public and not cancel.is_set() and not self.store.setting('emergency_stop', False):
                self._event(id, 'tool_call', 'Render the public start page with a fresh browser session; at most 30 declared same-origin GET requests.')
                session = 'website-audit-' + id
                try:
                    report['public_render'] = self.app.tools.browser(project_id, seed, session_id=session, public_only=True, cancel_event=cancel)
                except Exception as exc:
                    report['render_error'] = type(exc).__name__ + ': ' + str(exc)[:220]
                    self._event(id, 'error', 'Public rendering incomplete. See saved HTTP evidence and render error.')
                receipts = [row for row in self.store.list('traffic', project_id=project_id)
                            if row.get('session_id') == 'public-review:' + session and row.get('trusted_observation')]
                report['requests_sent'] += len(receipts)
                report['assets'] = []
                for row in receipts:
                    request_url = row.get('request', {}).get('url', '')
                    if request_url == seed: continue
                    asset = inspect_asset(request_url, row.get('response') or {})
                    asset['evidence_ids'] = [row['id']]
                    report['assets'].append(asset)
            self._update(id, result=report, status='cancelled' if cancel.is_set() else 'completed')
            self._event(id,'completed',f"Observed {len(report['pages'])} pages; created {len(report['findings'])} review-only hypotheses.")
        except Exception as exc:
            self._update(id, status='cancelled' if cancel.is_set() else 'failed', result=report,
                         error=('Interrupted after the latest request.' if cancel.is_set() else type(exc).__name__ + ': ' + str(exc)[:220]))
            self._event(id, 'error', 'Audit ended without retrying any uncertain requests.')
        finally:
            with self._mutex: self._active.pop(id, None)

    def report(self, id: str):
        """One reviewable report; no replay and no secrets from responses."""
        task = self.store.get('work_task', id)
        if not task or task.get('kind') != 'website_audit':
            raise ValueError('Website audit not found.')
        project = self.app.policy.project(task['project_id'])
        result = task.get('result', {})
        lines = [f"# Website audit — {project['name']}", '',
                 f"Task: `{task['id']}`", f"Status: **{task.get('status','unknown')}**", '',
                 '## Scope and execution',
                 'Only explicitly marked read-only GET paths are examined via the verified policy gateway.',
                 f"Pages observed: {len(result.get('pages', []))}",
                 f"Requests observed: {result.get('requests_sent', 0)}",
                 f"Links skipped: {result.get('skipped', 0)}", '',
                 '## Observed pages', '']
        for page in result.get('pages', []):
            path = str(page.get('path',''))[:250].replace('`','')
            refs = ', '.join(str(v) for v in page.get('evidence_ids', [])[:10])
            lines.append(f"- HTTP {page.get('status', '?')} `{path}` — evidence: `{refs}`")
            for form in page.get('forms', [])[:12]:
                method = str(form.get('method','GET'))[:12]
                lines.append(f"  - Form observed: {method} (not submitted)")
        lines += ['', '## Leads needing review', '']
        for item in result.get('observations', []):
            title = str(item.get('title','Review finding')).replace('\n',' ')[:120]
            path = str(item.get('path','')).replace('`','')[:250]
            lines.append(f"- {title} on `{path}` — **unconfirmed**; inspect evidence and server-side controls.")
        if not result.get('observations'):
            lines.append('No configuration leads detected. This does not establish that the site is secure.')
        if result.get('public_render'):
            render = result['public_render']
            lines += ['', '## Public browser snapshot', str(render.get('message', '')),
                      'Title: ' + str(render.get('title', '')),
                      'Coverage: ' + str(render.get('coverage', 'partial')),
                      'Screenshot: ' + str(render.get('screenshot', '')),
                      'Blocked resource requests: ' + str(len(render.get('blocked_requests', []))),
                      'Failed/incomplete requests: ' + str(len(render.get('failed_requests', []))),
                      'Gateway evidence: ' + ', '.join(render.get('evidence_ids', [])),
                      '', 'Visible text:', str(render.get('text', ''))]
            for request in render.get('blocked_requests', []):
                lines.append(f"- Blocked {request['method']} {request['origin']}{request['path']} ({request['type']})")
            for request in render.get('failed_requests', []):
                lines.append(f"- Incomplete `{request.get('path', '')}`: {request.get('error', 'No completed receipt')}")
        if result.get('render_error'):
            lines += ['', '## Incomplete public rendering', result['render_error']]
        if result.get('assets'):
            lines += ['', '## Public assets observed', 'Static API counts are review aids, not vulnerabilities. No source-map URLs or inferred endpoints were probed.']
            for asset in result['assets']:
                lines.append(f"- HTTP {asset['status']} `{asset['path']}` — captured {asset['captured_characters']} characters; truncated: {asset['truncated']}; indicators: {asset['indicators']}; evidence: {', '.join(asset['evidence_ids'])}")
        lines += ['', '## Limitations',
                  'This is a read-only surface review, not an authenticated penetration test.',
                  'Checkout, cart mutations, payments, unknown GET paths, redirects, queries and active exploit probes are not executed.',
                  'Optional public rendering uses a fresh session and only declared same-origin GET paths. Blocked dependencies can leave the page incomplete. No exploitability is proven.',
                  'No interrupted/failed request is automatically retried.']
        if task.get('error'):
            lines += ['', '## Incomplete run', str(task['error'])[:250]]
        return {'filename': f"website-audit-{id[-8:]}.md", 'content':'\n'.join(lines)+'\n'}

    def dispatch(self, method, params):
        if method == 'website.audit.report': return self.report(params['id'])
        if method == 'website.audit.start': return self.start(**params)
        if method == 'website.audit.stop': return self.cancel(params['id'])
        raise ValueError('Unknown website audit operation.')
