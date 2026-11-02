import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from bughunter.store import Store
from bughunter.advanced_p8 import AdvancedP8, read_dataset, valid_model


def make_dataset(root, overlap=False):
    folder=Path(root)/'exports';folder.mkdir(exist_ok=True)
    path=folder/'dataset_p2_test.zip'
    train=[{'messages':[{'role':'user','content':'What is an invariant?'},{'role':'assistant','content':'A rule that must remain true.'}]}]
    evaluation=[{'messages':[{'role':'user','content':'Why do we validate data?'},{'role':'assistant','content':'To enforce the expected contract.'}]}]
    provenance=[{'id':'1','split':'train','source':'source-a'}, {'id':'2','split':'evaluation','source':'source-a' if overlap else 'source-b'}]
    manifest={'schema':'bughunter.p2.dataset.v1','training_started':False,'weight_changes':False,
              'holdout_present':True,'project_id':'project-test','counts':{'train':1,'evaluation':1}}
    with zipfile.ZipFile(path,'w') as archive:
        for name,contents in [('train.jsonl',train),('evaluation.jsonl',evaluation),('provenance.jsonl',provenance)]:
            archive.writestr(name,'\n'.join(json.dumps(x) for x in contents)+'\n')
        archive.writestr('manifest.json',json.dumps(manifest))
    return path


def service(tmp_path):
    return AdvancedP8(SimpleNamespace(store=Store(tmp_path)))


def test_dataset_prepares_dry_run_without_mac(tmp_path):
    dataset=make_dataset(tmp_path)
    runner=service(tmp_path)
    assert len(runner.datasets())==1
    job=runner.prepare(dataset.name,'mlx-community/Qwen3-0.6B-4bit',iters=10,approved=True,dry_run=True)
    assert job['status']=='prepared' and job['train_count']==1
    directory=Path(job['adapter_path']).parent/'data'
    assert (directory/'train.jsonl').is_file() and (directory/'valid.jsonl').is_file()
    assert runner.list()[0]['status']=='prepared'


def test_holdout_leakage_fails(tmp_path):
    with pytest.raises(ValueError,match='overlap'):
        read_dataset(make_dataset(tmp_path,overlap=True))


def test_approval_required(tmp_path):
    path=make_dataset(tmp_path)
    with pytest.raises(ValueError,match='Confirm'):
        service(tmp_path).prepare(path.name,'mlx-community/Qwen3-0.6B-4bit',approved=False,dry_run=True)


def test_model_validation_no_injected_arguments(tmp_path):
    for model in ['', '--help', 'foo/bar --disable', 'foo', '/this/path/does/not/exist']:
        with pytest.raises(ValueError):valid_model(model)
    assert valid_model('mlx-community/Qwen3-0.6B-4bit')=='mlx-community/Qwen3-0.6B-4bit'


def test_recovery_marks_interrupted(tmp_path):
    runner=service(tmp_path)
    row=runner.store.put('p8_training_run',{'status':'running','model':'stub'})
    newer=service(tmp_path)
    assert newer.store.get('p8_training_run',row['id'])['status']=='interrupted'


def test_web_coverage_requires_project_and_never_sends(tmp_path):
    runner=service(tmp_path)
    with pytest.raises(ValueError):runner.web_coverage('missing')
    project=runner.store.put('project',{'name':'test'})
    runner.store.put('traffic',{'project_id':project['id'],'request':{'url':'https://example.org/checkout'}})
    report=runner.web_coverage(project['id'])
    assert report['captured_requests']==1 and report['checkout_requests_observed']==1
    assert all(c['status']!='verified' for c in report['checks'])


def test_radar_layout_rejects_unknown_offsets(tmp_path):
    import importlib.util
    module_path=Path(__file__).resolve().parents[2]/'scripts'/'run_esp_lab_demo.py'
    spec=importlib.util.spec_from_file_location('radar',module_path)
    radar=importlib.util.module_from_spec(spec);spec.loader.exec_module(radar)
    data={'pid':123,'entity_base':'0x123000','entity_layout':{'count':2,'stride':24,'fields':[
        {'name':'x','type':'f32','offset':0},{'name':'y','type':'f32','offset':4}]}}
    p=tmp_path/'layout.json';p.write_text(json.dumps(data))
    assert radar.load_target_layout(p)['pid']==123
    data['entity_layout']['fields'][1]['name']='password'
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):radar.load_target_layout(p)
