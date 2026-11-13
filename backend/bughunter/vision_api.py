"""Cancellable image-only API calls using the saved provider connection and budget."""
import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import keyring


def describe(store,config,prompt,images,check):
    provider=config['vision_provider'];model=config['api_vision_model']
    active=store.setting('settings',{})
    profile=(active if active.get('provider')==provider else store.setting('provider_connections',{}).get(provider,{}))
    if not profile.get('pricing_confirmed'):raise ValueError('Save this API connection and confirm its prices/budget in Settings first.')
    key=keyring.get_password('BugHunterClaudex',provider)
    if not key:raise ValueError('Save the '+provider+' API key in Settings first. Google AI Pro sign-in and API-key access are separate.')
    defaults={'openai':'https://api.openai.com/v1','anthropic':'https://api.anthropic.com',
              'gemini':'https://generativelanguage.googleapis.com/v1beta/openai'}
    base=(profile.get('api_base_url') or defaults[provider]).rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Image API endpoints must use HTTPS without URL credentials or query parameters.')
    blocks=[{'type':'text','text':prompt}]
    if not 1<=len(images)<=3:raise ValueError('Choose one to three sampled images.')
    for image in images:
        path=Path(image)
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=10*1024**2:raise ValueError('Invalid frame image.')
        raw=path.read_bytes();mime='image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/png' if raw.startswith(b'\x89PNG') else None
        if not mime:raise ValueError('Use JPEG or PNG frames.')
        data=base64.b64encode(raw).decode()
        blocks.append({'type':'image','source':{'type':'base64','media_type':mime,'data':data}} if provider=='anthropic'
                      else {'type':'image_url','image_url':{'url':f'data:{mime};base64,{data}'}})
    # Reserve a conservative per-request bound, atomically shared by all workers.
    # This is an app budget reservation, not a claim about the provider's bill.
    reservation=((len(prompt.encode())+12000*len(images))*float(profile.get('input_price_per_million',0))
                 +4096*float(profile.get('output_price_per_million',0)))/1_000_000
    budget=float(profile.get('api_budget_usd',0));usage_key='media_api_usage:'+str(config.get('media_source_id') or 'image-tests')+':'+provider
    check()
    with store.connection(immediate=True) as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(usage_key,)).fetchone()
        usage=json.loads(row['value']) if row else {'reserved_usd':0,'requests':0}
        if usage['reserved_usd']+reservation>budget:raise ValueError('This course reached the configured API budget reservation. Review API spending before resuming.')
        usage.update(reserved_usd=usage['reserved_usd']+reservation,requests=usage['requests']+1)
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(usage_key,json.dumps(usage)))
    effort=config.get('vision_effort','medium')
    if provider=='anthropic':
        endpoint=base+('/messages' if base.endswith('/v1') else '/v1/messages')
        headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
        payload={'model':model,'max_tokens':4096,'messages':[{'role':'user','content':blocks}]}
        if effort:payload['output_config']={'effort':effort}
    else:
        endpoint=base+'/chat/completions';headers={'Authorization':'Bearer '+key}
        payload={'model':model,('max_tokens' if provider=='gemini' else 'max_completion_tokens'):4096,
                 'messages':[{'role':'user','content':blocks}]}
        if effort:payload['reasoning_effort']=effort
    async def request():
        async with httpx.AsyncClient(timeout=240,trust_env=False,follow_redirects=False) as client:
            response=await client.post(endpoint,headers=headers,json=payload)
            if response.is_error:raise ValueError(f'{provider} image request returned HTTP {response.status_code}. Check model access, API quota and connection settings.')
            data=response.json()
            text=('\n'.join(block.get('text','') for block in data.get('content',[]) if block.get('type')=='text')
                  if provider=='anthropic' else data['choices'][0]['message']['content'])
            if not isinstance(text,str) or not text.strip():raise ValueError('The image API returned no readable answer.')
            return text
    async def cancellable():
        task=asyncio.create_task(request())
        try:
            while not task.done():check();await asyncio.wait({task},timeout=.1)
            check();return await task
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    return asyncio.run(cancellable())
