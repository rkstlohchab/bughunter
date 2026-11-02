"""Agent dispatch, local tool discovery, state tracking, and chat activity."""
import json
import io
import time
from unittest.mock import patch
import pytest

from bughunter.app import Application
from bughunter.agent_jobs import AgentJobService


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.pid = 1234
        self.stdout = io.StringIO(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'pwd','exit_code':0}})+'\n')
    def poll(self): return 0
    def wait(self,timeout=None): return 0
    def terminate(self): pass


def wait_done(service, id):
    for _ in range(100):
        row = service.dispatch('agents.get', {'id':id})
        if row['status'] in ('completed','failed','cancelled'): return row
        time.sleep(0.02)
    pytest.fail('Job did not complete')


def test_deploy_two_agents_and_persist_command_events(tmp_path):
    app = Application(tmp_path/'data',primary=False)
    try:
        with patch('bughunter.agent_jobs.shutil.which',side_effect=lambda name:'/usr/local/bin/'+name), patch('bughunter.agent_jobs.subprocess.Popen',FakeProcess):
            job = app.dispatch('agents.start',{'prompt':'Review the repository', 'agents':['claude','codex'], 'working_dir':str(tmp_path)})
            result = wait_done(app.agent_jobs, job['id'])
        assert result['status'] == 'completed'
        assert len(result['events'])>=4
        assert any('command_execution' in e['content'] for e in result['events'])
        assert all(worker['status']=='completed' for worker in result['workers'].values())
        assert app.dispatch('agents.list')[0]['id']==job['id']
    finally:app.shutdown()


def test_missing_cli_and_invalid_paths_fail_without_launch(tmp_path):
    app=Application(tmp_path/'data',primary=False)
    try:
        with patch('bughunter.agent_jobs.shutil.which',return_value=None):
            with pytest.raises(ValueError,match='Install and sign in'): app.dispatch('agents.start',{'prompt':'Review', 'agents':['codex']})
        with patch('bughunter.agent_jobs.shutil.which',return_value='/bin/echo'):
            with pytest.raises(ValueError,match='does not exist'): app.dispatch('agents.start',{'prompt':'Review','agents':['codex'], 'working_dir':str(tmp_path/'bad')})
        assert app.dispatch('agents.list')==[]
    finally:app.shutdown()


def test_chat_delegate_records_an_actual_job_without_calling_llm(tmp_path):
    app=Application(tmp_path/'data',primary=False)
    try:
        with patch('bughunter.agent_jobs.shutil.which',return_value='/usr/bin/claude'),patch('bughunter.agent_jobs.subprocess.Popen',FakeProcess):
            chat=app.dispatch('chat.delegate',{'message':'Deploy Claude Code to review the project','agents':['claude']})
            assert 'Started agent task' in chat['messages'][-1]['content']
            assert chat['agent_job_id'].startswith('agent_job_')
            assert wait_done(app.agent_jobs,chat['agent_job_id'])['status']=='completed'
    finally:app.shutdown()
