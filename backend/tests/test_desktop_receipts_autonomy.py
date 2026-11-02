from types import SimpleNamespace
import pytest
from bughunter.store import Store
from bughunter.tasks import TaskService


def fixture(tmp_path):
    store=Store(tmp_path)
    svc=TaskService(SimpleNamespace(store=store))
    job=svc.external_start(kind='desktop_control',title='Inspect own app')
    return svc,store,job['id']


def test_desktop_receipts_keep_only_action_and_digest(tmp_path):
    service,store,id=fixture(tmp_path)
    receipt=service.external_step(id,'click_button','a'*64,True)
    assert receipt['recorded'] and receipt['step']==1
    task=store.get('work_task',id)
    assert task['desktop_receipts'][0]['observation_sha256']=='a'*64
    assert 'not independently attested' in task['desktop_receipts'][0]['provenance']
    assert 'private input' not in str(task)
    service.external_finish(id,'completed')
    with pytest.raises(ValueError,match='no longer running'):
        service.external_step(id,'read_ui','b'*64,True)


@pytest.mark.parametrize('action,hash_value,success',[
    ('shell','a'*64,True),('click_button','secret',True),('read_ui','a'*64,'yes'),
    ('type_text','a'*64,1),('read_ui','a'*63+'G',False),
])
def test_untrusted_or_raw_receipts_rejected(tmp_path,action,hash_value,success):
    service,store,id=fixture(tmp_path)
    with pytest.raises(ValueError):
        service.external_step(id,action,hash_value,success)
    assert store.get('work_task',id).get('desktop_receipts') is None
