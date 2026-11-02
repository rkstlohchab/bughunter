"""P6 skill memory must not confuse human review, source integrity and lab scope."""
import base64
import time
from types import SimpleNamespace
import pytest

from bughunter.store import Store
from bughunter.knowledge import KnowledgeService
from bughunter.learning_p4 import LearningP4
from bughunter.skill_memory_p6 import SkillMemoryP6


@pytest.fixture
def env(tmp_path,monkeypatch):
    store=Store(tmp_path/'data')
    knowledge=KnowledgeService(store,start_worker=False)
    monkeypatch.setattr(knowledge,'_index_vectors',lambda source: None)
    app=SimpleNamespace(store=store,knowledge=knowledge)
    app.learning_p4=LearningP4(app)
    app.skill_memory_p6=SkillMemoryP6(app)
    yield app
    knowledge.shutdown()


def lesson(app):
    text='Step 1: Find the health value in an offline game.\nStep 2: Verify the pointer chain after restarting.'
    source=app.knowledge.import_source(filename='lesson.md',content_base64=base64.b64encode(text.encode()).decode())
    app.knowledge.process_source(source['id'])
    return source,app.knowledge.procedures.list(source_id=source['id'])[0]


def checked(app,procedure):
    r=app.knowledge.experiments.start(procedure['id'],'source_fidelity')
    limit=time.monotonic()+8
    while time.monotonic()<limit:
        run=app.knowledge.experiments.get(r['id'])
        if run['status'] not in {'queued','running','stopping'}:
            assert run['status']=='passed',run
            return run
        time.sleep(.02)
    raise AssertionError('Citation run timeout')


def test_create_is_idempotent_and_candidate_not_certified(env):
    source,p=lesson(env)
    a=env.skill_memory_p6.create(p['id'])
    assert a['assessment']['level']=='candidate'
    assert not a['reviewed_at']
    assert env.skill_memory_p6.create(p['id'])['id']==a['id']
    with pytest.raises(ValueError,match='current passing citation'):
        env.skill_memory_p6.review(a['id'],confirm=True,summary='An example review of the cited lesson')


def test_source_checked_requires_real_run_and_explicit_operator_review(env):
    _,p=lesson(env)
    card=env.skill_memory_p6.create(p['id'])
    run=checked(env,p)
    current=env.skill_memory_p6.get(card['id'])
    assert current['assessment']['level']=='source_checked'
    assert current['assessment']['citation_run_id']==run['id']
    with pytest.raises(ValueError,match='explicit desktop review'):
        env.skill_memory_p6.review(card['id'],summary='Only the cited passage was checked.')
    reviewed=env.skill_memory_p6.review(card['id'],confirm=True,summary='Only the original lesson quotations were checked.',recheck_days=15)
    assert reviewed['reviewed_at'] and reviewed['assessment']['level']=='source_checked'
    assert reviewed['assessment']['recheck_due'] is False
    assert reviewed['review_history'][0]['citation_run_id']==run['id']


def test_stale_after_source_checksum_or_steps_change(env):
    source,p=lesson(env);card=env.skill_memory_p6.create(p['id']);checked(env,p)
    env.skill_memory_p6.review(card['id'],confirm=True,summary='Evidence of source fidelity, not target verification.')
    changed={**env.store.get('source',source['id']),'content_hash':'0'*64}
    env.store.put('source',changed,id=source['id'])
    stale=env.skill_memory_p6.get(card['id'])
    assert stale['assessment']['level']=='stale'
    assert not stale['steps']
    with pytest.raises(ValueError,match='current passing citation'):
        env.skill_memory_p6.review(card['id'],confirm=True,summary='Pretend to confirm the old evidence.')
    with pytest.raises(ValueError,match='source changed'):
        env.skill_memory_p6.recheck(card['id'])


def test_drift_on_step_change_without_checksum_change(env):
    _,p=lesson(env);card=env.skill_memory_p6.create(p['id']);checked(env,p)
    p2={**p,'steps':[dict(p['steps'][0],quote='Modified text')]}
    env.store.put('course_procedure',p2,id=p['id'])
    assert env.skill_memory_p6.get(card['id'])['assessment']['level']=='stale'


def test_failed_or_old_evidence_cannot_promote(env):
    source,p=lesson(env);card=env.skill_memory_p6.create(p['id'])
    env.store.put('experiment_run',{'procedure_id':p['id'],'source_id':source['id'],
        'source_sha256':'1'*64,'recipe':'source_fidelity','status':'passed',
        'verification_level':'source_verified_only'})
    assert env.skill_memory_p6.get(card['id'])['assessment']['level']=='candidate'
    with pytest.raises(ValueError):env.skill_memory_p6.review(card['id'],confirm=True,summary='This has no matching evidence.')


def test_fixture_only_level_after_matching_citations(env):
    source,p=lesson(env);card=env.skill_memory_p6.create(p['id'])
    checked(env,p)
    env.store.put('experiment_run',{'procedure_id':p['id'],'source_id':source['id'],
        'source_sha256':env.store.get('source',source['id'])['content_hash'],
        'recipe':'memory_restart_lab','status':'passed','verification_level':'lab_reproduced_only'})
    result=env.skill_memory_p6.get(card['id'])
    assert result['assessment']['level']=='fixture_reproduced'
    assert 'external targets' in result['claim_scope']


def test_recheck_is_only_source_and_never_runs_lab(env,monkeypatch):
    _,p=lesson(env);card=env.skill_memory_p6.create(p['id']);called=[]
    def fake_start(procedure_id,recipe):
        called.append((procedure_id,recipe))
        return env.store.put('experiment_run',{'procedure_id':procedure_id,'recipe':recipe,'status':'queued'})
    monkeypatch.setattr(env.knowledge.experiments,'start',fake_start)
    r=env.skill_memory_p6.recheck(card['id'])
    assert r['status']=='started'
    assert env.skill_memory_p6.recheck(card['id'])['status']=='already_running'
    assert called==[(p['id'],'source_fidelity')]


def test_export_metadata_and_project_isolation(env):
    _,p=lesson(env)
    a=env.store.put('project',{'name':'A'})['id'];b=env.store.put('project',{'name':'B'})['id']
    card=env.skill_memory_p6.create(p['id'],project_id=a)
    assert len(env.skill_memory_p6.list(project_id=a))==1
    assert len(env.skill_memory_p6.list(project_id=b))==0
    with pytest.raises(ValueError,match='not found'):env.skill_memory_p6.get(card['id'],project_id=b)
    with pytest.raises(ValueError,match='not found'):env.skill_memory_p6.review(card['id'],project_id=b,confirm=True,summary='A sufficiently long review summary')
    x=env.skill_memory_p6.export(project_id=a)
    text=open(x['path']).read()
    assert card['id'] in text and 'Step 1:' not in text
    assert 'source_checked' not in text


def test_explicit_source_review_required_for_tor_capture(env):
    source,p=lesson(env)
    page=env.store.put('tor_page',{'reviewed':False})
    env.store.put('source',{**source,'tor_research_page_id':page['id']},id=source['id'])
    with pytest.raises(ValueError,match='must be reviewed'):
        env.skill_memory_p6.create(p['id'])


def test_latest_failed_recheck_overrides_old_pass(env):
    source,p=lesson(env);card=env.skill_memory_p6.create(p['id'])
    checked(env,p)
    env.skill_memory_p6.review(card['id'],confirm=True,summary='Original source quotation matched the indexed text.')
    env.store.put('experiment_run',{'procedure_id':p['id'],'source_id':source['id'],
        'source_sha256':source['content_hash'],'recipe':'source_fidelity','status':'failed',
        'verification_level':'not_tested','error':'Quotation changed'})
    outcome=env.skill_memory_p6.get(card['id'])
    assert outcome['assessment']['level']=='candidate'
    with pytest.raises(ValueError,match='passing citation'):
        env.skill_memory_p6.review(card['id'],confirm=True,summary='It should not be eligible for review.')


def test_overdue_review_is_visible_without_upgrading_evidence(env):
    source,p=lesson(env);card=env.skill_memory_p6.create(p['id']);checked(env,p)
    reviewed=env.skill_memory_p6.review(card['id'],confirm=True,summary='Cited content matches a local source, not a target.')
    env.store.put('skill_memory_p6',{**reviewed,'review_due_at':'2020-01-01T00:00:00+00:00'},id=card['id'])
    assert env.skill_memory_p6.get(card['id'])['assessment']['recheck_due']
    with pytest.raises(ValueError,match='new source check'):
        env.skill_memory_p6.review(card['id'],confirm=True,summary='Cannot renew an overdue review without new citations.')
    checked(env,p)
    assert env.skill_memory_p6.review(card['id'],confirm=True,
        summary='New source check completed for the same original lesson.') ['assessment']['recheck_due'] is False


def test_revoked_tor_review_is_stale(env):
    source,p=lesson(env)
    page=env.store.put('tor_page',{'reviewed':True})
    env.store.put('source',{**source,'tor_research_page_id':page['id']},id=source['id'])
    card=env.skill_memory_p6.create(p['id'])
    env.store.put('tor_page',{**page,'reviewed':False},id=page['id'])
    assert env.skill_memory_p6.get(card['id'])['assessment']['level']=='stale'


def test_model_reads_only_operator_reviewed_cards(env):
    from bughunter.model_tools import ModelTools, STUDY_TOOL_NAMES, TOOL_SPECS
    from types import SimpleNamespace
    assert {'list_reviewed_skills','inspect_reviewed_skill'} <= STUDY_TOOL_NAMES
    assert {'list_reviewed_skills','inspect_reviewed_skill'} <= {x['name'] for x in TOOL_SPECS}
    _,p=lesson(env)
    a=env.store.put('project',{'name':'A'})['id']
    card=env.skill_memory_p6.create(p['id'],project_id=a)
    env.settings=SimpleNamespace(get=lambda:{'provider':'ollama'})
    env.tor=SimpleNamespace(settings=lambda:{'allow_remote_ai':False})
    run=env.store.put('run',{'status':'running','project_id':a})
    tools=ModelTools(env,run_id=run['id'],study=True)
    assert tools.call('list_reviewed_skills',{'query':'health'})==[]
    with pytest.raises(ValueError,match='unreviewed'):
        tools.call('inspect_reviewed_skill',{'id':card['id']})
    checked(env,p)
    env.skill_memory_p6.review(card['id'],confirm=True,summary='Original quotations checked, not external behavior.')
    listed=tools.call('list_reviewed_skills',{'query':'health'})
    assert len(listed)==1 and listed[0]['id']==card['id']
    assert tools.call('inspect_reviewed_skill',{'id':card['id']})['assessment']['level']=='source_checked'
    with pytest.raises(ValueError,match='not available'):
        tools.call('skill.p6.review',{'id':card['id']})


def test_model_cannot_see_project_b_card(env):
    from bughunter.model_tools import ModelTools
    from types import SimpleNamespace
    _,p=lesson(env)
    a=env.store.put('project',{'name':'A'})['id']; b=env.store.put('project',{'name':'B'})['id']
    card=env.skill_memory_p6.create(p['id'],project_id=a)
    checked(env,p)
    env.skill_memory_p6.review(card['id'],confirm=True,summary='A citation matches its source, nothing more.')
    env.settings=SimpleNamespace(get=lambda:{'provider':'ollama'})
    env.tor=SimpleNamespace(settings=lambda:{'allow_remote_ai':False})
    run=env.store.put('run',{'status':'running','project_id':b})
    tools=ModelTools(env,run_id=run['id'],study=True)
    assert tools.call('list_reviewed_skills',{'query':'health'})==[]
    with pytest.raises(ValueError,match='not found'):
        tools.call('inspect_reviewed_skill',{'id':card['id']})
