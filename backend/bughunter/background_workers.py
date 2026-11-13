"""Bounded delegated tasks using the same provider tools, task grants and shared memory."""
import threading
from .store import now


class BackgroundWorkers:
 def __init__(self,app,recover=False):
  self.app=app;self.store=app.store;self._slots=threading.BoundedSemaphore(2);self._threads={};self._lock=threading.RLock()
  if recover:
   for row in self.store.list('background_worker'):
    if row.get('status') in {'queued','running'}:self.store.put('background_worker',{**row,'status':'interrupted','error':'Desktop restarted; saved work was retained.'},id=row['id'])

 def start(self,parent_id,prompt,computer=False):
  with self._lock:return self._start(parent_id,prompt,computer)

 def _start(self,parent_id,prompt,computer):
  parent=self.app.harness.check(parent_id)
  if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000:raise ValueError('Enter a subtask up to 6,000 characters.')
  if parent.get('parent_id'):raise ValueError('A background worker cannot create further workers.')
  active=[r for r in self.store.list('background_worker') if r.get('parent_id')==parent_id and r['status'] in {'queued','running'}]
  if len(active)>=3:raise ValueError('This task already has three background workers. Check their results first.')
  grants=list(parent['grants'])
  if not computer:grants=[grant for grant in grants if grant!='computer']
  if not grants:raise ValueError('This subtask has no app/file/command access. Allow the required task access first.')
  row=self.store.put('background_worker',{'parent_id':parent_id,'prompt':prompt.strip(),'status':'queued','grants':grants,'generation':parent['generation'],'created_at':now()})
  thread=threading.Thread(target=self._work,args=(row['id'],),daemon=True,name='background:'+row['id']);self._threads[row['id']]=thread;thread.start()
  return {'id':row['id'],'status':'queued','parent_id':parent_id}

 def _work(self,id):
  acquired=False
  try:
   row=self.store.get('background_worker',id)
   while not acquired:
    self._check(row);acquired=self._slots.acquire(timeout=.1)
   self._check(row)
   parent=self.app.harness.check(row['parent_id']);chat=self.store.get('chat',parent['chat_id']) or {}
   parent_run=self.store.get('operator_run',parent['run_id']) or {}
   task=self.app.harness.start(message=row['prompt'],grants=row['grants'],project_id=chat.get('project_id'),mode='task',
       execution_mode=parent.get('execution_mode','auto'),web_search_mode=parent.get('web_search_mode','auto'),background=True,parent_id=row['parent_id'],settings_override=parent_run.get('settings_snapshot'))
   self.store.put('background_worker',{**row,'status':'running','harness_id':task['id'],'run_id':task['run_id']},id=id)
   while True:
    self._check(row)
    task=self.app.harness.get(task['id'])
    if task['status']!='running':
     latest=self.store.get('background_worker',id)
     self.store.put('background_worker',{**latest,'status':'completed' if task['status']=='awaiting_user' else task['status'],
       'answer':next((m['content'] for m in reversed(task['session'].get('messages',[])) if m.get('role')=='assistant'),''),'finished_at':now(),'error':task.get('error')},id=id)
     return
    threading.Event().wait(.3)
  except Exception as error:
   latest=self.store.get('background_worker',id)
   if latest.get('harness_id'):
    try:self.app.harness.stop(latest['harness_id'])
    except Exception:pass
   cancelled=isinstance(error,InterruptedError) or self.store.setting('emergency_stop',False) or latest.get('status')=='cancelled' or latest.get('generation')!=self.store.setting('stop_generation',0)
   self.store.put('background_worker',{**latest,'status':'cancelled' if cancelled else 'failed','error':str(error),'finished_at':now()},id=id)
  finally:
   if acquired:self._slots.release()

 def _check(self,row):
  current=self.store.get('background_worker',row['id'])
  if current.get('status')=='cancelled':raise InterruptedError('Background task stopped.')
  self.app.harness.check(row['parent_id'])

 def get(self,id,parent_id=None):
  row=self.store.get('background_worker',id)
  if not row or (parent_id and row['parent_id']!=parent_id):raise ValueError('Background worker not found in this task.')
  return {k:v for k,v in row.items() if k not in {'generation'}}

 def collect(self,parent_id,reported,check):
  """Wait without model polling, then return each worker's bounded cited outcome once."""
  while True:
   check()
   rows=[r for r in self.store.list('background_worker') if r.get('parent_id')==parent_id and r['id'] not in reported]
   if not any(r['status'] in {'queued','running'} for r in rows):
    return [{'id':r['id'],'task':r['prompt'],'status':r['status'],'harness_id':r.get('harness_id'),'answer':r.get('answer','')[:6000],'error':r.get('error')} for r in rows]
   threading.Event().wait(.1)

 def stop_children(self,parent_id):
  for row in self.store.list('background_worker'):
   if row.get('parent_id')==parent_id and row['status'] in {'queued','running'}:
    self.store.put('background_worker',{**row,'status':'cancelled'},id=row['id'])
    if row.get('harness_id'):
     try:self.app.harness.stop(row['harness_id'])
     except Exception:pass

 def cancel_all(self):
  for row in self.store.list('background_worker'):
   if row.get('status') in {'queued','running'}:self.store.put('background_worker',{**row,'status':'cancelled'},id=row['id'])
