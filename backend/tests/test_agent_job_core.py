"""Agent events and lifecycle tests that run without optional ML dependencies."""
import io
import json
import time
from unittest.mock import patch

from bughunter.agent_jobs import AgentJobService, MAX_JOB_SECONDS, MAX_OUTPUT_LINE
from bughunter.store import Store


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.pid = 12345
        self.stdout = io.StringIO(json.dumps({"type": "item.completed", "item": {"command": "pwd", "exit_code": 0}}) + "\n")
        self.environment = kwargs.get('env', {})
    def poll(self): return 0
    def wait(self, timeout=None): return 0
    def terminate(self): pass
    def kill(self): pass


def test_agent_records_outputs_and_filters_known_workbench_secrets(tmp_path, monkeypatch):
    store = Store(tmp_path/'store')
    agent = AgentJobService(store, recover=False)
    seen=[]
    def fake_popen(*args, **kwargs):
        seen.append(kwargs.get('env', {}))
        return FakeProcess(*args, **kwargs)
    monkeypatch.setenv('NGROK_AUTHTOKEN','private-key-which-should-not-reach-agent')
    monkeypatch.setenv('OPENAI_API_KEY','other-private-value')
    with patch('bughunter.agent_jobs.shutil.which',return_value='/usr/local/bin/claude'), patch('bughunter.agent_jobs.subprocess.Popen',fake_popen):
        job=agent.start('Read the project and produce a summary.',agents=['claude'])
        for _ in range(100):
            record=agent.dispatch('agents.get',{'id':job['id']})
            if record['status'] in {'completed','failed'}: break
            time.sleep(0.01)
    assert record['status']=='completed'
    assert any(e['kind']=='item.completed' for e in record['events'])
    assert seen and 'NGROK_AUTHTOKEN' not in seen[0]
    assert 'OPENAI_API_KEY' not in seen[0]
    assert MAX_OUTPUT_LINE < 100_000 and MAX_JOB_SECONDS <= 1800
    agent.shutdown()


def test_agent_deadline_records_failure(tmp_path, monkeypatch):
    from bughunter import agent_jobs
    store = Store(tmp_path/'store')
    service = AgentJobService(store, recover=False)
    class Hung:
        pid = 12345
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.stdout = self
        def readline(self, limit):
            time.sleep(.05)
            return ''
        def poll(self): return 143 if self.closed else None
        def terminate(self): self.closed = True
        def wait(self, timeout=None): return 143
        def kill(self): self.closed = True
    monkeypatch.setattr(agent_jobs, 'MAX_JOB_SECONDS', .01)
    monkeypatch.setattr(AgentJobService, '_terminate', staticmethod(lambda process: process.terminate()))
    with patch('bughunter.agent_jobs.shutil.which',return_value='/usr/bin/codex'), patch('bughunter.agent_jobs.subprocess.Popen',Hung):
        job = service.start('Check source files',agents=['codex'])
        for _ in range(100):
            result=service.dispatch('agents.get',{'id':job['id']})
            if result['status'] in {'failed','completed'}: break
            time.sleep(.01)
    assert result['status']=='failed'
    assert any(e['kind']=='timeout' for e in result['events'])
    service.shutdown()
