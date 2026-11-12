"""The chat agent must report only real, scoped, attributable work."""
import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest
from bughunter.autopilot import Autopilot,choose_workflow,inspect_source
from bughunter.store import Store
from bughunter.tool_installer import ToolInstaller


class Settings:
    def get(self):return {'provider':'ollama','model':'test-model'}

class FakeKnowledge:
    def search_for_project(self,query,project_id,limit=6):
        return [{'source_id':'source_a','id':'chunk_1','title':'Original reference','text':'Document passage read from source.'}]

class FakeAudit:
    def __init__(self):self.calls=[]
    def start(self,**kwargs):
        self.calls.append(kwargs)
        return {'id':'task_web','kind':'website_audit','status':'starting'}

@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'db')
    store.put('project',{'name':'Controlled lab','scope':['https://lab.example/'],
        'read_only_paths':['/'],'authorization':'I own the lab','lab_project':True},id='project_lab')
    app=SimpleNamespace(store=store,knowledge=FakeKnowledge(),settings=Settings(),
                         website_audit=FakeAudit())
    return Autopilot(app),store,app


def test_choose_workflow():
    assert choose_workflow('inspect the game executable I uploaded',['source_a'])=='file'
    assert choose_workflow('scan the website for issues')=='website'
    assert choose_workflow('test prompt injection resilience')=='chat'
    assert choose_workflow('research my knowledge')=='knowledge'
    assert choose_workflow('hi')=='chat'


def test_actual_exe_evidence(env,tmp_path):
    auto,store,_=env
    data=bytearray(256)
    data[:2]=b'MZ'
    data[0x3c:0x40]=(128).to_bytes(4,'little')
    data[128:132]=b'PE\x00\x00'
    data[132:134]=(0x8664).to_bytes(2,'little')
    src=tmp_path/'fixture.exe';src.write_bytes(data)
    store.put('source',{'path':str(src),'original_retained':True,'project_id':'project_lab',
                        'filename':'fixture.exe'},id='source_exe')
    result=auto.start(message='inspect my game exe',source_ids=['source_exe'],project_id='project_lab')
    record=result['result']['files'][0]
    assert record['architecture']=='x86-64'
    assert record['sha256']==hashlib.sha256(data).hexdigest()
    assert 'No exploit' in record['not_verified']
    assert result['status']=='completed'
    assert store.get('work_task',result['id'])['result']['files'][0]['sha256']==record['sha256']


def test_apk_archive_is_not_executed_or_unzipped(env,tmp_path):
    auto,store,_=env
    path=tmp_path/'game.apk'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('AndroidManifest.xml','manifest')
        z.writestr('classes.dex',b'\x00'*16)
        z.writestr('lib/arm64-v8a/libtest.so','binary')
    store.put('source',{'path':str(path),'original_retained':True,
       'project_id':None,'filename':'game.apk'},id='source_apk')
    result=auto.start(message='analyze this APK',source_ids=['source_apk'])
    assert result['result']['files'][0]['format']=='Android APK / ZIP'
    assert result['result']['files'][0]['observations'][1]['evidence']['dex_files']==1
    assert list(tmp_path.iterdir())==[path] or set(p.name for p in tmp_path.iterdir())=={'game.apk','db'}


def test_source_cannot_cross_projects(env,tmp_path):
    auto,store,_=env
    store.put('project',{'name':'other'},id='project_other')
    source=tmp_path/'private';source.write_bytes(b'MZ')
    store.put('source',{'path':str(source),'original_retained':True,
                       'project_id':'project_other'},id='source_private')
    with pytest.raises(ValueError,match='unavailable'):
        auto.plan(message='inspect my game',source_ids=['source_private'],project_id='project_lab')


def test_website_requires_permission_scope_and_uses_existing_runner(env):
    auto,store,app=env
    request={'message':'audit the website','project_id':'project_lab'}
    plan=auto.plan(**request)
    assert plan['ready'] and plan['requires_approval']
    with pytest.raises(ValueError,match='Confirm authorization'):
        auto.start(**request)
    assert app.website_audit.calls==[]
    started=auto.start(**request,approved=True)
    assert started['id']=='task_web'
    assert app.website_audit.calls[0]['approved'] is True
    assert app.website_audit.calls[0]['project_id']=='project_lab'
    with pytest.raises(ValueError,match='Missing setup'):
        auto.start(message='audit the website')


def test_tool_installer_requires_explicit_approval_and_allowlist(env,monkeypatch):
    _,store,_=env
    service=ToolInstaller(store)
    with pytest.raises(ValueError,match='approve'):
        service.install('ffmpeg')
    with pytest.raises(ValueError,match='approved installation'):
        service.install('freeform-tool',approved=True)
    monkeypatch.setattr('bughunter.tool_installer.platform.system',lambda:'Linux')
    with pytest.raises(ValueError,match='macOS'):
        service.install('ffmpeg',approved=True)
