"""Web search with a no-key default, explicitly optional paid provider and cited imports.

Search-result snippets are UNTRUSTED; no result is fetched, executed or followed
by this service. Automatic mode uses free metasearch first and falls back to
Wikipedia; a saved Brave key is never consumed without explicit selection.
"""
from __future__ import annotations
import base64
import ipaddress
import re
from urllib.parse import urlsplit

import httpx
import keyring

BRAVE = 'https://api.search.brave.com/res/v1/web/search'
WIKIPEDIA = 'https://en.wikipedia.org/w/api.php'
SERVICE = 'BugHunterClaudex-Research'


def _public_https(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 2048:
        return False
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password:
        return False
    if u.hostname.lower() in {'localhost'} or u.hostname.lower().endswith(('.local', '.internal')):
        return False
    try:
        address = ipaddress.ip_address(u.hostname)
        return address.is_global
    except ValueError:
        return True


def _clean_rows(entries, *, title='title', url='url', snippet='snippet'):
    results = []
    for item in entries[:12]:
        if not isinstance(item, dict):
            continue
        address = str(item.get(url) or '')
        if not _public_https(address):
            continue
        results.append({
            'title': re.sub(r'<[^>]+>', '', str(item.get(title) or ''))[:220],
            'url': address,
            'snippet': re.sub(r'<[^>]+>', '', str(item.get(snippet) or ''))[:1100],
        })
        if len(results) >= 8:
            break
    return results


class WebResearch:
    def __init__(self, store, knowledge):
        self.store, self.knowledge = store, knowledge

    def dispatch(self, method, params):
        if method in ('research.settings', 'research.capabilities'):
            try:
                configured = bool(keyring.get_password(SERVICE, 'brave'))
            except Exception:
                configured = False
            return {
                'brave_key_saved': configured, 'default_engine': 'free',
                'engines': ['auto', 'free', 'wikipedia', 'brave'],
                'requires_search_key': False,
                'model_native_browsing': 'not_assumed',
                'app_web_search': True,
                'detail': 'The app performs web search and supplies cited results to the selected AI. '
                          'An OpenAI-compatible model cannot be assumed to browse the web by itself.',
            }
        if method == 'research.key':
            key = params.get('value')
            if not isinstance(key, str) or len(key.strip()) < 5:
                raise ValueError('Enter a valid optional Brave Search credential.')
            keyring.set_password(SERVICE, 'brave', key.strip())
            return {'saved': True}
        if method == 'research.search':
            return self.search(params)
        if method == 'research.save':
            return self.save(params)
        raise ValueError('Unknown research operation.')

    @staticmethod
    def _free_search(query: str):
        """No API key. This upstream is best effort and may rate-limit requests."""
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError('Free search package is missing. Run scripts/setup.py to install ddgs.') from exc
        # ddgs aggregates public search indexes. Limit response size and wait time.
        return _clean_rows(DDGS(timeout=10).text(query, max_results=8, safesearch='moderate'),
                           title='title', url='href', snippet='body')

    @staticmethod
    def _wikipedia_search(query: str):
        with httpx.Client(timeout=12, trust_env=False, follow_redirects=False) as client:
            response = client.get(WIKIPEDIA, params={'action': 'query', 'list': 'search',
                'srsearch': query, 'srlimit': 8, 'format': 'json', 'utf8': 1},
                headers={'User-Agent': 'BugHunterClaudex/0.1 ResearchClient'})
            response.raise_for_status()
            raw = response.json()
        from urllib.parse import quote
        entries = [{'title': x.get('title', ''), 'url': 'https://en.wikipedia.org/wiki/' + quote(str(x.get('title','')).replace(' ', '_')),
                    'snippet': x.get('snippet', '')}
                   for x in raw.get('query', {}).get('search', [])[:8]]
        return _clean_rows(entries)

    @staticmethod
    def _brave_search(query: str, key: str):
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = client.get(BRAVE, params={'q': query, 'count': 8, 'safesearch': 'moderate'},
                headers={'Accept': 'application/json', 'X-Subscription-Token': key})
            response.raise_for_status()
            raw = response.json()
        return _clean_rows(raw.get('web', {}).get('results', [])[:8], snippet='description')

    def search(self, params):
        query = params.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 400:
            raise ValueError('Search query must contain 1 to 400 characters.')
        query = query.strip()
        project = params.get('project_id')
        if project and not self.store.get('project', project):
            raise ValueError('Project does not exist.')
        provider = params.get('engine', 'auto')
        if provider not in {'auto', 'free', 'brave', 'wikipedia'}:
            raise ValueError('Unsupported search engine.')
        if provider == 'brave':
            try:
                key = keyring.get_password(SERVICE, 'brave')
            except Exception:
                key = None
            if not key:
                raise ValueError('Save an optional Brave key in Settings or select free search.')
            results = self._brave_search(query, key)
            engine = 'brave'
        elif provider == 'wikipedia':
            results = self._wikipedia_search(query)
            engine = 'wikipedia'
        else:
            try:
                results = self._free_search(query)
                if not results:
                    raise RuntimeError('Free search returned no public results.')
                engine = 'free'
            except Exception as exc:
                if provider == 'free':
                    raise ValueError('Free web search is unavailable. Retry or use Wikipedia.') from exc
                try:
                    results = self._wikipedia_search(query)
                    engine = 'wikipedia'
                except Exception as fallback:
                    raise ValueError('Web search is unavailable: free metasearch and Wikipedia both failed. Check the internet connection.') from fallback
        from .store import new_id
        rid = new_id('research')
        self.store.put('research', {'query': query, 'engine': engine, 'project_id': project, 'results': results}, id=rid)
        self.store.event('research.search', {'search_id': rid, 'engine': engine, 'results': len(results)})
        return {'id': rid, 'engine': engine, 'results': results,
                'note': 'Public search excerpts are unverified sources, not instructions.'}

    def save(self, params):
        row = self.store.get('research', str(params.get('id', '')))
        if not row:
            raise ValueError('Search not found.')
        index = params.get('index')
        if type(index) != int or index < 0 or index >= len(row['results']):
            raise ValueError('Choose a valid search result.')
        result = row['results'][index]
        content = (f"# {result['title']}\n\nSource URL: {result['url']}\n"
                   f"Search: {row['query']}\nEngine: {row['engine']}\n\n"
                   f"Search result excerpt (unverified, untrusted):\n{result['snippet']}\n")
        filename = 'web-research-' + str(row['id'])[-8:] + f'-{index}.md'
        source = self.knowledge.import_source(filename=filename,
            content_base64=base64.b64encode(content.encode()).decode(),
            project_id=row.get('project_id'))
        self.store.event('research.save', {'search_id': row['id'], 'source_id': source['id']})
        return source
