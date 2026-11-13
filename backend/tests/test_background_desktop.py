import pytest
from bughunter.store import Store
from bughunter.desktop_bridge import DesktopBridge

@pytest.mark.parametrize('name,args',[('desktop_press',{'key':'ENTER'}),('desktop_launch',{'activate':True}),('desktop_click',{'headed':True}),('desktop_drag',{})])
def test_background_rejects_focus_stealing_before_action(name,args):
 with pytest.raises(PermissionError):DesktopBridge.background_guard(name,args,True)
 DesktopBridge.background_guard(name,args,False)

def test_hidden_background_cursor_does_not_enable_global_overlay(tmp_path,monkeypatch):
 d=DesktopBridge(Store(tmp_path));calls=[]
 async def mcp(op,name,args,**kw):
  calls.append((name,args));return {'structuredContent':{'ok':True,'data':{'session_id':'fixture','rendered':True}}}
 monkeypatch.setattr(d,'_mcp',mcp);monkeypatch.setattr(d,'focused',lambda *args:False);monkeypatch.setattr(d,'_watch_focus',lambda:None)
 row=d.controller('op_test',{'number':1,'label':'Small model #1'},'Editor',background=True)
 assert not row['overlay_rendered']
 assert 'desktop_cursor_overlay_enable' not in [c[0] for c in calls]
 row=d._overlay(row,True)
 assert calls[-1][1]['size']==.65 and row['overlay_visible']
 d.end_controller('op_test');assert not d.controller_record('op_test')['active']

def test_background_launch_is_headless_and_focus_false(tmp_path,monkeypatch):
 d=DesktopBridge(Store(tmp_path));seen=[]
 monkeypatch.setattr(d,'tools',lambda:[{'name':'desktop_launch'}])
 async def mcp(op,name,args,**kw):seen.append(args);return {'content':[]}
 monkeypatch.setattr(d,'_mcp',mcp)
 d.call('desktop_launch',{'app':'Editor'},'op_test',background=True)
 assert seen==[{'app':'Editor','activate':False}]

def test_cursor_matches_only_the_target_window_when_known(tmp_path,monkeypatch):
 d=DesktopBridge(Store(tmp_path));rows=[{'id':'w_a','app_name':'Among Us','is_focused':False},{'id':'w_b','app_name':'Among Us','is_focused':True}]
 monkeypatch.setattr(d,'windows',lambda:rows)
 assert d.focused('AmongUs')
 assert not d.focused('AmongUs','w_a')
 assert d.focused('AmongUs','w_b')
 assert d.target_name({'window_id':'w_a'})=='Among Us'
