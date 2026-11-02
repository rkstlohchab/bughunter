"""Scoped automation of an installed Tor Browser using its own Firefox executable.

A running local Tor SOCKS5 service, geckodriver, and optional Selenium are required.
The WebDriver instance uses a copy of the Tor Browser profile, not the live profile.
Only bookmarked origins are navigable, and page content is untrusted evidence.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

from .tor_research import MAX_TEXT, PageText, _origin, validated_url

MAX_LINKS = 80
PAGE_TIMEOUT = 35


def installed_paths(binary_path: str):
    if not isinstance(binary_path, str) or not binary_path.strip() or len(binary_path) > 2048:
        raise ValueError("Select the Tor Browser executable, not an ordinary Firefox binary.")
    binary = Path(binary_path).expanduser().resolve()
    if not binary.is_file() or binary.name.lower() not in {'firefox', 'firefox.exe', 'firefox-bin'}:
        raise ValueError("Tor Browser executable was not found. Select firefox or firefox.exe inside Tor Browser.")
    # Tor Browser's bundled profile includes its own privacy extensions and preferences.
    candidates = [
        binary.parent / 'TorBrowser' / 'Data' / 'Browser' / 'profile.default',
        binary.parent.parent / 'Resources' / 'TorBrowser' / 'Data' / 'Browser' / 'profile.default',
        binary.parent.parent / 'TorBrowser' / 'Data' / 'Browser' / 'profile.default',
    ]
    profile = next((p for p in candidates if p.is_dir()), None)
    if not profile:
        raise ValueError("A Tor Browser profile.default directory was not found beside this executable. Ordinary Firefox is not supported.")
    return binary, profile


def _webdriver_factory(binary: Path, profile: Path, port: int, driver_path: str, allow_scripts: bool):
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
    except ImportError as exc:
        raise ValueError("Selenium is not installed. Install the backend's browser extra: pip install -e './backend[browser]'.") from exc
    if driver_path and not Path(driver_path).expanduser().is_file():
        raise ValueError("The selected geckodriver executable was not found.")
    if not driver_path and not shutil.which('geckodriver'):
        raise ValueError("geckodriver is not installed. Put it on PATH or enter its full path.")
    options = Options()
    options.binary_location = str(binary)
    # Preserve installed Tor Browser hardening while dropping personal site state.
    # geckodriver/Selenium clones the sanitized profile again for the session.
    with tempfile.TemporaryDirectory(prefix='bughunter-tor-profile-') as work:
        clean = Path(work) / 'profile'
        shutil.copytree(profile, clean, ignore=shutil.ignore_patterns(
            'cookies.sqlite*', 'places.sqlite*', 'formhistory.sqlite*',
            'logins.json', 'sessionstore*', 'webappsstore.sqlite*',
            'storage', 'cache2', 'startupCache', 'bookmarkbackups',
            'parent.lock', '.parentlock', 'lock', 'jumpListCache', 'thumbnails'))
        options.profile = str(clean)
        return _start_firefox(options, port, driver_path, allow_scripts)


def _start_firefox(options, port, driver_path, allow_scripts):
    from selenium import webdriver
    from selenium.webdriver.firefox.service import Service
    prefs = {
        'network.proxy.type': 1,
        'network.proxy.socks': '127.0.0.1',
        'network.proxy.socks_port': port,
        'network.proxy.socks_version': 5,
        'network.proxy.socks_remote_dns': True,
        'network.proxy.no_proxies_on': '',
        'network.proxy.failover_direct': False,
        'network.trr.mode': 5,
        'network.dns.disablePrefetch': True,
        'network.prefetch-next': False,
        'network.http.speculative-parallel-limit': 0,
        'media.peerconnection.enabled': False,
        'javascript.enabled': allow_scripts,
    }
    for key, value in prefs.items():
        options.set_preference(key, value)
    env = {**os.environ, 'TOR_PROVIDER': 'none', 'TOR_SOCKS_PORT': str(port)}
    service = Service(executable_path=str(Path(driver_path).expanduser().resolve()) if driver_path else 'geckodriver', env=env)
    driver = webdriver.Firefox(service=service, options=options)
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    return driver


class TorBrowserController:
    def __init__(self, service, factory=None):
        self.service = service
        self.factory = factory or _webdriver_factory
        self.lock = threading.RLock()
        self.driver = None
        self.source_id = None
        self.links = []

    def config(self):
        return {'binary_path': self.service.store.setting('tor_browser_binary_path', ''),
                'driver_path': self.service.store.setting('tor_browser_driver_path', ''),
                'allow_scripts': self.service.store.setting('tor_browser_allow_scripts', False)}

    def configure(self, binary_path: str, driver_path: str = '', allow_scripts: bool = False):
        if not isinstance(allow_scripts, bool):
            raise ValueError('JavaScript setting must be a boolean.')
        if not isinstance(driver_path, str) or len(driver_path) > 2048:
            raise ValueError('Invalid geckodriver path.')
        with self.lock:
            if self.driver is not None:
                raise ValueError('Close the browser session before changing its configuration.')
            binary, _ = installed_paths(binary_path)
            if driver_path and not Path(driver_path).expanduser().is_file():
                raise ValueError('The specified geckodriver executable does not exist.')
            self.service.store.set_setting('tor_browser_binary_path', str(binary))
            self.service.store.set_setting('tor_browser_driver_path', driver_path.strip())
            self.service.store.set_setting('tor_browser_allow_scripts', allow_scripts)
            return self.config()

    def status(self):
        with self.lock:
            if self.driver is not None:
                try:
                    url = self.driver.current_url
                except Exception:
                    self.close()
                    url = None
            else:
                url = None
            return {'running': self.driver is not None, 'source_id': self.source_id,
                    'url': url, 'links': list(self.links),
                    'configured': bool(self.config()['binary_path'])}

    def _origin_guard(self, source_id, url):
        source = self.service.store.get('tor_source', source_id)
        if not source:
            raise ValueError('Choose an existing research bookmark.')
        destination = validated_url(urldefrag(url).url)
        if _origin(destination) != _origin(validated_url(source['url'])):
            raise ValueError('Navigation is restricted to the bookmarked origin. Add another source to visit other sites.')
        return destination

    def open(self, source_id: str):
        with self.lock:
            source = self.service.store.get('tor_source', source_id)
            if not source: raise ValueError('Choose an existing research bookmark.')
            url = self._origin_guard(source_id, source['url'])
            if self.driver is None:
                status = self.service.status()
                if not status['connected']:
                    raise ValueError('Start a local Tor SOCKS5 service and check its port first. No direct fallback is available.')
                config = self.config()
                binary, profile = installed_paths(config['binary_path'])
                self.driver = self.factory(binary, profile, self.service.settings()['socks_port'], config['driver_path'], config['allow_scripts'])
            self.source_id = source_id
            return self._navigate(url)

    def _navigate(self, url: str):
        if self.driver is None or not self.source_id:
            raise ValueError('Start a browser session from a saved source first.')
        self._origin_guard(self.source_id, url)
        try:
            self.driver.get(url)
            current = self._origin_guard(self.source_id, self.driver.current_url)
        except Exception:
            # Do not leave the browser on a site reached via cross-origin redirect.
            try:
                self.driver.get('about:blank')
            except Exception:
                pass
            self.links = []
            raise
        return self._read(current)

    def _read(self, current_url):
        parser = PageText()
        # page_source reflects the rendered DOM, including dynamically added text.
        html = self.driver.page_source
        if not isinstance(html, str): html = ''
        if len(html) > 2_000_000: html = html[:2_000_000]
        parser.feed(html)
        text = ' '.join(''.join(parser.text).split())[:MAX_TEXT]
        links, seen = [], set()
        for href in parser.links[:1000]:
            try:
                target = self._origin_guard(self.source_id, urljoin(current_url, href))
            except (ValueError, TypeError):
                continue
            if target not in seen:
                links.append({'index': len(links), 'url': target})
                seen.add(target)
            if len(links) >= MAX_LINKS:
                break
        self.links = links
        title = str(self.driver.title or parser.title or current_url)[:300]
        return {'source_id': self.source_id, 'url': current_url, 'title': title,
                'excerpt': text[:12000], 'text_length': len(text), 'links': links,
                'note': 'Untrusted rendered page text; automation does not submit forms or download files. Enabled page scripts may make their own requests.'}

    def read_current(self):
        """Observe the current visible browser state after a human challenge."""
        with self.lock:
            if not self.driver or not self.source_id:
                raise ValueError('Open a saved research source first.')
            current = self._origin_guard(self.source_id, self.driver.current_url)
            return self._read(current)

    def follow(self, index: int):
        with self.lock:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.links):
                raise ValueError('Select a link index from the current page.')
            return self._navigate(self.links[index]['url'])

    def navigate(self, url: str):
        with self.lock:
            if not self.driver or not self.source_id:
                raise ValueError('Start a browser session first.')
            return self._navigate(self._origin_guard(self.source_id, url))

    def capture(self):
        with self.lock:
            if not self.driver or not self.source_id:
                raise ValueError('Start a browser session first.')
            current = self._origin_guard(self.source_id, self.driver.current_url)
            page = self._read(current)
            # Full text is extracted afresh, not only the agent's 12,000-character preview.
            parser = PageText()
            parser.feed(str(self.driver.page_source)[:2_000_000])
            complete = ' '.join(''.join(parser.text).split())[:MAX_TEXT]
            record = self.service.record_page(self.source_id, {'url': current, 'title': page['title'], 'text': complete})
            return {'id': record['id'], 'url': current, 'title': record['title'],
                    'text_length': len(record['text']), 'reviewed': record['reviewed'], 'source_id': self.source_id}

    def close(self):
        with self.lock:
            driver = self.driver
            self.driver = None
            self.source_id = None
            self.links = []
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            return {'closed': True}
