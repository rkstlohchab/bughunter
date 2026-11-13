import threading,time
from pathlib import Path
import pytest
from bughunter.app import Application

@pytest.fixture
def app(tmp_path):
 a=Application(tmp_path,primary=False)
 yield a
 a.close()

def source(app,status='queued'):
 return app.store.put('source',{'title':'Course','status':status,'path':str(Path(__file__)),'original_retained':True,'media_control':'resume','media_progress':{'phase':'frames','analyzed':20,'total':100}})

def test_pause_and_continue_keep_checkpoints_and_queue(app):
 r=source(app);app.dispatch('sources.control',{'id':r['id'],'operation':'pause'})
 assert app.store.get('source',r['id'])['media_control']=='pause'
 assert app.knowledge._claim(r['id']) is None
 app.dispatch('sources.control',{'id':r['id'],'operation':'resume'})
 saved=app.store.get('source',r['id']);assert saved['media_progress']['analyzed']==20 and saved['media_control']=='resume'
 assert app.knowledge._claim(r['id'])['status']=='processing'

def test_pause_blocks_work_and_stop_all_unblocks_cancellation(app):
 r=source(app,'processing');app.knowledge.control_source(r['id'],'pause');seen=[]
 def work():
  try:app.knowledge._check_cancelled(r['id']);seen.append('ran')
  except InterruptedError:seen.append('cancelled')
 t=threading.Thread(target=work);t.start();time.sleep(.2);assert not seen
 app.stop_all();t.join(timeout=2);assert seen==['cancelled']
 with pytest.raises(ValueError,match='Resume tasks'):app.knowledge.control_source(r['id'],'resume')

def test_continue_partial_reuses_cached_media_and_respects_provider_wait(app):
 r=source(app,'partial');folder=app.store.root/'artifacts'/r['id'];folder.mkdir();timeline=folder/'media_timeline.json';timeline.write_text('[{"modality":"vision","start":0,"text":"saved"}]')
 app.knowledge.control_source(r['id'],'resume');assert timeline.exists() and app.store.get('source',r['id'])['status']=='queued'
 row=app.store.get('source',r['id']);app.store.put('source',{**row,'provider_wait':{'retry_at':time.time()+500}},id=r['id'])
 app.knowledge.control_source(r['id'],'resume');assert app.knowledge._claim(r['id']) is None

def test_saved_progress_stays_visible_while_rechecking_existing_frames(app):
 r=source(app,'processing');app.knowledge._processing.source_id=r['id'];app.knowledge._processing.saved_visual_times=set(range(80))
 row=app.knowledge._update(r['id'],media_progress={'phase':'frames','analyzed':5,'total':100})
 assert row['media_progress']['saved_analyzed']==80 and row['media_progress']['analyzed']==5

def test_pdf_image_description_is_cited_reused_and_preserves_unreadable_warning(app,tmp_path):
 import pymupdf,json
 path=tmp_path/'diagram.pdf'
 with pymupdf.open() as doc:doc.new_page();doc.save(path)
 r=app.store.put('source',{'title':'Diagram','status':'partial','path':str(path),'content_hash':'fixture-hash'})
 folder=app.store.root/'artifacts'/r['id'];folder.mkdir()
 (folder/'pdf-page-analysis.json').write_text(json.dumps({'source_hash':'fixture-hash','pages':[{'page':1,'text':'Visible: central node and two branches. Inference: organize work.','provider':'codex_subscription','model':'gpt-6.1-sol','warnings':['Page 1 has unreadable small labels.']}]}))
 segments,warnings=app.knowledge._extract_pdf(path,source_id=r['id'])
 assert any(s['modality']=='vision' and s['page']==1 for s in segments)
 assert 'Page 1 has unreadable small labels.' in warnings
 assert not any('no extracted text' in w for w in warnings)
 (folder/'pdf-page-analysis.json').write_text(json.dumps({'source_hash':'different-hash','pages':[{'page':1,'text':'Stale description'}]}))
 segments,warnings=app.knowledge._extract_pdf(path,source_id=r['id'])
 assert not segments and any('no extracted text' in w for w in warnings)

def test_shutdown_requeues_processing_while_operator_stop_remains_cancelled(app,monkeypatch):
 r=source(app)
 def shutdown_extract(*args):app.knowledge._stop.set();raise InterruptedError('ChatGPT task stopped.')
 monkeypatch.setattr(app.knowledge,'_extract',shutdown_extract)
 result=app.knowledge.process_source(r['id']);assert result['status']=='queued' and result['stage']=='awaiting_restart' and result['error'] is None
 app.knowledge._stop.clear()
 def operator_stop(*args):app.store.set_setting('emergency_stop',True);raise InterruptedError('ChatGPT task stopped.')
 monkeypatch.setattr(app.knowledge,'_extract',operator_stop)
 result=app.knowledge.process_source(r['id']);assert result['status']=='cancelled'

@pytest.mark.parametrize('operation',['retry','resume'])
def test_failed_source_retry_has_truthful_queue_stage_and_retains_cause(app,monkeypatch,operation):
 r=source(app)
 def full_disk(*args):raise OSError(28,'No space left on device')
 monkeypatch.setattr(app.knowledge,'_extract',full_disk)
 failed=app.knowledge.process_source(r['id']);assert failed['status']=='error' and failed['stage']=='failed'
 folder=app.store.root/'artifacts'/r['id'];timeline=folder/'media_timeline.json';timeline.write_text('[{"start":0,"text":"saved"}]')
 queued=app.knowledge.retry_source(r['id']) if operation=='retry' else app.knowledge.control_source(r['id'],'resume')
 assert queued['status']=='queued' and queued['stage']=='queued' and queued['error'] is None and queued['worker_pid'] is None
 assert queued['last_failure']['error']==failed['error'] and queued['last_failure']['at']==failed['updated_at']
 assert queued['media_progress']['analyzed']==20 and timeline.exists()
 claimed=app.knowledge._claim(r['id']);assert claimed['status']=='processing' and claimed['stage']=='extracting'
 assert claimed['last_failure']==queued['last_failure'] and claimed['media_progress']['analyzed']==20

def test_resume_legacy_failed_queue_recovers_failure_audit_without_losing_progress(app):
 r=source(app);app.store.put('source',{**r,'stage':'failed','error':None,'media_control':'pause'},id=r['id'])
 app.store.event('source.failed',{'source_id':r['id'],'error':'[Errno 28] No space left on device'})
 queued=app.knowledge.control_source(r['id'],'resume')
 assert queued['status']=='queued' and queued['stage']=='queued' and queued['error'] is None
 assert queued['last_failure']['error']=='[Errno 28] No space left on device'
 assert queued['media_progress']==r['media_progress']
 assert app.knowledge._claim(r['id'])['stage']=='extracting'

@pytest.mark.parametrize('changed',['source','generation'])
def test_guarded_resume_preserves_a_newer_pause_or_stop(app,changed):
 r=source(app);paused=app.knowledge.control_source(r['id'],'pause');generation=app.store.setting('stop_generation',0)
 if changed=='source':app.knowledge.control_source(r['id'],'pause')
 else:app.store.set_setting('stop_generation',generation+1)
 with pytest.raises(ValueError,match='preserved'):
  app.knowledge.control_source(r['id'],'resume',expected_updated_at=paused['updated_at'],expected_generation=generation)
 assert app.store.get('source',r['id'])['media_control']=='pause'
