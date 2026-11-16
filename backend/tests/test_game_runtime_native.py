"""Actual helper/process tests; no provider or paid model calls."""
import json
import os
import select
import signal
import subprocess
import sys
import time

import pytest
from bughunter.app import Application
from bughunter.game_runtime.service import birth, helper_alive
from bughunter.memory_engine.protocol import Request, encode_frame, decode_frame

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native acceptance lane')


class Helper:
    def __init__(self, script=None):
        program = script or "from bughunter.memory_engine.__main__ import main; main()"
        self.process = subprocess.Popen([sys.executable, '-u', '-c', program], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.ident = 0
        self.call('bootstrap', {'credential_hex': 'b'*64})

    def send(self, method, params):
        self.ident += 1
        self.process.stdin.write(encode_frame(Request(id=self.ident, method='gamelab.native.'+method, params=params)))
        self.process.stdin.flush()
        return self.ident

    def receive(self, timeout=20):
        assert select.select([self.process.stdout], [], [], timeout)[0], 'helper reply timeout'
        line = self.process.stdout.readline()
        return decode_frame(line) if line else None

    def call(self, method, params):
        ident = self.send(method, params)
        result = self.receive()
        assert result and result.id == ident and not result.error, result
        return result.result

    def close(self):
        try: os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr): stream.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    app = Application(tmp_path, primary=False, native_credential='a'*64)
    monkeypatch.setattr(app.operator, '_loop', lambda *args: None)
    app.game_runtime.private('gamelab.native.bootstrap', {'credential_hex':'a'*64})
    task = app.harness.start('Test game runtime', grants=['app'], mode='game', web_search_mode='off')
    fixture = app.game_runtime.fixture(task['id'])
    yield app, fixture
    app.close()


def test_crash_after_real_write_before_reply_retains_unknown_and_never_replays(live):
    app, fixture = live
    g, b = app.game_runtime, app.game_runtime.broker
    session, target = fixture['session'], fixture['target']
    script = """import os
from bughunter.memory_engine import server
original = server._METHOD_TABLE['memory.write']
def lost_receipt(self, params):
    original(self, params)
    os._exit(77)
server._METHOD_TABLE['memory.write'] = lost_receipt
from bughunter.memory_engine.__main__ import main
main()
"""
    h = Helper(script)
    try:
        attached=h.call('attach', {'pid':target['pid'], 'session_id':'native'})
        executor = b.register_executor('native','host',birth(h.process.pid),session['process_instance_id'],target['target_fingerprint'])
        params = {**target['fields']['countdown'],'value':7,'expected_value':60,'verify':True}
        op = b.enqueue(session['id'], 'write', 'mutating', params)
        claimed = b.claim('native',executor['executor_epoch'])
        dispatched = b.dispatched(op['id'],'native',executor['executor_epoch'],claimed['attempt_id'],claimed['revision'])
        h.send('write', {**params,'session_id':'native','identity':attached['identity']})
        assert h.receive() is None
        assert h.process.wait(timeout=3) == 77
        # An oracle reads the actual changed fixture state, independently of
        # the deliberately lost native receipt.
        from bughunter.game_runtime.testing import Oracle
        with Oracle.from_identity(g._fixtures[target['id']].identity()) as oracle:
            assert oracle.read_field('countdown') == 7
        g.reconcile()
        assert b.get_operation(op['id'])['status'] == 'outcome_unknown'
        assert b.get_session(session['id'])['state'] == 'interrupted'
        assert not helper_alive(executor['helper_process_instance_id'])
        again = type(b)(app.store)
        assert again.get_operation(op['id'])['attempt_id'] == dispatched['attempt_id']
        assert not any(o['status']=='queued' for o in again.operations())
        if folder:=os.environ.get('BUGHUNTER_P04_EVIDENCE_DIR'):
            from pathlib import Path
            output=Path(folder);output.mkdir(parents=True,exist_ok=True)
            (output/'lost-write-reply.json').write_text(json.dumps({'oracle_value':7,'helper_exit_code':77,
                'operation':again.get_operation(op['id']),'session':again.get_session(session['id']),
                'queued_replays':0,'helper_death_confirmed':not helper_alive(executor['helper_process_instance_id'])},indent=2))
    finally: h.close()


def test_priority_stop_answers_while_native_job_is_blocked():
    script = """import time
from bughunter.memory_engine import server
server._METHOD_TABLE['memory.read'] = lambda self, params: time.sleep(10)
from bughunter.memory_engine.__main__ import main
main()
"""
    h = Helper(script)
    try:
        h.send('read', {'session_id':'blocked','address':'0x1000','vtype':'uint'})
        time.sleep(.05)
        started=time.monotonic()
        reply=h.call('quiesce', {})
        assert reply['quiescent'] is False
        assert time.monotonic()-started < .5
        h.send('read', {'session_id':'blocked','address':'0x1000','vtype':'uint'})
        assert h.receive().error
    finally: h.close()

def test_stop_timeout_reaps_helper_after_actual_write_and_preserves_unknown(live):
    app,fixture=live;g=app.game_runtime;b=g.broker;target=fixture['target'];session=fixture['session']
    script="""import time,json
from bughunter.memory_engine import server
original=server._METHOD_TABLE['memory.write']
def delayed(self, params):
    result=original(self, params)
    print(json.dumps({'kind':'notification','method':'test.mutation_observed','params':{}}),flush=True)
    time.sleep(60)
    return result
server._METHOD_TABLE['memory.write']=delayed
from bughunter.memory_engine.__main__ import main
main()
"""
    h=Helper(script)
    try:
        attached=h.call('attach',{'pid':target['pid'],'session_id':'native'})
        b.register_executor('native','host',birth(h.process.pid),session['process_instance_id'],target['target_fingerprint'])
        params={**target['fields']['countdown'],'value':7,'expected_value':60,'verify':True}
        op=b.enqueue(session['id'],'write','mutating',params)
        c=b.claim('native',0);b.dispatched(c['id'],'native',0,c['attempt_id'],c['revision'])
        h.send('write',{**params,'session_id':'native','identity':attached['identity']})
        assert h.receive().method=='test.mutation_observed'
        b.stop(session['id']);start=time.monotonic()
        while time.monotonic()-start<2:
            assert h.call('quiesce',{})['quiescent'] is False
            time.sleep(.1)
        kill_start=time.monotonic();h.close()
        assert time.monotonic()-kill_start<2
        g.reconcile()
        assert b.get_operation(op['id'])['status']=='outcome_unknown'
        assert b.get_session(session['id'])['state']=='interrupted'
        from bughunter.game_runtime.testing import Oracle
        with Oracle.from_identity(g._fixtures[target['id']].identity()) as oracle:
            assert oracle.read_field('countdown')==7
    finally:
        if h.process.poll() is None:h.close()
