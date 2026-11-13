import base64,threading,time
import pytest
from bughunter.store import Store
from bughunter.voice import VoiceService,clean_speech,MODELS

def test_fillers_optional_cleanup_and_no_recording_at_startup(tmp_path):
 v=VoiceService(Store(tmp_path));assert not v.status()['recording']
 assert clean_speech('Um, show uh the Python diagram')=='show the Python diagram'
 assert clean_speech('hmm show me the human function')=='hmm show me the human function'
 assert set(MODELS)=={'tiny','small','medium'}

def test_start_needs_verified_local_model_and_stop_latches(tmp_path):
 v=VoiceService(Store(tmp_path))
 with pytest.raises(ValueError,match='Set up'):v.start()
 v.store.set_setting('emergency_stop',True)
 with pytest.raises(ValueError,match='Resume'):v.start()
 assert v.stop('unknown')['stopped']

def test_audio_payload_limit_and_generation_checked(tmp_path):
 v=VoiceService(Store(tmp_path));v._sessions['voice_x']={'generation':0,'status':'ready'}
 with pytest.raises(ValueError):v.feed('voice_x','a'*90001)
 with pytest.raises(ValueError):v.feed('voice_x',base64.b64encode(b'1').decode())
 v._sessions.clear()

def test_setup_cancel_and_disk_guard(tmp_path,monkeypatch):
 v=VoiceService(Store(tmp_path));monkeypatch.setattr('bughunter.voice.shutil.disk_usage',lambda _:type('D',(),{'free':1})())
 with pytest.raises(ValueError,match='1 GiB'):v.setup('tiny')
 v.cancel_all();assert v._cancel.is_set() and not v._sessions

def test_select_rejects_uninstalled_model_and_active_recording(tmp_path):
 v=VoiceService(Store(tmp_path))
 with pytest.raises(ValueError,match='verify'):v.dispatch('voice.select',{'model':'medium'})
 path=tmp_path/'speech';path.mkdir();v.store.set_setting('voice_runtime',{'models':{'medium':{'ready':True,'path':str(path)}}})
 v._sessions['active']={}
 with pytest.raises(ValueError,match='microphone off'):v.dispatch('voice.select',{'model':'medium'})
 v._sessions.clear();assert v.dispatch('voice.select',{'model':'medium'})['selected']=='medium'
