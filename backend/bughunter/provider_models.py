"""Read-only model discovery. Never makes an inference request or saves drafts."""
from __future__ import annotations
import re
import time
import json
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

import httpx
import keyring

from .providers import PROVIDERS, LOCAL_PROVIDERS, is_local_network_host, claude_status
from .codex_subscription import connection_status
from .model_effort import decorate

DEFAULT_URLS = {'openai': 'https://api.openai.com/v1', 'anthropic': 'https://api.anthropic.com',
                'deepseek': 'https://api.deepseek.com', 'ollama': 'http://127.0.0.1:11434',
                'gemini':'https://generativelanguage.googleapis.com/v1beta/openai'}
SUBSCRIPTIONS = {'claude_code', 'codex','gemini_cli'}


def claude_catalog():
    """Initialize the signed-in CLI control protocol without a user/inference turn."""
    from .providers import subscription_env
    executable=shutil.which('claude')
    if not executable:raise ValueError('Install Claude Code and sign in, then refresh models.')
    args=[executable,'--print','--verbose','--input-format','stream-json','--output-format','stream-json',
          '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','',
          '--settings','{"disableAllHooks":true}','--safe-mode','--no-session-persistence']
    request={'type':'control_request','request_id':'catalog','request':{'subtype':'initialize'}}
    with tempfile.TemporaryDirectory(prefix='bh-model-catalog-') as workspace:
        try:
            result=subprocess.run(args,input=json.dumps(request)+'\n',text=True,capture_output=True,
                                  cwd=workspace,env=subscription_env(),timeout=15)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError('Claude model discovery could not finish. Check the CLI connection and refresh models.') from exc
    if result.returncode or len(result.stdout)>2_000_000:
        raise ValueError('Claude model discovery failed. Check the CLI connection and refresh models.')
    models={}
    for line in result.stdout.splitlines():
        try:message=json.loads(line)
        except ValueError:continue
        response=message.get('response',{})
        if message.get('type')!='control_response' or response.get('subtype')!='success':continue
        for row in response.get('response',{}).get('models',[]):
            model=row.get('resolvedModel') or str(row.get('value','')).removesuffix('[1m]')
            if not isinstance(model,str) or not re.fullmatch(r'claude-[a-z0-9-]+',model):continue
            option=models.setdefault(model,{'id':model,
                'aliases':[],'effort':{'levels':row.get('supportedEffortLevels',[]) if row.get('supportsEffort') else [],
                'default':None,'source':'provider_catalog','detail':'Reported by your signed-in Claude CLI.'}})
            from .claude_models import label
            option['label']=label(model)
            alias=row.get('value')
            if alias and alias not in option['aliases']:option['aliases'].append(alias)
            if alias=='default':option['default']=True
    if not models:raise ValueError('Claude did not return versioned models. Update Claude Code and refresh models.')
    # Initialize reports recommendations, not every version accepted by /model.
    # Merge documented public versions without claiming account inference access.
    from .claude_models import catalog
    for option in catalog():
        models.setdefault(option['id'],option)
    return list(models.values())


class ProviderModels:
    def __init__(self, store):
        self.store = store
        self.cache = {}

    def discover(self, provider, api_base_url='', refresh=False):
        if provider not in PROVIDERS:
            raise ValueError('Unknown model provider.')
        base = str(api_base_url or DEFAULT_URLS.get(provider, '')).strip().rstrip('/')
        cache_key = (provider, base)
        cached = self.cache.get(cache_key)
        if cached and not refresh and time.monotonic() - cached[0] < 60:
            return cached[1]
        result = self._discover(provider, base)
        result={**result,'models':[decorate(provider,model) for model in result.get('models',[])]}
        self.cache[cache_key] = (time.monotonic(), result)
        if result.get('ok'):
            catalogs = self.store.setting('provider_model_catalogs', {})
            catalogs[provider] = {**result, 'api_base_url': base}
            self.store.set_setting('provider_model_catalogs', catalogs)
        return result

    def _discover(self, provider, base):
        if provider=='gemini_cli':
            from .gemini_subscription import connection_status as gemini_status
            status=gemini_status()
            return {'provider':provider,'ok':status['ready'],**{k:status[k] for k in ('models','detail','source')}}
        if provider == 'codex':
            status = connection_status()
            return {'provider': provider, 'ok': status['ready'], 'models': status['models'],
                    'detail': status['detail'], 'source': 'Codex account catalog'}
        if provider == 'claude_code':
            status = claude_status()
            if not status.get('subscription_ready'):
                return {'provider':provider,'ok':False,'models':[],'detail':'Run claude auth login, then refresh models.'}
            try:models=claude_catalog()
            except ValueError as exc:
                return {'provider':provider,'ok':False,'models':[],'detail':str(exc)}
            return {'provider':provider,'ok':True,'models':models,
                    'detail':'Versioned Claude models and effort levels. Access depends on your plan.',
                    'source':'Claude CLI recommendations and documented version catalog'}
        if not base:
            return {'provider': provider, 'ok': False, 'models': [], 'detail': 'Enter your server address, then choose Refresh models.'}
        url = urlsplit(base)
        if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a model server URL without credentials, a query or a fragment.')
        if provider == 'crucible':
            if not is_local_network_host(url.hostname):
                raise ValueError('Use the local-network address of your CrucibleLLM device.')
        elif url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Remote model servers require HTTPS.')
        try:
            key = keyring.get_password('BugHunterClaudex', provider) if provider not in LOCAL_PROVIDERS else None
        except Exception:
            key = None
        if provider not in LOCAL_PROVIDERS and not key:
            return {'provider': provider, 'ok': False, 'models': [], 'detail': 'Save your connection key to load the available models.'}
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        endpoint = base + '/models'
        if provider == 'anthropic':
            endpoint = base + ('/models' if base.endswith('/v1') else '/v1/models')
            headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        elif provider in {'ollama', 'remote_ollama'}:
            endpoint = base + '/api/tags'
        elif provider == 'crucible':
            endpoint = base + ('/models' if base.endswith('/v1') else '/v1/models')
        try:
            models, params = [], {'limit': 100} if provider == 'anthropic' else {}
            with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                for _ in range(20):
                    response = client.get(endpoint, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()
                    for row in data.get('models' if provider in {'ollama', 'remote_ollama'} else 'data', []):
                        model = row.get('id') or row.get('name') or row.get('model')
                        if provider=='gemini' and isinstance(model,str) and model.startswith('models/'):
                            model=model.removeprefix('models/')
                        if not isinstance(model, str) or not model or len(model) > 300:
                            continue
                        if provider == 'openai' and (not re.match(r'^(gpt-|chatgpt-|o\d)', model) or any(x in model for x in ['audio', 'realtime', 'transcri', 'tts', 'image'])):
                            continue
                        if provider=='gemini' and (not model.startswith('gemini-') or any(x in model for x in ('tts','image','live','embedding'))):continue
                        if provider in {'ollama', 'remote_ollama'} and 'embed' in model.lower():
                            continue
                        models.append({'id': model, 'label': row.get('display_name') or model})
                    if provider != 'anthropic' or not data.get('has_more'):
                        break
                    cursor = data.get('last_id')
                    if not cursor or cursor == params.get('after_id'):
                        raise ValueError('Model list pagination did not advance.')
                    params['after_id'] = cursor
                else:
                    raise ValueError('Model list exceeded the page limit.')
            unique = {m['id']: m for m in models}
            return {'provider': provider, 'ok': True, 'models': list(unique.values()),
                    'detail': f'{len(unique)} models available.' if unique else 'No chat models reported. Load a model on your server, then refresh.',
                    'source': 'Provider model list'}
        except httpx.HTTPStatusError as exc:
            return {'provider': provider, 'ok': False, 'models': [],
                    'detail': f'Model list returned HTTP {exc.response.status_code}. Check your key and server address, or enter a custom model.'}
        except (httpx.HTTPError, ValueError) as exc:
            return {'provider': provider, 'ok': False, 'models': [],
                    'detail': f'Could not load models ({type(exc).__name__}). Check the connection or enter a custom model.'}
