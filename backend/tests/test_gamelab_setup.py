from types import SimpleNamespace
from bughunter.gamelab_setup import inspect_setup


def test_missing_device_does_not_require_apk(monkeypatch):
 monkeypatch.setattr('bughunter.gamelab_setup.executable',lambda name:None)
 result=inspect_setup(SimpleNamespace())
 assert result['apk_required'] is False and result['devices']==[]
 assert result['missing_tools']==['adb','scrcpy']
 assert result['runtime_analysis']['offsets_verified'] is False


def test_connected_and_unauthorized_devices_are_reported_separately(monkeypatch):
 monkeypatch.setattr('bughunter.gamelab_setup.executable',lambda name:'/fake/'+name)
 monkeypatch.setattr('bughunter.gamelab_setup.run',lambda *args,**kwargs:{'exit_code':0,'stdout':'List of devices attached\nphone1 device model:Fixture\nemulator-5554 unauthorized\n','stderr':''})
 rows=inspect_setup(SimpleNamespace())['devices']
 assert [row['state'] for row in rows]==['device','unauthorized']
 assert rows[1]['emulator'] is True
