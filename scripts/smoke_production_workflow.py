"""Repeatable local integration smoke: Knowledge -> Chat -> Tool -> Agents -> restart.

Requires project Python dependencies (or a packaged sidecar); no outside network,
AI subscription, live browser, target process, or OS API permissions are used.
"""
from __future__ import annotations
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        size = int(self.headers.get('Content-Length', 0))
        message = json.loads(self.rfile.read(size))
        if self.path != '/api/chat':
            self.send_error(404);return
        system = next(m['content'] for m in message['messages'] if m['role']=='system')
        self.server.seen.append(system)
        body = json.dumps({'message': {'content': 'Mock response: citation received.'}}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)


def main():
    command = [sys.executable, '-m', 'bughunter']
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT/'backend') + os.pathsep + env.get('PYTHONPATH', '')
    if len(sys.argv)>1 and sys.argv[1]!='--python':
        command = [str(Path(sys.argv[1]).expanduser().resolve())]
    with tempfile.TemporaryDirectory(prefix='bughunter-production-smoke-') as td:
        root = Path(td)
        with ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler) as mock:
            mock.seen=[]
            thread = threading.Thread(target=mock.serve_forever, daemon=True)
            thread.start()
            def launch():
                return subprocess.Popen(command+['--stdio','--data-dir',str(root)],cwd=ROOT,
                    env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            def rpc(proc,seq,rpc_method,**params):
                proc.stdin.write(json.dumps({'id':seq,'method':rpc_method,'params':params})+'\n')
                proc.stdin.flush()
                if not select.select([proc.stdout], [], [], 45)[0]:
                    raise RuntimeError(f'No reply to {rpc_method}')
                line=proc.stdout.readline()
                if not line:
                    raise RuntimeError(f'Backend stopped: {proc.stderr.read()[-1000:]}')
                reply=json.loads(line)
                if 'error' in reply: raise RuntimeError(f'{rpc_method}: {reply["error"]}')
                return reply['result']
            first=launch()
            try:
                project = rpc(first,1,'lab.setup')['project']
                rpc(first,2,'settings.save',provider='ollama',model='mock-ollama',
                    api_base_url=f'http://127.0.0.1:{mock.server_port}',local_only=True)
                source_file=root/'research-note.md'
                source_file.write_text('The local test shop checks coupon exclusions on the server.')
                source=rpc(first,3,'sources.import',path=str(source_file),project_id=project['id'])
                for i in range(80):
                    source=rpc(first,4+i,'sources.get',id=source['id'])
                    if source['status'] in {'ready','partial','error'}:break
                    time.sleep(.1)
                assert source['status']=='ready',source
                hit=rpc(first,100,'knowledge.search',query='coupon exclusions',project_id=project['id'])
                assert any(r['source_id']==source['id'] for r in hit),hit
                # New orchestrated, non-invasive project plan + evidence-reviewed method.
                plan=rpc(first,150,'plans.create',objective='Review coupon exclusions and local research notes',
                    mode='research',project_id=project['id'])
                checked=rpc(first,151,'plans.run',id=plan['id'])
                assert checked['status']=='completed',checked
                assert any(step['key']=='knowledge' for step in checked['steps'])
                assert 'Recorded checkpoints' in rpc(first,152,'plans.report',id=plan['id'])['content']
                method=rpc(first,153,'skills.propose',title='Review coupon exclusions',
                    method='Compare tested coupon eligibility with the documented expected exclusion behavior.',
                    evidence_id=source['id'],project_id=project['id'],tags=['coupon','exclusions'])
                assert method['status']=='candidate'
                assert not rpc(first,154,'skills.match',query='coupon exclusion',project_id=project['id'])
                rpc(first,155,'skills.review',id=method['id'],verified=True,
                    note='Verified against the saved source and local demonstration baseline.')
                plan2=rpc(first,156,'plans.create',objective='Review coupon exclusions again',mode='research',project_id=project['id'])
                checked2=rpc(first,157,'plans.run',id=plan2['id'])
                assert any(s.get('result',{}).get('verified_methods') for s in checked2['steps'])
                conversation=rpc(first,101,'chat.send',message='Summarize the supplied source',
                    source_ids=[source['id']],project_id=project['id'])
                assert 'Mock response' in conversation['messages'][-1]['content']
                assert mock.seen and 'coupon exclusions' in mock.seen[-1]
                traffic=next(x for x in rpc(first,102,'lab.setup')['traffic'] if '/vulnerable/' in x['request']['url'])
                tests=rpc(first,103,'tests.propose',traffic_id=traffic['id'])
                test=next(x for x in tests if x['title']=='Check coupon eligibility on excluded items')
                action=rpc(first,104,'tests.queue',id=test['id'])
                assert action['status']=='pending_approval'
                rpc(first,105,'actions.approve',id=action['id'])
                result=rpc(first,106,'actions.execute',id=action['id'])
                assert result['status']=='completed',result
                finding=rpc(first,107,'tests.assess',action_id=action['id'])
                assert finding['status']=='confirmed', finding
                report=rpc(first,108,'findings.export',id=finding['id'],format='json')
                assert Path(report['path']).exists()
                task=rpc(first,109,'tasks.external.start',kind='memory_session',project_id=project['id'],title='Synthetic integration session')
                rpc(first,110,'tasks.external.event',id=task['id'],kind='tool_result',name='synthetic_tool_result')
                assert any(t['id']==task['id'] for t in rpc(first,111,'tasks.list',project_id=project['id']))
            finally:
                first.stdin.close()
                try:first.wait(timeout=20)
                except subprocess.TimeoutExpired:first.kill();first.wait()
            second=launch()
            try:
                task_after=rpc(second,112,'tasks.get',id=task['id'])
                assert task_after['status']=='interrupted'
                assert any(e.get('name')=='synthetic_tool_result' for e in task_after['events'])
                assert rpc(second,113,'chat.get',id=conversation['id'])['id']==conversation['id']
                assert rpc(second,114,'findings.export',id=finding['id'],format='json')
                print('PASS: real backend stdio -> indexed knowledge -> plans/checkpoints/verified skills -> mock local LLM chat -> lab approval/tool -> Agents history -> restart recovery')
            finally:
                second.stdin.close()
                try:second.wait(timeout=20)
                except subprocess.TimeoutExpired:second.kill();second.wait()
            mock.shutdown()

if __name__=='__main__': main()
