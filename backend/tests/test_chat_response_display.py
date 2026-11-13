from types import SimpleNamespace
from bughunter.chat import ChatService
from bughunter.store import Store


def fixture(tmp_path):
 store=Store(tmp_path)
 service=ChatService(store,None,tasks=SimpleNamespace(dispatch=lambda *args:{'id':'run_one','status':'completed','summary':'Final reply'}))
 store.put('run',{'status':'completed','summary':'Reading context.\n\nFinal reply','events':[{'type':'assistant','text':'Reading context.'},{'type':'tool','text':'get_project'},{'type':'assistant','text':'Final reply'}]},id='run_one')
 store.put('chat',{'title':'Question','task_id':'run_one','task_ids':['run_one'],'messages':[{'role':'user','content':'Question'},{'role':'assistant','content':'','run_id':'run_one','task_id':'run_one','pending':True}]},id='chat_one')
 return service,store


def test_final_reply_is_once_and_finalization_is_idempotent(tmp_path):
 service,store=fixture(tmp_path)
 for _ in range(3):
  service.finalize({'id':'chat_one','run_id':'run_one'})
  service.task_result({'id':'chat_one','task_id':'run_one'})
 chat=service.dispatch('chat.get',{'id':'chat_one'})
 assert [m['content'] for m in chat['messages']]==['Question','Final reply']
 assert store.get('run','run_one')['summary']=='Reading context.\n\nFinal reply'


def test_legacy_duplicate_is_repaired_without_discarding_evidence(tmp_path):
 service,store=fixture(tmp_path)
 chat=store.get('chat','chat_one');chat['messages'][1]={'role':'assistant','content':'Reading context.\n\nFinal reply','run_id':'run_one','task_id':'run_one'}
 chat['messages'].append({'role':'assistant','content':'Question: completed.\nFinal reply','task_id':'run_one'})
 store.put('chat',chat,id='chat_one')
 assert [m['content'] for m in service.dispatch('chat.get',{'id':'chat_one'})['messages']]==['Question','Final reply']
 assert len(store.get('run','run_one')['events'])==3
