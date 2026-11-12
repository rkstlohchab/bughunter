"""Separate curricular storage, reviewed-only retrieval and leakage-free exports."""
import base64
import io
import json
import zipfile

import pytest

from bughunter.store import Store
from bughunter.training import DOMAINS, TrainingService

@pytest.fixture
def training(tmp_path):
    return TrainingService(Store(tmp_path))


def add(t, ws, label, group="", review=True):
    ex = t.save_example(ws["id"], f"Explain {label}", f"Verified {label}", source=f"source-{label}", group=group)
    if review: ex = t.review(ex["id"], True)
    return ex


def unpack(export):
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(export["content_base64"]))) as archive:
        return {name: archive.read(name).decode() for name in archive.namelist()}


def test_workspace_and_review_isolation(training):
    ws = {domain: training.create_workspace(domain, domain) for domain in DOMAINS}
    for domain, w in ws.items():
        add(training, w, f"{domain} characteristic")
    for domain in DOMAINS:
        hits = training.search(domain, "characteristic")
        assert len(hits) == 1 and hits[0]["workspace_id"] == ws[domain]["id"]
    draft = add(training, ws['cheat_engine'], 'probe', review=False)
    assert not training.search('cheat_engine', 'probe')
    assert training.list_workspaces('cheat_engine')[0]['reviewed_count'] == 1
    training.review(draft['id'], True)
    assert len(training.search('cheat_engine', 'probe')) == 1
    training.review(draft['id'], False)
    assert not training.search('cheat_engine', 'probe')


def test_edits_force_review_and_no_cross_workspace_move(training):
    w1 = training.create_workspace('burp_suite', 'A')
    w2 = training.create_workspace('tor_research', 'B')
    e = add(training, w1, 'one')
    with pytest.raises(ValueError, match='transferred'):
        training.save_example(w2['id'], 'foo', 'bar', id=e['id'])
    modified = training.save_example(w1['id'], e['instruction'], 'updated', id=e['id'])
    assert modified['status'] == 'draft'
    with pytest.raises(ValueError, match='already exists'):
        training.save_example(w1['id'], modified['instruction'], 'updated')
    with pytest.raises(ValueError):
        training.save_example(w1['id'], 'task', 'answer', tags=['x']*13)
    assert training.search('burp_suite', 'updated') == []


def test_dataset_export_splits_holdout_group_and_excludes_drafts(training):
    w = training.create_workspace('tor_research', 'Research notes', instructions='Respect source fidelity.')
    for i in range(13):
        add(training, w, f'case-{i}', group=f'family-{i}')
    add(training, w, 'variant', group='family-1')
    add(training, w, 'do-not-train', review=False)
    bundle = training.export(w['id'])
    files = unpack(bundle)
    assert all(f'{name}.jsonl' in files for name in ('train', 'valid', 'test'))
    manifest = json.loads(files['manifest.json'])
    assert manifest['reviewed_examples'] == 14
    assert manifest['excluded_drafts'] == 1
    assert manifest['split_counts']['test'] >= 1 and manifest['split_counts']['valid'] >= 1
    id_to_split = {id: split for split, ids in manifest['split_ids'].items() for id in ids}
    expected_group = {e['id']: e['group'] for e in training.list_examples(w['id']) if e['status'] == 'reviewed'}
    for a, ga in expected_group.items():
        for b, gb in expected_group.items():
            if ga == gb:
                assert id_to_split[a] == id_to_split[b]
    messages = [json.loads(line)['messages'] for line in files['train.jsonl'].splitlines()]
    assert messages[0][0]['content'] == 'Respect source fidelity.'
    assert all('do-not-train' not in contents for contents in files.values())
    assert bundle['manifest']['training_performed'] is False


def test_delete_workspace_cascades_no_cross_domain(training):
    a = training.create_workspace('burp_suite','A')
    b = training.create_workspace('cheat_engine','B')
    e = add(training, a, 'A')
    add(training, b, 'B')
    assert training.delete_workspace(a['id'])['deleted']
    assert training.store.get('training_example', e['id']) is None
    assert len(training.list_examples(b['id'])) == 1


def test_no_unreviewed_export_and_input_guard(training):
    w = training.create_workspace('cheat_engine', 'C')
    add(training, w, 'draft', review=False)
    with pytest.raises(ValueError, match='Review'):
        training.export(w['id'])
    with pytest.raises(ValueError, match='domain'):
        training.create_workspace('unknown','X')
    with pytest.raises(ValueError, match='boolean'):
        training.review(training.list_examples(w['id'])[0]['id'], 'yes')


def test_training_rpc_roundtrip(tmp_path, monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'keyring', types.SimpleNamespace(get_password=lambda *_: None))
    from bughunter.app import Application
    app = Application(tmp_path, primary=False)
    w = app.dispatch('training.create', {'domain':'burp_suite','title':'HTTP workflow'})
    ex = app.dispatch('training.save', {'workspace_id':w['id'],'instruction':'Inspect cookie','response':'Review flags'})
    app.dispatch('training.review', {'id':ex['id'],'approved':True})
    assert app.dispatch('training.search', {'domain':'burp_suite','query':'cookie'})[0]['id'] == ex['id']
    assert app.dispatch('training.export', {'workspace_id':w['id']})['manifest']['split_counts']['train'] == 1


def test_investigation_tool_retrieves_only_reviewed_burp_examples(tmp_path, monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'keyring', types.SimpleNamespace(get_password=lambda *_: None))
    from bughunter.app import Application
    from bughunter.model_tools import ModelTools
    app = Application(tmp_path, primary=False)
    burp = app.training.create_workspace('burp_suite', 'HTTP')
    ce = app.training.create_workspace('cheat_engine', 'Local')
    good = add(app.training, burp, 'cookie', review=True)
    add(app.training, ce, 'cookie', review=True)
    add(app.training, burp, 'cookie draft', review=False)
    run = app.store.put('run', {'project_id':'p', 'status':'running'})
    data = ModelTools(app, run['id']).call('search_burp_training', {'query':'cookie'})
    assert [e['id'] for e in data] == [good['id']]
