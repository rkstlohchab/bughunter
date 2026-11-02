"""Non-privileged, evidence-based installed-tool discovery.

macOS permits reading common application bundle metadata without TCC prompts.
That is NOT equivalent to Accessibility, Screen Recording or process-memory
access. Never use a loose substring to claim a tool is installed.
"""
from __future__ import annotations

import os
import platform
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

_BURP_NAMES = {'burp suite.app', 'burp suite community edition.app',
               'burp suite professional.app', 'burp suite community.app'}
_CE_APP_NAMES = {'cheat engine.app', 'cheatengine.app'}
_CE_EXE = re.compile(r'^cheat\s*engine(?:-x86_64)?(?:\.exe)?$', re.I)


def _bundle_info(app: Path):
    try:
        with (app / 'Contents' / 'Info.plist').open('rb') as handle:
            return plistlib.load(handle)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return {}


def validate_tool_path(tool: str, path: str):
    """Accept only actual known bundles or named executables, not arbitrary files."""
    if tool not in {'Burp Suite', 'Cheat Engine'}:
        raise ValueError('Only Burp Suite and Cheat Engine can be located manually.')
    if not isinstance(path, str) or not 1 <= len(path) <= 4096 or '\x00' in path:
        raise ValueError('Choose an installed application or executable file.')
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise ValueError('That file or application does not exist.')
    if candidate.is_dir() and candidate.suffix.lower() == '.app':
        info = _bundle_info(candidate)
        bundle = str(info.get('CFBundleIdentifier') or '').lower()
        display = (str(info.get('CFBundleName') or '') + ' ' + candidate.stem).lower()
        if tool == 'Burp Suite' and ('portswigger' in bundle or 'burpsuite' in bundle or
                                      (candidate.name.lower() in _BURP_NAMES and info)):
            return {'location': str(candidate), 'runtime': 'macOS application'}
        if tool == 'Cheat Engine' and (candidate.name.lower() in _CE_APP_NAMES or
                                       'cheat engine' in display or 'cheatengine' in bundle) and info:
            return {'location': str(candidate), 'runtime': 'macOS application'}
    if candidate.is_file():
        name = candidate.name.lower()
        if tool == 'Cheat Engine' and (name in {'cheat engine.exe', 'cheatengine.exe', 'cheatengine-x86_64.exe'}):
            return {'location': str(candidate), 'runtime': 'Windows executable (Wine/CrossOver required)'}
        if tool == 'Cheat Engine' and name in {'cheatengine', 'cheat-engine'} and os.access(candidate, os.X_OK):
            return {'location': str(candidate), 'runtime': 'command-line executable'}
        if tool == 'Burp Suite' and name in {'burpsuite', 'burpsuite.sh'} and os.access(candidate, os.X_OK):
            return {'location': str(candidate), 'runtime': 'command-line executable'}
    raise ValueError(f'This is not a recognized {tool} app or executable. Select its .app bundle or actual program file.')


def _mac_candidates():
    roots = [Path('/Applications'), Path.home() / 'Applications', Path('/opt/homebrew/Caskroom')]
    # A cask often uses a versioned subdirectory. Only scan one level beneath it.
    for root in roots[:2]:
        if root.is_dir():
            try:
                yield from (p for p in root.iterdir() if p.suffix.lower() == '.app')
            except OSError:
                pass
    caskroot = roots[2]
    if caskroot.is_dir():
        for named in ('burp-suite', 'burp-suite-professional', 'cheat-engine'):
            subdir = caskroot / named
            if subdir.is_dir():
                try:
                    for version in list(subdir.iterdir())[:15]:
                        if version.is_dir():
                            yield from (p for p in version.iterdir() if p.suffix.lower() == '.app')
                except OSError:
                    pass


def _spotlight_candidates():
    if platform.system() != 'Darwin' or not shutil.which('mdfind'):
        return
    # Spotlight queries only indexed metadata; it is not a full-disk scan.
    for term in ('Burp Suite', 'Cheat Engine'):
        try:
            result = subprocess.run(['mdfind', '-name', term], capture_output=True,
                                    text=True, timeout=3, check=False)
            for line in result.stdout.splitlines()[:120]:
                candidate = Path(line)
                if candidate.suffix.lower() in ('.app', '.exe'):
                    yield candidate
        except (OSError, subprocess.TimeoutExpired):
            continue


def _found(tool: str, manual_paths=None):
    paths = (manual_paths or {})
    manual = paths.get(tool)
    if isinstance(manual, str) and manual:
        try:
            return validate_tool_path(tool, manual), 'selected manually'
        except (OSError, ValueError):
            pass  # removed/uninstalled since selection; continue auto-detection
    system = platform.system()
    if system == 'Darwin':
        candidates = list(_mac_candidates())
        # Optional Spotlight search finds apps installed outside /Applications.
        candidates.extend(_spotlight_candidates())
        for candidate in candidates:
            try:
                return validate_tool_path(tool, str(candidate)), 'detected on Mac'
            except (OSError, ValueError):
                continue
        # Wine/CrossOver may store a Windows CE executable outside Applications.
        if tool == 'Cheat Engine':
            home = Path.home()
            prefixes = [home / '.wine/drive_c/Program Files',
                        home / '.wine/drive_c/Program Files (x86)']
            for root in prefixes:
                if root.is_dir():
                    try:
                        for folder in root.iterdir():
                            if folder.is_dir() and 'cheat engine' in folder.name.lower():
                                for filename in ('Cheat Engine.exe', 'cheatengine-x86_64.exe'):
                                    path = folder / filename
                                    if path.is_file():
                                        return validate_tool_path(tool, str(path)), 'detected in Wine prefix'
                    except OSError:
                        pass
    if system == 'Darwin' and tool == 'Cheat Engine':
        # CrossOver stores a Windows install inside each bottle, not /Applications.
        # Inspect only the normal per-user bottle roots (no general disk crawl).
        bottles = Path.home() / 'Library/Application Support/CrossOver/Bottles'
        if bottles.is_dir():
            try:
                for bottle in list(bottles.iterdir())[:100]:
                    for program_dir in ('Program Files','Program Files (x86)'):
                        root = bottle / 'drive_c' / program_dir
                        if not root.is_dir(): continue
                        for folder in list(root.iterdir())[:100]:
                            if not folder.is_dir() or 'cheat engine' not in folder.name.lower(): continue
                            for filename in ('Cheat Engine.exe','cheatengine-x86_64.exe'):
                                binary = folder / filename
                                if binary.is_file():
                                    return validate_tool_path(tool,str(binary)), 'detected in CrossOver bottle'
            except OSError:
                pass
    for command in (('burpsuite',) if tool == 'Burp Suite' else ('cheatengine', 'cheat-engine')):
        executable = shutil.which(command)
        if executable:
            try:
                return validate_tool_path(tool, executable), 'detected on PATH'
            except (ValueError, OSError):
                pass
    return None, ''


def inventory(manual_paths=None):
    system = platform.system()
    applications = (Path('/Applications'), Path.home() / 'Applications')

    def check(name, commands=(), apps=(), setup='', caveat=''):
        cli = next((shutil.which(n) for n in commands if shutil.which(n)), None)
        bundle = next((str(base / n) for base in applications for n in apps if (base / n).exists()), None)
        path = cli or bundle
        return {
            'name':name, 'installed':bool(path), 'location':path,
            'status':'detected' if path else 'missing', 'verified':False,
            'instructions':setup,
            'detail':caveat or ('Detected; running connection is not verified.' if path else setup),
        }

    special = []
    for name in ('Burp Suite', 'Cheat Engine'):
        detected, evidence = _found(name, manual_paths)
        if name == 'Burp Suite':
            hint = 'Install Burp Suite from PortSwigger, or use the built-in web gateway.'
        else:
            hint = 'Choose your installed Cheat Engine .app or Windows .exe with Locate installed app. Windows versions require Wine/CrossOver and are not a native macOS memory connection.'
        detail = (f'{evidence}: {detected["runtime"]}. Existence verified; active connection not tested.'
                  if detected else f'Not found in standard locations or Spotlight. {hint}')
        special.append({'name':name, 'installed':bool(detected),
                        'location':detected['location'] if detected else None,
                        'runtime':detected['runtime'] if detected else None,
                        'status':'detected' if detected else 'missing', 'verified':False,
                        'instructions':hint, 'detail':detail, 'source':evidence or 'not detected',
                        'native_ready':bool(detected and detected['runtime']=='macOS application')})
    burp, cheat = special
    return [
        burp,
        check('Tor Browser',('tor-browser',),('Tor Browser.app',), 'Install Tor Browser from torproject.org.'),
        check('geckodriver',('geckodriver',),(), 'Install a matching geckodriver.'),
        check('Nuclei',('nuclei',),(), 'Install ProjectDiscovery Nuclei for approved checks.'),
        check('Claude Code',('claude',),(), 'Install Claude Code CLI and sign in.'),
        check('Codex CLI',('codex',),(), 'Install OpenAI Codex CLI and sign in.'),
        check('ffmpeg',('ffmpeg',),(), 'Install ffmpeg to extract media.'),
        check('Tesseract OCR',('tesseract',),(), 'Install tesseract for OCR.'),
        cheat,
        {'name':'macOS memory bridge', 'installed':system=='Darwin',
         'location':'Bundled local integration' if system=='Darwin' else None,
         'status':'available' if system=='Darwin' else 'unsupported', 'verified':False,
         'instructions':'Use the local memory workspace and select a process.' if system=='Darwin' else 'Requires macOS.',
         'detail':'Bundled does not mean attached to a process or permission granted.'},
    ]
