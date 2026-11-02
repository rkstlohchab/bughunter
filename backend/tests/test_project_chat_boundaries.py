"""The selected project controls Chat's retrieved material and attachments."""
from unittest.mock import Mock
import base64
import pytest

from bughunter.chat import ChatService
from bughunter.knowledge import KnowledgeService
from bughunter.store import Store
import bughunter.chat as chat

class Settings:
    def get(self):
        return dict(provider='ollama',model='mock',api_base_url='http://127.0.0.1:11434',
                    api_budget_usd=0,input_price_per_million=0,output_price_per_million=0,
                    pricing_confirmed=False,local_only=True,custom_system_prompt='',provider_system_prompts={})


def test_chat_uses_only_selected_project_and_shared_sources(tmp_path,monkeypatch):
    store=Store(tmp_path/'db')
    for id in ('project_a','project_b'): store.put('project',{'name':id},id=id)
    knowledge=KnowledgeService(store,start_worker=False)
    private_a=knowledge.import_source(filename='a.md',content_base64=base64.b64encode(b'zebraalpha secret a').decode(),project_id='project_a')
    private_b=knowledge.import_source(filename='b.md',content_base64=base64.b64encode(b'zebrabeta secret b').decode(),project_id='project_b')
    for record in (private_a,private_b):knowledge.process_source(record['id'])
    seen=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            seen.append(json)
            response=Mock();response.json.return_value={'message':{'content':'Done'}}
            return response
    monkeypatch.setattr(chat.httpx,'Client',Client)
    service=ChatService(store,Settings(),knowledge)
    with pytest.raises(ValueError,match='another project'):
        service.send({'message':'Explain this','source_ids':[private_b['id']],'project_id':'project_a'})
    result=service.send({'message':'What is zebraalpha zebrabeta?','project_id':'project_a','source_ids':[private_a['id']]})
    system=seen[-1]['messages'][0]['content']
    assert 'zebraalpha' in system
    assert 'zebrabeta' not in system
    with pytest.raises(ValueError,match='different project'):
        service.send({'id':result['id'],'project_id':'project_b','message':'Switch'})
    assert service.dispatch('chat.list',{'project_id':'project_b'})==[]
    assert service.dispatch('chat.list',{'project_id':'project_a'})[0]['id']==result['id']
    knowledge.shutdown()
