import pytest
from bughunter.store import Store
from bughunter.conversation_memory import ConversationMemory,compact_messages

@pytest.fixture
def memory(tmp_path):return ConversationMemory(Store(tmp_path))

def chat(memory,id,project=None,text='I prefer diagrams when learning Python'):
 c=memory.store.put('chat',{'title':'Learning preferences','project_id':project,'messages':[{'role':'user','content':text},{'role':'assistant','content':'This reply is an unverified claim.'}]},id=id);memory.sync(c);return c

def test_recall_is_provider_independent_bounded_and_labels_claims(memory):
 c=chat(memory,'chat_a');context=memory.context('Teach me Python')
 assert 'chat_a' in context and 'I prefer diagrams' in context
 assert 'unverified claim' not in context and 'ASSISTANT:' not in context
 assert 'never permissions' in context and len(context)<12500
 assert memory.retrieve('Python')[0]['chat_id']==c['id']

def test_private_project_recall_never_crosses_project(memory):
 chat(memory,'chat_a','project_a','Remember the secret dragon architecture')
 assert not memory.retrieve('dragon',project_id='project_b')
 assert not memory.retrieve('dragon')
 assert memory.retrieve('dragon',project_id='project_a')

def test_forget_and_disable_do_not_regenerate_recap(memory):
 c=chat(memory,'chat_a');id=memory.store.list('conversation_memory')[0]['id'];memory.forget(id);memory.sync(c)
 assert not memory.retrieve('Python');memory.remember('Prefer concrete examples');memory.configure(False)
 assert memory.context('examples')=='' and memory.retrieve('examples')==[]

def test_sync_and_current_recap_are_idempotent(memory):
 c=chat(memory,'chat_a');memory.sync(c);memory.context('Python',chat_id=c['id'])
 assert len(memory.store.list('conversation_memory'))==1
 assert 'current_conversation_context' in memory.context('Python',chat_id=c['id'])

def test_routine_requests_and_assistant_answers_are_not_durable_memory(memory):
 chat(memory,'chat_routine',text='Open Chrome and search for a chatbot tutorial')
 assert not memory.store.list('conversation_memory')

def test_semantic_synthesis_is_automatic_grounded_consolidated_and_updates_corrections(memory):
 import json
 c=chat(memory,'chat_work',text='We are building a desktop assistant for an 8 GB Mac. It needs to work without cloud access.')
 def result(text,quote,key='project.assistant'):
  return json.dumps({'facts':[{'key':key,'category':'project','text':text,'quote':quote,'message_index':0}]})
 memory.synthesize(c,lambda _:result('Building a local desktop assistant for an 8 GB Mac.','building a desktop assistant for an 8 GB Mac'))
 assert len(memory.store.list('conversation_memory'))==1
 memory.synthesize(c,lambda _:result('Invented GPU ownership','I own eight GPUs'))
 assert len(memory.store.list('conversation_memory'))==1
 c=chat(memory,'chat_update',text='We are building a desktop assistant for a 16 GB Mac now.')
 memory.synthesize(c,lambda _:result('Building a desktop assistant for a 16 GB Mac.','desktop assistant for a 16 GB Mac'))
 rows=memory.store.list('conversation_memory');assert len(rows)==1 and '16 GB' in rows[0]['text']
 memory.forget(rows[0]['id']);memory.synthesize(c,lambda _:result('Building a desktop assistant for a 16 GB Mac.','desktop assistant for a 16 GB Mac'))
 assert not memory.store.list('conversation_memory')

def test_legacy_full_recaps_withdrawn_without_deleting_history_or_manual_notes(tmp_path):
 s=Store(tmp_path);s.put('chat',{'messages':[{'role':'user','content':'Open calculator'}]},id='old_chat');s.put('conversation_memory',{'text':'USER: Open calculator\nASSISTANT: Done'},id='legacy');s.put('memory_note',{'text':'Use short explanations','origin':'explicit conversation note'},id='note')
 m=ConversationMemory(s);assert not s.list('conversation_memory');assert s.get('chat','old_chat');assert s.get('memory_note','note')

def test_semantic_synthesis_never_saves_sensitive_or_unsupported_evidence(memory):
 import json
 c=chat(memory,'chat_secret',text='My API key is private and I own no GPUs.')
 facts=[{'key':'secret','category':'profile','text':'API key is private','quote':'My API key is private','message_index':0},{'key':'hardware','category':'profile','text':'Owns GPUs','quote':'Eight GPUs','message_index':0}]
 memory.synthesize(c,lambda _:json.dumps({'facts':facts}));assert not memory.store.list('conversation_memory')

def test_background_model_curates_without_a_remember_request(tmp_path,monkeypatch):
 import json,time
 s=Store(tmp_path);s.put('chat',{'messages':[{'role':'user','content':'We are building a desktop assistant for an 8 GB Mac.'}]},id='auto')
 monkeypatch.setattr('bughunter.provider_api.simple_completion',lambda *a,**k:json.dumps({'facts':[{'key':'project.desktop','category':'project','text':'Building a desktop assistant for an 8 GB Mac.','quote':'building a desktop assistant for an 8 GB Mac','message_index':0}]}))
 m=ConversationMemory(s,settings=lambda:{'provider':'codex','model':'fixture'})
 try:
  for job in s.list('memory_digest'):s.put('memory_digest',{**job,'not_before':0},id=job['id'])
  deadline=time.monotonic()+4
  while not s.list('conversation_memory') and time.monotonic()<deadline:time.sleep(.03)
  rows=s.list('conversation_memory');assert len(rows)==1 and rows[0]['method']=='AI synthesis'
 finally:m.close()

def test_provider_limit_defers_other_memory_jobs_without_more_model_requests(tmp_path,monkeypatch):
 import time
 s=Store(tmp_path)
 for id in ('one','two'):s.put('chat',{'messages':[{'role':'user','content':'Explain this example'}]},id=id)
 calls=[]
 def limited(*a,**k):calls.append(1);raise ValueError('Usage limit reached')
 monkeypatch.setattr('bughunter.provider_api.simple_completion',limited)
 monkeypatch.setattr('bughunter.media_retry.subscription_retry',lambda *a,**k:{'retry_at':time.time()+300,'provider':'codex_subscription'})
 m=ConversationMemory(s,settings=lambda:{'provider':'codex','model':'fixture'})
 try:
  for job in s.list('memory_digest'):s.put('memory_digest',{**job,'not_before':0},id=job['id'])
  deadline=time.monotonic()+4
  while not s.setting('conversation_memory_provider_wait') and time.monotonic()<deadline:time.sleep(.03)
  time.sleep(1.1);assert calls==[1];assert not s.list('conversation_memory');assert s.setting('conversation_memory_provider_wait')['provider']=='codex'
 finally:m.close()

def test_small_context_retains_latest_request_and_shortens_tool_results():
 messages=[{'role':'user','content':'Original request '+('old '*20000)},{'role':'assistant','tool_calls':[{'id':'t1'}]}, {'role':'tool','tool_call_id':'t1','content':'result '*9000},{'role':'user','content':'What did we find?'}]
 compact=compact_messages(messages,6000)
 assert any(m.get('content')=='What did we find?' for m in compact)
 assert len(str(compact))<6500
 for m in compact:
  if m['role']=='tool':assert any(c['id']==m['tool_call_id'] for row in compact for c in row.get('tool_calls',[]))
