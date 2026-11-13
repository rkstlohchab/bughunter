"""Image-only subscription requests through the user's signed-in Claude CLI."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from .security import redact_text

from .claude_models import catalog

MODEL_OPTIONS=[*catalog(),
               *[{'id':name,'label':name.title()+' (latest alias)'} for name in ('sonnet','opus','haiku')]]
MODEL_IDS={model['id'] for model in MODEL_OPTIONS}


def connection_status():
    from .providers import claude_status
    status=claude_status()
    ready=bool(status.get('subscription_ready'))
    return {'ready':ready,'models':MODEL_OPTIONS,
            'detail':'Signed in with a Claude subscription.' if ready else 'Sign in with claude auth login, then recheck.'}


def image_message(prompt, images):
    if not 1 <= len(images) <= 3:
        raise ValueError('Claude video analysis requires one to three sampled images.')
    blocks=[{'type':'text','text':redact_text(prompt)}]
    for value in images:
        path=Path(value)
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 10*1024**2:
            raise ValueError('Use regular image files of at most 10 MiB.')
        raw=path.read_bytes()
        if raw.startswith(b'\xff\xd8'):
            mime='image/jpeg'
        elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
            mime='image/png'
        elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':
            mime='image/webp'
        else:
            raise ValueError('Use JPEG, PNG or WebP tutorial images.')
        blocks.append({'type':'image','source':{'type':'base64','media_type':mime,
                                               'data':base64.b64encode(raw).decode('ascii')}})
    return {'type':'user','message':{'role':'user','content':blocks},'parent_tool_use_id':None}


def describe(model, system, prompt, images, cancel_check=None, timeout=240,effort='medium'):
    """No file, command, browser, MCP, session-reuse or API-key tools are exposed."""
    from .providers import subscription_env
    def check():
        if cancel_check: cancel_check()
    check()
    message=image_message(prompt,images)
    if model not in MODEL_IDS:
        raise ValueError('Choose a model from the Claude subscription catalog.')
    from .model_effort import info
    levels=info('claude_code',model)['levels']
    if effort and levels and effort not in levels:raise ValueError('Unsupported effort for this Claude model.')
    if not connection_status()['ready']:
        raise ValueError('Sign in to a Claude subscription with claude auth login first.')
    executable=shutil.which('claude')
    if not executable:
        raise ValueError('Install Claude Code and sign in with your subscription.')
    args=[executable,'-p','--input-format','stream-json','--output-format','stream-json','--verbose',
          '--model',model,'--tools','','--strict-mcp-config',
          '--mcp-config','{"mcpServers":{}}','--setting-sources','',
          '--settings','{"disableAllHooks":true}','--safe-mode','--no-session-persistence',
          '--max-turns','1','--permission-mode','dontAsk','--system-prompt',system]
    if effort:args+=['--effort',effort]
    from .process_runtime import cli_argv
    args=cli_argv(args)
    with tempfile.TemporaryDirectory(prefix='bughunter-claude-media-') as workspace:
        check()
        incoming=Path(workspace)/'input.jsonl'
        incoming.write_text(json.dumps(message)+'\n')
        # A regular input file provides an unambiguous EOF even for multi-image
        # payloads. Repeated timed communicate(input=...) calls can leave the
        # CLI waiting for the rest of a large JSONL message on its input pipe.
        with incoming.open() as stream:
            process=subprocess.Popen(args,cwd=workspace,env=subscription_env(),text=True,
                stdin=stream,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}))
        deadline=time.monotonic()+timeout
        try:
            while True:
                check()
                if time.monotonic()>=deadline:
                    raise TimeoutError('Claude video analysis exceeded its time limit; completed frames remain saved.')
                try:
                    output,error=process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            check()
            terminal=None
            actual_models=set()
            for line in output.splitlines():
                try: event=json.loads(line)
                except ValueError: continue
                if event.get('type')=='assistant' and event.get('message',{}).get('model'):
                    actual_models.add(event['message']['model'])
                if event.get('type')=='result':terminal=event
            if process.returncode or not terminal or terminal.get('is_error'):
                detail=(terminal or {}).get('result') or '; '.join((terminal or {}).get('errors') or []) or error.strip()
                raise ValueError(redact_text(str(detail or 'Claude returned no completed result.'))[:1000])
            answer=redact_text(str(terminal.get('result') or '')).strip()
            if not answer:
                raise ValueError('Claude returned no readable image description.')
            if model.startswith('claude-'):
                actual_models.update((terminal.get('modelUsage') or terminal.get('model_usage_since_last_result') or {}).keys())
                if actual_models!={model}:
                    raise ValueError('Claude did not verify the requested pinned model; no substitute was accepted.')
            return answer
        finally:
            if process.poll() is None:
                from .process_runtime import terminate
                terminate(process)
            for pipe in (process.stdin,process.stdout,process.stderr):
                if pipe:pipe.close()
