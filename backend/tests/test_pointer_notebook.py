import pytest
from bughunter.store import Store
from bughunter.pointer_notebook import PointerNotebook

@pytest.fixture
def notebook(tmp_path):
    return PointerNotebook(Store(tmp_path))


def sample(n):
    return n.save(process_label='Offline test game', label='Health', base='0x1000',
                  offsets=['0x18'], value_type='i32', expected_value='100')


def test_save_and_retrieve_readonly_candidate(notebook):
    row = sample(notebook)
    assert row['status'] == 'unverified'
    assert row['base'] == '0x1000'
    assert row['offsets'] == ['24']
    assert notebook.dispatch('memory.pointers.get', {'id': row['id']})['id'] == row['id']


def test_restart_observations_are_not_permanent_stability_claim(notebook):
    row = sample(notebook)
    initial = {'stable_across_restarts': 'not_established', 'matched': True,
               'attached_pid': 220, 'process_instance_id': '220:linux-ticks:100',
               'resolved_address': '0x1234'}
    first = notebook.observe(row['id'], initial)
    assert first['status'] == 'observed_once'
    twice_same_instance = notebook.observe(row['id'], initial)
    assert twice_same_instance['status'] == 'observed_once'
    second = notebook.observe(row['id'], {**initial, 'attached_pid': 240,
                                          'process_instance_id': '240:linux-ticks:200'})
    assert second['status'] == 'retested_across_processes'
    assert 'not ASLR-stable' in second['note']


def test_invalid_observation_never_claims_success(notebook):
    row = sample(notebook)
    with pytest.raises(ValueError):
        notebook.observe(row['id'], {'matched': True, 'attached_pid': 220,
                                      'process_instance_id': '220:linux-ticks:100',
                                      'stable_across_restarts': 'verified'})
    with pytest.raises(ValueError):
        notebook.observe(row['id'], {'matched': 'true', 'attached_pid': 220,
                                      'stable_across_restarts': 'not_established'})
    assert notebook.dispatch('memory.pointers.get', {'id':row['id']})['status'] == 'unverified'


def test_validates_input_bounds_and_project(notebook):
    with pytest.raises(ValueError, match='project'):
        notebook.save(process_label='test', label='Health', base=42, offsets=[],
                      value_type='i32', expected_value=100, project_id='not-there')
    with pytest.raises(ValueError, match='64-bit'):
        notebook.save(process_label='test', label='Health', base=-1, offsets=[],
                      value_type='i32', expected_value=100)
