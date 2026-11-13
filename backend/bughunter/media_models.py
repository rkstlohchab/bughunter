"""Curated local weights; installed custom models require capability inspection."""
import shutil
import os
import platform
import subprocess
from functools import lru_cache
from pathlib import Path
import httpx

HOST = 'http://127.0.0.1:11434'
RESERVE_BYTES = 5 * 1024**3
CURATED = [
    {'id':f'huihui_ai/qwen3-vl-abliterated:{size}b-instruct',
     'name':f'Qwen3-VL Abliterated {size}B (uncensored)', 'roles':['chat','vision'],
     'download_bytes':int(gb*10**9), 'memory_note':note}
    for size,gb,note in [(2,1.9,'Recommended on 8 GB Macs; one model at a time.'),
                         (4,3.3,'Can swap heavily or time out on 8 GB Macs; prefer 2B for local vision.'),
                         (8,6.1,'May exceed available memory on 8 GB Macs; 16 GB or more recommended.')]
]
# Retain the existing explicit official choices without downloading arbitrary tags.
CURATED += [{'id':f'qwen3-vl:{size}b','name':f'Qwen3-VL {size}B','roles':['chat','vision'],
             'download_bytes':int(gb*10**9),'memory_note':'One model at a time.'}
            for size,gb in [(2,1.9),(4,3.3),(8,6.1)]]

def local_capabilities(info, model):
    return (isinstance(info,dict) and not info.get('remote_host') and not info.get('remote_model')
            and 'cloud' not in model.lower() and isinstance(info.get('capabilities'),list))

def discover():
    with httpx.Client(timeout=httpx.Timeout(5,connect=1),trust_env=False,follow_redirects=False) as client:
        response=client.get(HOST+'/api/tags'); response.raise_for_status()
        result=[]
        for tag in response.json().get('models',[]):
            model=tag.get('name','')
            if not model: continue
            item={'id':model,'name':model,'installed':True,'digest':tag.get('digest'),
                  'download_bytes':tag.get('size',0),'capabilities':[],'roles':[],'local':False}
            try:
                show=client.post(HOST+'/api/show',json={'model':model}); show.raise_for_status()
                info=show.json(); item['local']=bool(local_capabilities(info,model))
                item['capabilities']=info.get('capabilities',[])
                if item['local']:
                    if 'completion' in item['capabilities']: item['roles'].append('chat')
                    if 'vision' in item['capabilities']: item['roles'].append('vision')
            except (httpx.HTTPError,ValueError,AttributeError): pass
            result.append(item)
        return result

def catalog(root):
    error=None
    try: installed=discover()
    except (httpx.HTTPError,ValueError): installed=[]; error='Start Ollama to inspect installed model capabilities.'
    rows={row['id']:{**row,'installed':False,'capabilities':[],'local':True,'digest':None} for row in CURATED}
    for row in installed:
        old=rows.get(row['id'],{})
        rows[row['id']]={**old,**row,'name':old.get('name',row['name']),
                         'roles':row['roles']}
    values=list(rows.values())
    return {'models':values,'chat_models':[r for r in values if 'chat' in r['roles']],
            'vision_models':[r for r in values if 'vision' in r['roles']],
            'disk':{'free_bytes':shutil.disk_usage(Path(root)).free,'reserve_bytes':RESERVE_BYTES},'error':error}


@lru_cache(maxsize=1)
def low_memory_host():
    """Cache physical RAM once; never infer available RAM from a transient load."""
    try:
        if platform.system() == 'Darwin':
            result = subprocess.run(['/usr/sbin/sysctl','-n','hw.memsize'],capture_output=True,text=True,timeout=2,check=True)
            size = int(result.stdout.strip())
        else:
            size = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        return 0 < size <= 8 * 1024**3
    except (OSError,ValueError,subprocess.SubprocessError):
        return False
