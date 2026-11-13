import asyncio
import base64
import json
import threading
import time
from contextlib import asynccontextmanager
import pytest
from bughunter.desktop_bridge import DesktopBridge
from bughunter.store import Store


def test_exact_discovered_tools_and_private_image_artifacts(tmp_path,monkeypatch):
 bridge=DesktopBridge(Store(tmp_path));bridge._catalog=[{'name':'desktop_screenshot'}];bridge._stamp=time.monotonic()
 async def mcp(*args,**kw):return {'content':[{'type':'image','data':base64.b64encode(b'fixture png').decode(),'mimeType':'image/png'}]}
 monkeypatch.setattr(bridge,'_mcp',mcp)
 image=bridge.call('desktop_screenshot',{},'oprun_fixture')['content'][0]
 assert image['type']=='image_artifact' and 'data' not in image
 assert __import__('pathlib').Path(image['path']).read_bytes()==b'fixture png'
 with pytest.raises(ValueError):bridge.call('invented_command',{},'oprun_fixture')
 with pytest.raises(ValueError):bridge.call('desktop_screenshot',{},'../../other')


def test_stop_cancels_pending_mcp_context(tmp_path,monkeypatch):
 bridge=DesktopBridge(Store(tmp_path));closed=threading.Event();started=threading.Event();errors=[]
 monkeypatch.setattr(bridge,'executable',lambda:'/fake/engine')
 @asynccontextmanager
 async def stdio(*args,**kwargs):
  try:yield (None,None)
  finally:closed.set()
 class Session:
  def __init__(self,*args,**kwargs):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def initialize(self):started.set();await asyncio.sleep(90)
 monkeypatch.setattr('mcp.client.stdio.stdio_client',stdio)
 monkeypatch.setattr('mcp.ClientSession',Session)
 def run():
  try:bridge.tools()
  except InterruptedError as error:errors.append(error)
 thread=threading.Thread(target=run);thread.start();assert started.wait(2)
 bridge.cancel_all();thread.join(2)
 assert not thread.is_alive() and errors and closed.is_set()
