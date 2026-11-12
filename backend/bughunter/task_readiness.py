"""Human-readable, passive readiness checks for tasks launched from Chat.

These checks NEVER launch processes or make network requests. A detection is not
proof a specific remote tool or protected process can be controlled.
"""
from __future__ import annotations
import time

SUPPORTED = {'chat', 'website', 'cart', 'memory', 'agents', 'research'}


def inspect_task(kind, *, settings, credentials=None, dependencies=(), project=None, gateway=None):
    if kind not in SUPPORTED:
        raise ValueError('Unknown task type for connection check.')
    credentials = credentials or {}
    gateway = gateway or {}
    installed = {str(x.get('name')): bool(x.get('installed')) for x in dependencies}
    steps = []

    def add(label, status, detail, action=''):
        steps.append({'label': label, 'status': status, 'detail': detail, 'action': action})

    provider = str(settings.get('provider') or '')
    model = str(settings.get('model') or '')
    if kind in {'chat', 'research'}:
        missing = not provider or not model or (provider in {'openai_compatible', 'remote_ollama'} and not settings.get('api_base_url'))
        if provider in {'openai', 'anthropic', 'deepseek', 'openai_compatible', 'remote_ollama'} and not credentials.get(provider):
            missing = True
        add('AI model', 'setup' if missing else 'configured',
            'Choose your AI in Settings and save its connection key or sign-in. Use “Test connection” to verify it is online.' if missing
            else f'{model} is configured. Reachability is not tested by this quick check.', 'settings' if missing else 'test_model')

    if kind == 'website':
        ok = bool(project and project.get('scope') and not project.get('workspace_only'))
        add('Authorized website project', 'ready' if ok else 'setup',
            'A web target and its permitted paths must be set before any automated requests.' if not ok
            else 'A target scope is saved; the runner still enforces the approved paths.', 'project' if not ok else '')
        verified = bool(gateway.get('verified_at') and time.time() - gateway['verified_at'] < 300)
        add('Built-in web connection', 'ready' if verified else 'setup',
            'Connect the built-in web gateway. Burp is optional; this needs no Brave key.' if not verified
            else 'The gateway was verified recently for HTTP and HTTPS.', 'connect_web' if not verified else '')
    if kind == 'cart':
        add('Shopping-cart testing', 'ready',
            'The disposable local cart lab is built in. Testing a live store requires a scoped project and approvals.')
    if kind == 'memory':
        available = installed.get('macOS memory bridge', False)
        add('Memory tools', 'configured' if available else 'setup',
            'macOS bridge is bundled, but you must grant process access and select an accessible process.' if available
            else 'The macOS memory bridge is not available here; use a supported OS and the separate memory workspace.',
            'memory' if available else 'settings')
    if kind == 'agents':
        available = [name for name in ('Claude Code', 'Codex CLI') if installed.get(name)]
        add('Coding agents', 'configured' if available else 'setup',
            'Detected: ' + ', '.join(available) + '. CLI sign-in and folder permissions must still be verified.' if available
            else 'Install Claude Code or Codex CLI and sign in to use external coding agents.', 'settings' if not available else '')
    if kind == 'research':
        add('Public web search', 'configured',
            'Free metasearch is the default and Wikipedia is the fallback; no Brave key is required. Internet access is needed.')
    return {
        'kind': kind, 'steps': steps,
        'needs_setup': any(s['status'] == 'setup' for s in steps),
        'note': 'Ready means configured, not guaranteed successful. No task was executed by this check.',
    }
