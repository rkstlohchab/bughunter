"""Provider-reported effort levels take precedence over documented model limits."""
EFFORTS={'none','minimal','low','medium','high','xhigh','max','ultra'}


def info(provider, model, reported=None):
    if isinstance(reported,dict) and isinstance(reported.get('levels'),list):
        levels=list(dict.fromkeys(level for level in reported['levels'] if isinstance(level,str) and level in EFFORTS))
        default=reported.get('default')
        if default is None and provider in {'claude_code','anthropic'}:
            from .claude_models import effort
            known_default=effort(model,provider)['default']
            if known_default in levels:default=known_default
        return {**reported,'levels':levels,'default':default}
    levels=[]
    if provider in {'claude_code','anthropic'}:
        from .claude_models import effort
        return effort(model,provider)
    elif provider=='openai' and model in {'gpt-6.1-sol','gpt-6-astra'}:
        levels=['low','medium','high','xhigh','max']
    elif provider in {'gemini','gemini_cli'}:
        if model in {'gemini-3.8-flash','gemini-3.7-flash','gemini-3.1-pro-preview','gemini-4-carbon','gemini-4-argon',
                     'gemini-2.5-pro','gemini-2.5-flash','gemini-2.5-flash-lite'}:levels=['low','medium','high']
        elif model=='gemini-4-hydrogen':levels=['low','medium','high','xhigh']
        elif model in {'gemini-3.6-flash','gemini-3.5-flash-lite','gemini-3.1-flash-lite','gemini-3-flash-preview'}:levels=['minimal','low','medium','high']
        elif model=='gemini-3-pro-preview':levels=['low','high']
    return {'levels':levels,'default':None,'source':'documented' if levels else 'unreported',
            'detail':'Provider-documented model levels.' if levels else 'This connection does not report selectable effort levels.'}


def validate(provider, model, effort, store=None):
    if effort is None or effort=='':return ''
    if not isinstance(effort,str) or effort not in EFFORTS:
        raise ValueError('Choose a supported reasoning effort level.')
    catalog=(store.setting('provider_model_catalogs',{}) if store else {}).get(provider,{})
    option=next((item for item in catalog.get('models',[]) if item.get('id')==model or model in item.get('aliases',[])),{})
    capability=info(provider,model,option.get('effort'))
    if effort not in capability['levels']:
        raise ValueError('This model does not report support for '+effort+' effort. Refresh its model list or use Provider default.')
    return effort


def decorate(provider, option):
    return {**option,'effort':info(provider,option['id'],option.get('effort'))}
