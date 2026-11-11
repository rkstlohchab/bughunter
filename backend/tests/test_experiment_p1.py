"""P1 reproducible experiments: citations, fixture execution, failure and recovery."""
import json
import time
import zipfile

import pytest
from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    store = Store(tmp_path / "db")
    service = KnowledgeService(store, start_worker=False)
    monkeypatch.setattr(service, '_index_vectors', lambda _source: 'disabled')
    yield service
    service.shutdown()


def course(knowledge, text):
    import base64
    source = knowledge.import_source(filename='sample.md', content_base64=base64.b64encode(text.encode()).decode())
    source = knowledge.process_source(source['id'])
    assert source['status'] in ('ready','partial')
    candidates = knowledge.procedures.list(source_id=source['id'])
    assert candidates
    return candidates[0]


def complete(service, id):
    for _ in range(200):
        row = service.get(id)
        if row['status'] not in ('queued','running','stopping'):
            return row
        time.sleep(.01)
    raise AssertionError('Experiment worker did not finish')


def test_citations_and_graph_and_export(knowledge):
    proc = course(knowledge,'Step 1: Open the program.\nStep 2: Click the Run button.\nFinally verify the output.')
    service = knowledge.experiments
    assert [r['id'] for r in service.available(proc['id'])] == ['source_fidelity']
    row = complete(service,service.start(proc['id'],'source_fidelity')['id'])
    assert row['status']=='passed', row['error']
    assert row['verification_level']=='source_verified_only'
    assert row['observations'] and all(s['matched_original'] for s in row['observations'])
    assert knowledge.procedures.get(proc['id'])['verified'] is False
    assert any(edge['relation']=='tested_by_fixture' for edge in knowledge.graph(limit=1000)['edges'])
    archive = knowledge.portability().export_bundle(include_frames=False)
    with zipfile.ZipFile(archive['path']) as z:
        runs = [json.loads(line) for line in z.read('experiment_runs.jsonl').splitlines()]
        assert any(r['id']==row['id'] for r in runs)
        assert z.read('train.jsonl') == b''


def test_citation_drift_does_not_confirm_source(knowledge):
    proc=course(knowledge, 'Step 1: Open the test tool.\nStep 2: Click the verification button.')
    step=proc['steps'][0]
    with knowledge.store.connection(immediate=True) as db:
        db.execute('UPDATE knowledge_chunks SET text=?, original_text=? WHERE id=?',('Changed source text','Changed source text',step['chunk_id']))
    row=complete(knowledge.experiments,knowledge.experiments.start(proc['id'],'source_fidelity')['id'])
    assert row['status']=='failed'
    assert any(not s['matched_original'] for s in row['observations'])
    assert row['hypotheses']
    assert knowledge.procedures.get(proc['id'])['status']=='candidate'


def test_allowlist_and_approval(knowledge):
    proc=course(knowledge, 'Step 1: Scan the memory.\nStep 2: Verify pointer after restart.')
    available=[r['id'] for r in knowledge.experiments.available(proc['id'])]
    assert 'memory_restart_lab' in available
    with pytest.raises(ValueError,match='Confirm'):
        knowledge.experiments.start(proc['id'],'memory_restart_lab')
    with pytest.raises(ValueError,match='does not support'):
        knowledge.experiments.start(proc['id'],'checkout_integrity_lab',approve_lab=True)
    with pytest.raises(ValueError,match='does not support'):
        knowledge.experiments.start(proc['id'],'arbitrary_shell',approve_lab=True)


def test_lab_pass_failure_retry_and_no_auto_promotion(knowledge,monkeypatch):
    proc=course(knowledge,'Step 1: Test the checkout price in the cart.\nStep 2: Verify the quote.')
    service=knowledge.experiments
    def fail(id,recipe):
        return {'passed':False,'observations':[{'exit_code':9}], 'error':'fixture failed','hypotheses':['Check test dependency.']}
    monkeypatch.setattr(service,'_run_lab',fail)
    row=complete(service,service.start(proc['id'],'checkout_integrity_lab',approve_lab=True)['id'])
    assert row['status']=='failed' and row['hypotheses']
    monkeypatch.setattr(service,'_run_lab',lambda id,recipe:{'passed':True,'observations':[{'exit_code':0}],'error':None,'hypotheses':[]})
    retry=complete(service,service.retry(row['id'],approve_lab=True)['id'])
    assert retry['status']=='passed'
    assert retry['verification_level']=='lab_reproduced_only'
    assert service.get(row['id'])['status']=='failed'
    assert knowledge.procedures.get(proc['id'])['verified'] is False
    z=knowledge.portability().export_bundle(include_frames=False)
    with zipfile.ZipFile(z['path']) as archive:
        failures=archive.read('experiment_failures.jsonl').decode()
        assert row['id'] in failures
        assert retry['id'] not in failures


def test_cancel_and_recovery(knowledge, monkeypatch):
    proc=course(knowledge,'Step 1: Scan a memory value.\nStep 2: Restart and verify the pointer.')
    service=knowledge.experiments
    def block(id,recipe):
        for _ in range(150):
            with service._lock:
                if id in service._cancels:break
            time.sleep(.005)
        return {'passed':True,'observations':[], 'error':None,'hypotheses':[]}
    monkeypatch.setattr(service,'_run_lab',block)
    run=service.start(proc['id'],'memory_restart_lab',approve_lab=True)
    service.stop(run['id'])
    result=complete(service,run['id'])
    assert result['status']=='cancelled'
    assert result['verification_level']=='not_verified'
    interrupted=knowledge.store.put('experiment_run',{'id':'experiment_interrupted','status':'running','recipe':'source_fidelity'})
    from bughunter.experiment_p1 import ExperimentP1
    recovered=ExperimentP1(knowledge,run_workers=True)
    assert recovered.get(interrupted['id'])['status']=='interrupted'


def test_safe_excerpt_redacts_secrets():
    from bughunter.experiment_p1 import _safe_excerpt
    output=_safe_excerpt('token=myvalue bearer abc123 password=12345')
    assert 'myvalue' not in output and 'abc123' not in output and '12345' not in output


def test_source_check_batch_and_failure_insights(knowledge):
    a=course(knowledge,'Step 1: Open the test utility.\nStep 2: Verify the memory value.')
    report=knowledge.dispatch('experiments.check_source',{'source_id':a['source_id']})
    assert report['started']>=1
    rows=[complete(knowledge.experiments,id) for id in report['experiment_ids']]
    assert all(r['status']=='passed' for r in rows)
    assert knowledge.dispatch('experiments.insights',{})==[]


def test_real_fixture_process_evidence(knowledge):
    proc=course(knowledge,'Step 1: Scan the memory for health.\nStep 2: Verify the pointer after restart.')
    job=knowledge.experiments.start(proc['id'],'memory_restart_lab',approve_lab=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        result=knowledge.experiments.get(job['id'])
        if result['status'] not in ('queued','running','stopping'):
            break
        time.sleep(.05)
    import sys
    if sys.platform == 'darwin' and result['status'] == 'failed' and 'task_for_pid' in str(result.get('observations', [])):
        pytest.skip('macOS denied debugger access to the disposable external process; failure evidence was retained.')
    assert result['status']=='passed', result.get('error')
    assert result['observations'][0]['exit_code']==0
    assert 'verified_on_two_lab_processes' in result['observations'][0]['output_excerpt']
    assert knowledge.procedures.get(proc['id'])['verified'] is False


def test_live_checkout_fixture_process_evidence(knowledge):
    proc=course(knowledge,'Step 1: Check checkout price manipulation.\nStep 2: Verify the cart quote.')
    job=knowledge.experiments.start(proc['id'],'checkout_integrity_lab',approve_lab=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        result=knowledge.experiments.get(job['id'])
        if result['status'] not in ('queued','running','stopping'):
            break
        time.sleep(.05)
    assert result['status']=='passed',result.get('error')
    assert result['observations'][0]['exit_code']==0
    assert 'passed' in result['observations'][0]['output_excerpt']
    assert knowledge.procedures.get(proc['id'])['verified'] is False


def test_deleted_source_does_not_leave_experiment_evidence(knowledge):
    proc=course(knowledge,'Step 1: Open the test tool.\nStep 2: Verify its output.')
    done=complete(knowledge.experiments,knowledge.experiments.start(proc['id'],'source_fidelity')['id'])
    assert done['status']=='passed'
    knowledge.delete_source(proc['source_id'])
    assert knowledge.experiments.list(procedure_id=proc['id'])==[]
