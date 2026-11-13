from pathlib import Path
import json,threading,concurrent.futures
import pytest
from bughunter.store import Store
from bughunter.rea import ReaService
from bughunter.agent_skills import AgentSkills

class FakeSession:
    def __init__(self):
        self.stop=threading.Event();self.ready=concurrent.futures.Future();self.ready.set_result(True)
        self.catalog=[{'name':'inspect_pe_resources','input_schema':{},'description':'Inspect a PE image'},{'name':'capture_native_ui_scenario','input_schema':{},'description':'Runtime UI'}]
        self.availability={'groups':[{'names':['inspect_pe_resources'],'available':True,'effects':['mutatesSession']},{'names':['capture_native_ui_scenario'],'available':True,'effects':['mutatesTarget','executesForeignCode','changesUiState']}]};self.calls=[]
    def request(self,name,arguments,check=None):
        if check:check()
        self.calls.append((name,arguments));return {'content':[{'type':'text','text':json.dumps({'result':{'evidence_id':'rea-proof','observed':'PE resource tree'}})}]}

def test_bundled_skill_is_versioned_and_blocks_arbitrary_paths():
    skills=AgentSkills();row=skills.list('REA')['items'][0];assert row['id']=='reverse-engineer-anything'
    page=skills.read(row['id']);assert 'REA' in page['text'] and page['package_version']=='6.7.0'
    with pytest.raises(ValueError):skills.read(row['id'],'../../.env')

def test_schema_discovery_and_same_task_session_preserve_evidence(tmp_path,monkeypatch):
    service=ReaService(Store(tmp_path));session=FakeSession();monkeypatch.setattr(service,'session',lambda scope:session)
    catalog=service.tools('inspect_pe_resources',scope='task');assert len(catalog['items'])==1 and catalog['items'][0]['availability']['available']
    result=service.call('task','inspect_pe_resources',{'path':'/fixture/game.exe'},grants=['files'],mode='plan')
    assert result['result']['result']['evidence_id']=='rea-proof';assert Path(result['complete_artifact']).exists()
    assert 'PE resource tree' in service.evidence(result['evidence_id'],'task')['text']
    with pytest.raises(ValueError):service.evidence(result['evidence_id'],'other')

def test_runtime_effects_require_task_grants_and_foreground(tmp_path,monkeypatch):
    service=ReaService(Store(tmp_path));session=FakeSession();monkeypatch.setattr(service,'session',lambda scope:session)
    for mode,grants,background in [('plan',['files','shell','computer'],False),('auto',['files'],False),('auto',['files','shell'],False),('auto',['files','shell','computer'],True)]:
        with pytest.raises(PermissionError):service.call('task','capture_native_ui_scenario',{},mode=mode,grants=grants,background=background)
    assert not session.calls
    service.call('task','capture_native_ui_scenario',{},mode='full',grants=['files','shell','computer'],background=False);assert len(session.calls)==1

def test_stop_all_prevents_queued_analysis(tmp_path,monkeypatch):
    service=ReaService(Store(tmp_path));session=FakeSession();monkeypatch.setattr(service,'session',lambda scope:session);service.store.set_setting('emergency_stop',True)
    with pytest.raises(InterruptedError):service.call('task','inspect_pe_resources',{})
    assert not session.calls

def test_unknown_or_unavailable_operations_do_not_run(tmp_path,monkeypatch):
    service=ReaService(Store(tmp_path));session=FakeSession();monkeypatch.setattr(service,'session',lambda scope:session)
    with pytest.raises(ValueError,match='Discover'):service.call('task','unknown',{})
    session.availability['groups'][0].update(available=False,reason='engine_required',remediation='Install its engine')
    with pytest.raises(ValueError,match='engine_required'):service.call('task','inspect_pe_resources',{})
    assert not session.calls

def test_setup_installs_pinned_package_in_app_runtime(tmp_path,monkeypatch):
    from bughunter.tool_installer import ToolInstaller
    monkeypatch.setattr('bughunter.tool_installer.executable',lambda name:'/bin/'+name)
    installer=ToolInstaller(Store(tmp_path));plan=installer.plan('rea')
    assert '--prefix' in plan['argv'] and 'rea-agents@6.7.0' in plan['argv'] and '--global' not in plan['argv']


def test_real_mcp_sessions_isolate_targets_and_stop_all_cancels_inflight_work(tmp_path,monkeypatch):
    """Exercise the transport/process lifetime rather than mocking Session.request."""
    import sys,time
    server=tmp_path/'server.py'
    server.write_text('''import asyncio
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("session-fixture")
target=""
@mcp.tool()
def binary_session():
 return {"result":{"tool_availability":{"groups":[{"names":["open_binary","close_binary","inspect_fixture","wait_fixture"],"available":True,"effects":["mutatesSession"]}]}}}
@mcp.tool()
def open_binary(label:str):
 global target
 target=label
 return {"target":target}
@mcp.tool()
def close_binary():
 global target
 target=""
 return {"closed":True}
@mcp.tool()
def inspect_fixture():
 return {"target":target}
@mcp.tool()
async def wait_fixture():
 await asyncio.sleep(30)
 return {"target":target}
mcp.run()
''')
    service=ReaService(Store(tmp_path/'data'))
    monkeypatch.setattr(service,'launch',lambda:(sys.executable,[str(server)],{}))
    try:
        service.call('first','open_binary',{'label':'A'},grants=['files'])
        service.call('second','open_binary',{'label':'B'},grants=['files'])
        assert service.call('first','inspect_fixture',{},grants=['files'])['result']['target']=='A'
        assert service.call('second','inspect_fixture',{},grants=['files'])['result']['target']=='B'
        sessions=list(service._sessions.values());errors=[]
        def waiting():
            try:service.call('first','wait_fixture',{},grants=['files'])
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=waiting);thread.start();time.sleep(.2)
        service.cancel_all();thread.join(timeout=4)
        assert not thread.is_alive() and errors and isinstance(errors[0],InterruptedError)
        assert all(not item.thread.is_alive() for item in sessions)
    finally:service.cancel_all()
