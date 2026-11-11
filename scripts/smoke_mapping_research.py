"""Frozen-backend mapping research smoke test in a disposable workspace.

Uses a cited local lesson and an intentionally unavailable loopback model.
No external search, hosted inference, attachment, or process-memory read occurs.
"""
import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from smoke_backend import exchange

ROOT = Path(__file__).resolve().parents[1]


def main():
    executable = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'src-tauri/binaries/bughunter-backend-aarch64-apple-darwin'
    with tempfile.TemporaryDirectory(prefix='mapping-package-smoke-') as root, socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))  # Reserved, deliberately not listening.
        endpoint = 'http://127.0.0.1:' + str(unavailable.getsockname()[1])
        with tempfile.TemporaryFile(mode='w+') as errors:
            def launch():
                return subprocess.Popen([str(executable), '--stdio', '--data-dir', root],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True)
            process = launch()
            def rpc(method, **params):
                return exchange(process, {'id':1, 'method':method, 'params':params})
            def close():
                process.stdin.close()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(); raise
                finally: process.stdout.close()
            try:
                assert rpc('memory.mappings.list') == []
                rpc('settings.save', provider='ollama', model='mapping-smoke-unavailable',
                    api_base_url=endpoint, local_only=True)
                lesson = 'A fixture score can be located by scanning for its observed numeric value, changing the visible score, and narrowing the scan.'
                source = rpc('sources.import', filename='mapping-fixture.md',
                    content_base64=base64.b64encode(lesson.encode()).decode())
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    source = rpc('sources.get', id=source['id'])
                    if source['status'] in ('ready', 'partial', 'error'): break
                    time.sleep(.1)
                assert source['status'] == 'ready', source['status']
                job = rpc('memory.mappings.start', query='fixture score', program='mapping-fixture', search_web=False)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    job = rpc('memory.mappings.get', id=job['id'])
                    if job['status'] not in ('researching', 'suggesting'): break
                    time.sleep(.1)
                assert job['status'] == 'ready', job.get('progress')
                candidate = job['candidates'][0]
                assert candidate['status'] == 'proposed' and candidate['origin'] == 'scan_plan'
                assert any(r['source_id'] == source['id'] and candidate['quote'] in r['text'] for r in job['references'])
                exported = json.loads(rpc('memory.mappings.export', id=job['id'])['content'])
                assert exported['verified_count'] == 0
                assert exported['schema'] == 'bughunter.memory-mappings/v1'
                conversation = rpc('chat.mapping.start',message='Research memory mappings for fixture score',
                    source_ids=[source['id']],search_web=False)
                sid=conversation['session']['id']
                rpc('chat.mapping.select',id=sid,context={'pid':42,'process_instance_id':'42:smoke-fixture',
                    'program':'mapping-fixture','build_sha256':'a'*64})
                deadline = time.monotonic()+30
                while time.monotonic()<deadline:
                    conversation=rpc('chat.mapping.advance',id=sid)
                    if conversation['workflow']['status']!='researching': break
                    time.sleep(.1)
                assert conversation['workflow']['status']=='needs_value'
                rpc('chat.mapping.reply',id=sid,message='The value is 100.')
                pending=rpc('chat.mapping.advance',id=sid)
                assert pending['next']['kind']=='check'
                assert pending['next']['request']['expected_value']=='100'
                # This fixture does not execute native reads or synthesize verification.
                rpc('chat.mapping.stop',id=sid)
                history=json.loads(rpc('chat.export',id=sid)['content'])
                assert history['mapping_workflows'][0]['research']['verified_count']==0
                assert history['mapping_workflows'][0]['research']['references']
                close(); process = launch()
                restored = rpc('memory.mappings.get', id=job['id'])
                assert restored['candidates'][0]['id'] == candidate['id']
                assert restored['references'] == job['references']
                assert rpc('chat.mapping.status',id=sid)['workflow']['status']=='cancelled'
                assert rpc('chat.get',id=sid)['messages']==history['conversation']['messages']
                print(json.dumps({'packaged_mapping_research':'passed', 'citation_export':'passed',
                    'chat_research_and_check_handoff':'passed','chat_stop_and_persistence':'passed',
                    'restart_persistence':'passed', 'native_external_process_access':'not_tested',
                    'live_model_proposals':'not_tested'}, indent=2))
            finally:
                if process.poll() is None: close()


if __name__ == '__main__':
    main()
