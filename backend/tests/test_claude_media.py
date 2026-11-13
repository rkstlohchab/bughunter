import json
import subprocess

import pytest
from PIL import Image

from bughunter import claude_media
from bughunter.app import Application


@pytest.fixture
def picture(tmp_path):
    path=tmp_path/'frame.png';Image.new('RGB',(8,8),'red').save(path)
    return path


def test_image_message_is_bounded_and_rejects_nonimages(picture,tmp_path):
    message=claude_media.image_message('A tutorial.',[picture])
    assert message['message']['content'][1]['source']['media_type']=='image/png'
    assert message['parent_tool_use_id'] is None
    with pytest.raises(ValueError):claude_media.image_message('x',[])
    with pytest.raises(ValueError):claude_media.image_message('x',[picture]*4)
    alias=tmp_path/'alias.png';alias.symlink_to(picture)
    with pytest.raises(ValueError):claude_media.image_message('x',[alias])
    text=tmp_path/'text.txt';text.write_text('not an image')
    with pytest.raises(ValueError):claude_media.image_message('x',[text])


def test_subscription_transport_sends_images_with_all_tools_disabled(picture,monkeypatch):
    captured={}
    class Process:
        returncode=0
        stdin=stdout=stderr=None
        def communicate(self,input=None,timeout=None):
            assert input is None
            return json.dumps({'type':'result','is_error':False,'result':'Visible facts: red image.\nNarrator says: absent.'}),''
        def poll(self):return 0
    def start(args,**kwargs):
        captured.update(args=args,options=kwargs)
        captured['message']=json.load(kwargs['stdin'])
        return Process()
    monkeypatch.setattr(claude_media,'connection_status',lambda:{'ready':True})
    monkeypatch.setattr(claude_media.shutil,'which',lambda _: '/fake/claude')
    monkeypatch.setattr(claude_media.subprocess,'Popen',start)
    monkeypatch.setenv('ANTHROPIC_API_KEY','must-not-follow-subscription')
    result=claude_media.describe('sonnet','Evidence only.','Describe.',[picture])
    args=captured['args']
    assert result.startswith('Visible facts')
    assert args[args.index('--tools')+1]==''
    assert '--safe-mode' in args and '--strict-mcp-config' in args
    assert '--no-session-persistence' in args and '--fallback-model' not in args
    assert 'ANTHROPIC_API_KEY' not in captured['options']['env']
    assert captured['message']['message']['content'][1]['type']=='image'


def test_cancel_terminates_cli_and_subscription_error_is_not_fallback(picture,monkeypatch):
    calls=[]
    class Process:
        pid=987654
        returncode=None
        stdin=stdout=stderr=None
        def communicate(self,*args,**kwargs):
            raise subprocess.TimeoutExpired('claude',.1)
        def poll(self):return self.returncode
        def wait(self,timeout=None):self.returncode=-15
    process=Process()
    monkeypatch.setattr(claude_media,'connection_status',lambda:{'ready':True})
    monkeypatch.setattr(claude_media.shutil,'which',lambda _: '/fake/claude')
    monkeypatch.setattr(claude_media.subprocess,'Popen',lambda *a,**k:process)
    monkeypatch.setattr(claude_media.os,'killpg',lambda *a:calls.append(a))
    checked=[]
    def cancel():
        checked.append(True)
        if len(checked)>3:raise InterruptedError('Stop All')
    with pytest.raises(InterruptedError,match='Stop All'):
        claude_media.describe('sonnet','Evidence','Describe',[picture],cancel_check=cancel)
    assert calls and process.returncode==-15


def test_claude_routing_and_consent_leave_chat_and_local_runtime_alone(tmp_path,picture,monkeypatch):
    app=Application(tmp_path/'app',primary=False)
    try:
        before=app.settings.get()
        with pytest.raises(ValueError,match='Allow'):
            app.media.configure({'vision_provider':'claude_subscription'})
        config=app.media.configure({'vision_provider':'claude_subscription','claude_vision_consent':True})
        seen=[]
        def describe(model,system,prompt,images,**kwargs):
            seen.append((model,images,prompt));kwargs['cancel_check']()
            return 'Visible facts: A red image.\nNarrator says: No narration supplied.'
        monkeypatch.setattr(claude_media,'describe',describe)
        monkeypatch.setattr(app.media,'start',lambda *a,**k:pytest.fail('Unexpected local inference'))
        result=app.media.vision(picture,0,config=config)
        assert 'red image' in result and seen[0][0]=='sonnet'
        assert app.media._identity()['model']=='sonnet'
        assert app.settings.get()==before
    finally:app.shutdown()


@pytest.mark.parametrize('reported,works',[('claude-opus-4-7',True),('claude-opus-5-5',False)])
def test_pinned_opus_requires_actual_model_confirmation(picture,monkeypatch,reported,works):
    seen=[]
    class Process:
        returncode=0
        stdin=stdout=stderr=None
        def communicate(self,input=None,timeout=None):
            return json.dumps({'type':'result','is_error':False,'result':'Visible facts: red image.\nNarrator says: absent.',
                               'model_usage_since_last_result':{reported:{}}}),''
        def poll(self):return 0
    def start(args,**kwargs):
        seen.extend(args)
        return Process()
    monkeypatch.setattr(claude_media,'connection_status',lambda:{'ready':True})
    monkeypatch.setattr(claude_media.shutil,'which',lambda _:'/fake/claude')
    monkeypatch.setattr(claude_media.subprocess,'Popen',start)
    if works:assert claude_media.describe('claude-opus-4-7','Evidence','Describe',[picture])
    else:
        with pytest.raises(ValueError,match='pinned model'):
            claude_media.describe('claude-opus-4-7','Evidence','Describe',[picture])
    assert seen[seen.index('--model')+1]=='claude-opus-4-7'
    assert seen[seen.index('--effort')+1]=='medium'


def test_fable_5_2_can_be_selected_for_resources_and_video_with_supported_effort(tmp_path):
    from bughunter.source_models import validate
    app=Application(tmp_path,primary=False)
    try:
        profile=validate({'provider':'claude_subscription','model':'claude-fable-5-2','effort':'xhigh'},app.store)
        assert profile['model']=='claude-fable-5-2'
        config=app.media.configure({'vision_provider':'claude_subscription','claude_vision_model':'claude-fable-5-2','claude_vision_consent':True,'vision_effort':'high'})
        assert (config['claude_vision_model'],config['vision_effort'])==('claude-fable-5-2','high')
        assert 'claude-fable-5-2' in claude_media.MODEL_IDS
    finally:app.shutdown()
