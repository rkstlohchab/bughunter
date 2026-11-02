"""Real HTTP staging quote fixture with strict no-order/no-payment safety contracts."""
import time
from types import SimpleNamespace

import pytest

from bughunter.store import Store
from bughunter.policy import PolicyService
from bughunter.traffic import TrafficService
from bughunter.lab import LabService
from bughunter.staging_price import StagingPriceAudit, validate_configuration
from bughunter.autopilot import choose_workflow


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    policy = PolicyService(store)
    traffic = TrafficService(store)
    lab = LabService(store, policy, traffic)
    runtime = lab.start()
    project = policy.save_project({
        'name':'Staging quote fixture', 'authorization':'Operator-controlled disposable lab',
        'scope':[runtime['url']], 'lab_project':True, 'allow_private':True, 'requests_per_second':10,
        'max_requests':12,
        'read_only_paths':[{'path':'/.well-known/bughunter-price-audit.json','methods':['GET']},
                           {'path':'/__bughunter__/quote','methods':['POST']}],
        'staging_quote':{'environment':'staging', 'origin':runtime['url'],
            'challenge':lab.server.nonce, 'test_sku':'TEST-ITEM', 'expected_unit_price_minor':10000,
            'acknowledge_no_real_orders':True},
    })
    app = SimpleNamespace(store=store, policy=policy, lab=lab,
                          tasks=SimpleNamespace(describe=lambda row:row))
    audit = StagingPriceAudit(app)
    yield audit, app, project
    lab.stop()


def complete(store, job_id):
    for _ in range(160):
        row=store.get('work_task',job_id)
        if row['status'] in ('completed','failed','cancelled'):
            return row
        time.sleep(.06)
    raise AssertionError('staging quote audit did not finish')


def test_staging_quote_integrity_real_http_and_evidence(env):
    audit, app, project = env
    assert choose_workflow('Test price integrity on my staging site') == 'staging_price'
    with pytest.raises(ValueError, match='Explicitly approve'):
        audit.start(project_id=project['id'])
    job = audit.start(project_id=project['id'],approved=True)
    result = complete(app.store,job['id'])
    assert result['status']=='completed',result.get('error')
    report = result['result']
    assert len(report['cases'])==3
    assert all(row['quote_matches_catalog'] for row in report['cases'])
    assert report['transactional_actions']==0
    assert len(report['evidence_ids'])==4
    assert app.lab.server.carts=={}
    assert len(app.store.list('action',project_id=project['id']))==4
    assert all(a['request']['url'].endswith(('/.well-known/bughunter-price-audit.json','/__bughunter__/quote'))
        for a in app.store.list('action',project_id=project['id']))


def test_staging_quote_detects_bad_quote_without_purchasing(env):
    audit,app,project=env
    app.lab.server.quote_vulnerable=True
    job=audit.start(project_id=project['id'],approved=True)
    done=complete(app.store,job['id'])
    assert done['status']=='completed'
    rows=done['result']['cases']
    assert rows[0]['quote_matches_catalog']
    assert all(not r['quote_matches_catalog'] for r in rows[1:])
    assert 'Review staging calculation' in done['result']['assessment']
    assert app.lab.server.carts=={}


def test_challenge_mismatch_blocks_before_quote(env):
    audit,app,project=env
    data={**project,'staging_quote':{**project['staging_quote'],'challenge':'a'*30}}
    app.policy.save_project(data)
    job=audit.start(project_id=project['id'],approved=True)
    done=complete(app.store,job['id'])
    assert done['status']=='failed'
    assert len(app.store.list('action',project_id=project['id']))==1
    assert app.lab.server.carts=={}


def test_requires_strict_configured_readonly_paths(env):
    _,app,project=env
    for patch in ({'scope':['https://another.example']},
                  {'read_only_paths':[{'path':'/.well-known/bughunter-price-audit.json','methods':['GET']}]},
                  {'staging_quote':{**project['staging_quote'],'acknowledge_no_real_orders':False}}):
        with pytest.raises(ValueError): validate_configuration({**project,**patch})


def test_refuse_external_checkout_paths_even_if_requested(env):
    audit,app,project=env
    with pytest.raises(TypeError):
        audit.start(project_id=project['id'],approved=True,checkout_url='https://real.shop/checkout')


def test_readonly_quote_observations_export_without_manifest_challenge(env):
    from bughunter.autopilot import Autopilot
    audit, app, project = env
    job=audit.start(project_id=project['id'],approved=True)
    result=complete(app.store,job['id'])
    assert result['status']=='completed'
    auto=Autopilot(SimpleNamespace(store=app.store))
    exported=auto.export_evidence(project['id'])
    assert exported['count']==3
    assert 'staging_quote_integrity_observation' in exported['content']
    assert app.lab.server.nonce not in exported['content']
    assert 'verified exploit' not in exported['content']


def test_plan_requires_configured_staging_scope(env):
    from bughunter.autopilot import Autopilot
    audit,app,project=env
    auto=Autopilot(SimpleNamespace(store=app.store,policy=app.policy))
    plan=auto.plan(message='Test price integrity on my staging site',project_id=project['id'])
    assert plan['ready'] and plan['requires_approval']
    assert plan['workflow']=='staging_price'
    changed={**project,'staging_quote':None}
    app.store.put('project',changed,id=project['id'])
    plan=auto.plan(message='Test price integrity on my staging site',project_id=project['id'])
    assert plan['missing'] and not plan['ready']
