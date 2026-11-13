"""Resource-scoped model choices; never mutate global chat/media settings."""
import json
import re

from .store import now

HOSTED = {'claude_subscription', 'codex_subscription','gemini_subscription','gemini','openai','anthropic'}
MODEL_KEYS = {'claude_subscription':'claude_vision_model',
              'codex_subscription':'codex_vision_model','gemini_subscription':'gemini_vision_model',
              'gemini':'api_vision_model','openai':'api_vision_model','anthropic':'api_vision_model','local':'vision_model'}
CHAT_PROVIDERS={'claude_code':'claude_subscription','codex':'codex_subscription','gemini_cli':'gemini_subscription',
                'gemini':'gemini','openai':'openai','anthropic':'anthropic','ollama':'local'}


def validate(profile,store=None):
    if profile is None:
        return None
    if not isinstance(profile, dict) or not {'provider','model'}<=set(profile) or set(profile)-{'provider','model','effort'}:
        raise ValueError('analysis_model needs provider, model and optional effort.')
    provider, model = profile['provider'], profile['model']
    if not isinstance(provider, str) or provider not in MODEL_KEYS or not isinstance(model, str):
        raise ValueError('Choose a local, Claude subscription or ChatGPT subscription model.')
    if provider == 'claude_subscription':
        from .claude_media import MODEL_IDS
        if model not in MODEL_IDS:
            raise ValueError('Choose a model from the Claude subscription catalog.')
    elif provider == 'codex_subscription':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', model):
            raise ValueError('Invalid ChatGPT subscription model ID.')
    elif provider=='gemini_subscription':
        from .gemini_subscription import MODEL_OPTIONS
        if model not in {item['id'] for item in MODEL_OPTIONS}:raise ValueError('Choose a supported Gemini version.')
    elif provider in {'openai','anthropic','gemini'}:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}',model):raise ValueError('Invalid API model ID.')
    else:
        from . import media_models
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?', model) or '..' in model:
            raise ValueError('Invalid local model ID.')
        if model not in {m['id'] for m in media_models.CURATED}:
            try:
                choices = media_models.discover()
            except Exception as exc:
                raise ValueError('Start Ollama to inspect the selected local model.') from exc
            if not any(m['id'] == model and m.get('local') and 'vision' in m.get('roles', []) for m in choices):
                raise ValueError('Choose curated local weights or a capability-checked installed vision model.')
    result={'provider':provider,'model':model}
    if 'effort' in profile:
        from .model_effort import validate as validate_effort
        chat=next(name for name,analysis in CHAT_PROVIDERS.items() if analysis==provider)
        result['effort']=validate_effort(chat,model,profile['effort'],store)
    return result


def media_config(config, source):
    result = dict(config)
    profile = source.get('analysis_model')
    if profile:
        provider = profile['provider']
        result.update(vision_provider=provider, claude_parallel_enabled=False,
                      codex_detail_model='', enabled=True, vision_enabled=True)
        result[MODEL_KEYS[provider]] = profile['model']
        result['vision_effort']=profile.get('effort','medium') if provider!='local' else ''
        result['media_source_id']=source.get('id')
        if provider in HOSTED:
            result['claude_vision_consent' if provider == 'claude_subscription' else 'codex_vision_consent' if provider=='codex_subscription' else 'hosted_vision_consent'] = True
    return result


def effective(config, source):
    selected = media_config(config, source)
    provider = selected.get('vision_provider', 'local')
    return {'provider':provider, 'model':selected.get(MODEL_KEYS[provider])}


def study_settings(settings, source,store=None):
    result = dict(settings)
    profile = source.get('analysis_model')
    if profile:
        provider=next(name for name,analysis in CHAT_PROVIDERS.items() if analysis==profile['provider'])
        if store and provider!=settings.get('provider'):
            result.update(api_base_url='',pricing_confirmed=False,reasoning_effort='')
            result.update(store.setting('provider_connections',{}).get(provider,{}))
        result.update(provider=provider,model=profile['model'],reasoning_effort=profile.get('effort','medium') if provider!='ollama' else '')
        # This snapshot is authorized for this source only. Global local_only is untouched.
        result['local_only'] = profile['provider'] == 'local'
        if profile['provider'] == 'local':
            result['api_base_url'] = 'http://127.0.0.1:11434'
        result['source_analysis_model'] = dict(profile)
    return result


def set_models(service, ids, analysis_model):
    profile = validate(analysis_model,service.store)
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(i, str) for i in ids):
        raise ValueError('Choose 1–500 resource IDs.')
    ids = list(dict.fromkeys(ids))
    updated = []
    with service.store.connection(immediate=True) as db:
        for ident in ids:
            row = db.execute("SELECT data FROM entities WHERE kind='source' AND id=?", (ident,)).fetchone()
            if not row:
                raise ValueError('Source not found: ' + ident)
            source = json.loads(row[0])
            selected=effective(service.media_runtime.config() if service.media_runtime else {},{'analysis_model':profile})
            wait=source.get('provider_wait') or {}
            if source.get('status')=='queued' and wait and (wait.get('provider'),wait.get('model'))!=(selected['provider'],selected['model']):
                source['last_provider_wait']=source.pop('provider_wait')
                progress=source.get('media_progress') or {}
                source['media_progress']={**progress,'phase':progress.get('resume_phase','frames')}
            source.update(analysis_model=profile, media_mixed_providers=True, updated_at=now())
            updated.append(source)
        for source in updated:
            db.execute("UPDATE entities SET data=?,updated_at=? WHERE kind='source' AND id=?",
                       (json.dumps(source), source['updated_at'], source['id']))
    for source in updated:
        service._ensure_model_worker(source)
    return {'sources':updated, 'count':len(updated), 'applies':'next_frame'}
