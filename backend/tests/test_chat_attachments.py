import base64,io
import pytest
from bughunter.app import Application

@pytest.fixture
def app(tmp_path):
 a=Application(tmp_path,primary=False)
 yield a
 a.close()

def add(app,name,raw):return app.chat_attachments.add(name,base64.b64encode(raw).decode())

def test_text_attachment_stays_private_and_outside_knowledge(app):
 row=add(app,'notes.md',b'Explain my local app without indexing this reference.')
 assert row['kind']=='text' and app.store.list('source')==[]
 context=app.chat_attachments.context([row['id']],'Explain this note')
 assert 'without indexing' in context and 'not instructions' in context
 assert app.knowledge.search('without indexing')==[]

def test_attachment_scope_and_path_traversal_are_checked(app):
 row=add(app,'../../note.txt',b'private')
 assert row['path'].startswith(str(app.chat_attachments.root/row['id']))
 with pytest.raises(ValueError):app.chat_attachments.get(row['id'],'other_project')
 for name in ('..','.'):
  with pytest.raises(ValueError):add(app,name,b'content')

def test_nonvision_model_requires_explicit_vision_assistant(app,monkeypatch):
 from PIL import Image
 image=io.BytesIO();Image.new('RGB',(32,32),'red').save(image,'PNG');row=add(app,'image.png',image.getvalue())
 monkeypatch.setattr(app.settings,'get',lambda:{'provider':'deepseek','model':'text-only'})
 assert not app.chat_attachments.capability()['selected_image_transport']
 with pytest.raises(ValueError,match='local vision model'):app.chat_attachments.context([row['id']],'What color?')
 called=[]
 monkeypatch.setattr(app.chat_attachments,'describe',lambda row,question,settings,mode,check:called.append(mode) or {'text':'Observed: red square.','provider':'local','model':'vision-fixture'})
 text=app.chat_attachments.context([row['id']],'What color?',vision_mode='configured')
 assert 'red square' in text and called==['configured']
 app.chat_attachments.context([row['id']],'What color?',vision_mode='configured');assert len(called)==1
 assert app.store.list('source')==[]

def test_bound_reference_is_retained_when_removed_from_a_draft(app):
 row=add(app,'code.py',b'print("hello")');chat=app.store.put('chat',{'title':'Reference','messages':[]});app.chat_attachments.bind([row['id']],chat)
 assert not app.chat_attachments.remove(row['id'])['deleted']
 assert app.chat_attachments.get(row['id'])

def test_chat_agent_receives_private_context_without_importing_a_source(app,monkeypatch):
 row=add(app,'code.py',b'print("reference only")');seen={}
 def start(**kw):seen.update(kw);return {'id':'run_fixture','status':'running'}
 monkeypatch.setattr(app.runs,'start_agent',start)
 result=app.chat.agent({'message':'Explain this code','attachment_ids':[row['id']]})
 assert result['session']['attachment_ids']==[row['id']]
 assert 'reference only' in seen['conversation_context'] and seen['source_ids']==[]
 assert app.store.list('source')==[]

def test_local_image_relay_unloads_resident_text_model_under_the_inference_lease(app,monkeypatch):
 from PIL import Image
 image=io.BytesIO();Image.new('RGB',(12,12),'red').save(image,'PNG');row=add(app,'red.png',image.getvalue());calls=[]
 monkeypatch.setattr(app.media,'config',lambda:{'vision_provider':'local','vision_model':'vision_fixture'})
 class Response:
  def __init__(self,data):self.data=data
  def raise_for_status(self):pass
  def json(self):return self.data
 class Client:
  def __init__(self,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def get(self,url):calls.append((url,None));return Response({'models':[{'name':'text9b'}]})
  async def post(self,url,json):
   calls.append((url,json))
   return Response({'capabilities':['vision']} if url.endswith('/show') else {'message':{'content':'Observed a red square.'}} if url.endswith('/chat') else {})
 monkeypatch.setattr('httpx.AsyncClient',Client)
 result=app.chat_attachments.describe(row,'What color?',{'provider':'deepseek','model':'text'},'configured',lambda:None)
 assert result['text']=='Observed a red square.'
 assert calls[1][0].endswith('/api/generate') and calls[1][1]=={'model':'text9b','keep_alive':0}
 assert calls[-1][1]['keep_alive']==0
