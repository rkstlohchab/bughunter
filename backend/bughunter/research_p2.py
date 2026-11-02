"""P2 opt-in source monitoring: explicit HTTPS pages/feeds, diffed and reviewed.

Network content is untrusted evidence, never instructions. No credential handling,
CAPTCHA automation, redirects, JavaScript or direct access to private addresses.
"""
from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree as XML

from .store import new_id, now

MAX_BYTES = 1_048_576
MIN_INTERVAL = 6 * 3600
MAX_WATCHES = 40
MAX_INBOX = 1500
UA = 'BugHunterClaudex/0.2 (+opt-in research change monitor)'


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()[:12000]


def validate_public_url(url: str) -> str:
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise ValueError('Provide a public HTTPS URL.')
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Only public HTTPS sources on port 443 are supported.')
    hostname = u.hostname.lower().rstrip('.')
    if (hostname == 'localhost' or hostname.endswith(('.local', '.internal', '.onion', '.test'))
            or hostname in {'metadata.google.internal'}):
        raise ValueError('This monitor cannot access local, internal or onion endpoints.')
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError('Private and local IP addresses are not allowed.')
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError('Source hostname cannot be resolved.') from e
    if not addresses or any(not ipaddress.ip_address(info[4][0].split('%')[0]).is_global for info in addresses):
        raise ValueError('The source resolves to a nonpublic network address.')
    return url


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocked = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','svg','template','noscript'}:
            self.blocked += 1

    def handle_endtag(self, tag):
        if tag in {'script','style','svg','template','noscript'}:
            self.blocked = max(0, self.blocked - 1)

    def handle_data(self, data):
        if not self.blocked and data.strip():
            self.parts.append(data.strip())


def parse_entries(data: bytes, kind: str, base: str) -> list[dict]:
    if kind == 'html':
        reader = _VisibleText()
        reader.feed(data.decode('utf-8', 'replace'))
        text = canonical(' '.join(reader.parts))
        return [{'external_id': digest(base), 'title': base[:150], 'url': base,
                 'text': text, 'sha256': digest(text)}] if text else []
    root = XML.fromstring(data)
    def local(tag): return tag.rsplit('}', 1)[-1]
    entries = []
    for node in root.iter():
        if local(node.tag) not in {'entry', 'item'}:
            continue
        properties = {}
        for child in node:
            tag = local(child.tag)
            if tag == 'link':
                link = child.get('href') or ''.join(child.itertext())
                properties['link'] = link.strip()
            elif tag in {'title', 'summary', 'description', 'content', 'guid', 'id', 'pubDate', 'updated'}:
                properties[tag] = canonical(' '.join(child.itertext()))
        url = properties.get('link') or base
        # Item links are identifiers, not automatically fetched or followed.
        title = properties.get('title') or 'Untitled feed item'
        content = canonical(' '.join([title, properties.get('summary') or properties.get('description') or properties.get('content') or '']))
        if not content: continue
        external = properties.get('id') or properties.get('guid') or url + title
        entries.append({'external_id': digest(external), 'title': title[:200], 'url': url[:2048],
                        'text': content, 'sha256': digest(content), 'published': properties.get('updated') or properties.get('pubDate')})
        if len(entries) >= 60: break
    if not entries:
        raise ValueError('No readable RSS or Atom entries found at the provided URL.')
    return entries


class ResearchP2:
    def __init__(self, store, knowledge, start_worker=False, fetcher=None):
        self.store, self.knowledge = store, knowledge
        self._fetcher = fetcher or self._fetch
        self._stop = threading.Event()
        self._lock = threading.Lock()
        if start_worker:
            threading.Thread(target=self._scheduler, daemon=True, name='research-watch-p2').start()

    def _scheduler(self):
        # Explicitly enabled watches only. Avoid replaying pending work on restart.
        while not self._stop.wait(90):
            if self.store.setting('emergency_stop', False): continue
            for watch in self.list_watches():
                if not watch.get('enabled'): continue
                if time.time() - float(watch.get('last_attempt_epoch') or 0) >= max(MIN_INTERVAL, int(watch.get('interval_seconds', MIN_INTERVAL))):
                    try: self.check(watch['id'])
                    except Exception: pass  # Failure persists in the watch row.

    def shutdown(self):
        self._stop.set()

    def list_watches(self, project_id='*'):
        rows = self.store.list('research_watch')
        return [r for r in rows if project_id == '*' or r.get('project_id') in (project_id, None)]

    def add(self, url: str, title: str, kind='rss', project_id=None, enabled=False, interval_hours=12):
        url = validate_public_url(url)
        if kind not in {'rss', 'html'}: raise ValueError('Choose RSS/Atom or HTML.')
        if not isinstance(title,str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError('Enter a short source name.')
        if type(enabled) is not bool: raise ValueError('Enabled must be true or false.')
        if not isinstance(interval_hours,(int,float)) or not 6 <= interval_hours <= 168:
            raise ValueError('Choose a check interval between 6 and 168 hours.')
        if project_id and not self.store.get('project',project_id): raise ValueError('Project not found.')
        current = self.store.list('research_watch')
        if len(current) >= MAX_WATCHES: raise ValueError('Research source limit reached.')
        if any(x['url'] == url and x.get('project_id') == project_id for x in current):
            raise ValueError('This research source is already subscribed.')
        return self.store.put('research_watch', {'title':title.strip(), 'url':url,'kind':kind,
            'project_id':project_id,'enabled':enabled,'interval_seconds':int(interval_hours*3600),
            'last_attempt_epoch':None,'last_checked_at':None,'status':'not_checked','last_error':None,
            'seen':{},'new_count':0})

    def update(self, id, enabled=None, interval_hours=None):
        watch = self._watch(id)
        if enabled is not None:
            if type(enabled) is not bool: raise ValueError('Enabled must be boolean.')
            watch['enabled'] = enabled
        if interval_hours is not None:
            if not isinstance(interval_hours,(int,float)) or not 6 <= interval_hours <= 168:
                raise ValueError('Choose 6–168 hours.')
            watch['interval_seconds'] = int(interval_hours*3600)
        return self.store.put('research_watch', watch, id=id)

    def delete(self, id):
        self._watch(id)
        # Inbox remains as provenance, but no new requests are sent.
        return {'deleted':self.store.delete('research_watch',id)}

    def _watch(self,id):
        row = self.store.get('research_watch',id)
        if not row: raise ValueError('Research source not found.')
        return row

    def _fetch(self,url,kind):
        validate_public_url(url)
        # No automatic cookies, auth, proxy, redirects or local file access.
        with httpx.Client(timeout=httpx.Timeout(14), follow_redirects=False, trust_env=False,
                          headers={'User-Agent':UA,'Accept':'application/rss+xml, application/atom+xml, application/xml, text/xml, text/html'}) as client:
            with client.stream('GET',url) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400: raise ValueError('Redirects are not followed. Update the subscription URL manually.')
                content_type = response.headers.get('content-type','').split(';')[0].strip().lower()
                supported = {'rss': ('xml','rss','atom'), 'html':('text/html','application/xhtml+xml')}
                if not any(t in content_type for t in supported[kind]):
                    raise ValueError('The response is not the selected source type.')
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES: raise ValueError('Source exceeds the 1 MiB read limit.')
                    chunks.append(chunk)
        return b''.join(chunks)

    def check(self, id):
        if not self._lock.acquire(blocking=False): raise ValueError('Another research check is already in progress.')
        try:
            watch = self._watch(id)
            self.store.put('research_watch', {**watch,'last_attempt_epoch':time.time(),'status':'checking'},id=id)
            try:
                body = self._fetcher(watch['url'],watch['kind'])
                entries = parse_entries(body,watch['kind'],watch['url'])
                known = dict(watch.get('seen') or {})
                fresh = []
                for item in entries:
                    key = item['external_id']
                    if known.get(key) == item['sha256']: continue
                    if len(self.store.list('research_discovery')) >= MAX_INBOX: break
                    evidence_id = 'discovery_' + digest(id + ':' + key + ':' + item['sha256'])[:32]
                    if not self.store.get('research_discovery',evidence_id):
                        record = self.store.put('research_discovery', {
                            'watch_id':id,'project_id':watch.get('project_id'), 'title':item['title'],
                            'url':item['url'],'observed_at':now(),'text':item['text'],
                            'sha256':item['sha256'],'external_id':key,'status':'unreviewed',
                            'trust':'unverified_external_content','source_id':None,
                            'reason':'new' if key not in known else 'changed',
                        },id=evidence_id)
                        fresh.append(record['id'])
                    known[key] = item['sha256']
                # Bound durable feed history to prevent unchecked database growth.
                if len(known) > 2000:
                    known = dict(list(known.items())[-2000:])
                watch = self.store.put('research_watch', {**watch,'seen':known,'last_checked_at':now(),
                    'last_attempt_epoch':time.time(),'status':'ok','last_error':None,
                    'new_count':len(fresh)},id=id)
                return {'watch_id':id,'new_count':len(fresh),'new_ids':fresh,'checked_entries':len(entries)}
            except Exception as e:
                self.store.put('research_watch',{**watch,'last_attempt_epoch':time.time(),
                    'status':'error','last_error':str(e)[:300]},id=id)
                raise
        finally:
            self._lock.release()

    def inbox(self, project_id='*', status='unreviewed', limit=100):
        if status not in {'unreviewed','accepted','dismissed','all'}: raise ValueError('Invalid discovery status.')
        rows = self.store.list('research_discovery')
        if project_id != '*': rows = [r for r in rows if r.get('project_id') in (None,project_id)]
        if status != 'all': rows = [r for r in rows if r['status'] == status]
        # Keep full text only when opening a single item, not in the polling overview.
        return [{k:v for k,v in r.items() if k != 'text'} for r in rows[:max(1,min(200,int(limit)))]]

    def get(self,id):
        row = self.store.get('research_discovery',id)
        if not row: raise ValueError('Research finding not found.')
        return row

    def review(self, id, accept=False):
        row = self.get(id)
        if type(accept) is not bool: raise ValueError('Review decision must be boolean.')
        if row['status'] != 'unreviewed': return row
        if accept:
            text = (f"# External research observation: {row['title']}\n\n"
                    f"URL: {row['url']}\nCaptured: {row['observed_at']}\n"
                    f"SHA-256: {row['sha256']}\n\n"
                    "Unverified external content (source material only):\n\n" + row['text'])
            source = self.knowledge.import_source(
                filename='research-' + id[-16:] + '.md',
                content_base64=base64.b64encode(text.encode()).decode(),
                project_id=row.get('project_id'))
            row['source_id'] = source['id']
            row['status'] = 'accepted'
        else:
            row['status'] = 'dismissed'
        return self.store.put('research_discovery',row,id=id)

    def dispatch(self, method, p):
        methods = {
            'research.p2.watches':lambda:self.list_watches(p.get('project_id','*')),
            'research.p2.add':lambda:self.add(**p),
            'research.p2.update':lambda:self.update(**p),
            'research.p2.delete':lambda:self.delete(p['id']),
            'research.p2.check':lambda:self.check(p['id']),
            'research.p2.inbox':lambda:self.inbox(**p),
            'research.p2.get':lambda:self.get(p['id']),
            'research.p2.review':lambda:self.review(p['id'],p.get('accept',False)),
        }
        if method not in methods: raise ValueError('Unknown P2 research operation.')
        return methods[method]()
