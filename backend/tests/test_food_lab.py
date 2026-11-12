import json

import pytest

from bughunter.food_lab import calculate_food_cart
from bughunter.lab import LabService
from bughunter.policy import PolicyService
from bughunter.store import Store
from bughunter.traffic import TrafficService


@pytest.mark.parametrize('body', [[], {'product_id': []}, {'product_id': 'unknown'},
    {'product_id': 'bowl', 'quantity': True}, {'product_id': 'bowl', 'quantity': 1.5},
    {'product_id': 'bowl', 'addons': [{}]}, {'product_id': 'bowl', 'coupon': ['UNKNOWN']}])
def test_invalid_food_input_is_rejected(body):
    with pytest.raises(ValueError): calculate_food_cart(body, 'fixed')


def test_food_cart_round_trip_and_account_isolation(tmp_path):
    store = Store(tmp_path)
    policy = PolicyService(store)
    lab = LabService(store, policy, TrafficService(store))
    try:
        setup = lab.setup()
        base, project = setup['runtime']['url'], setup['project']['id']
        action = policy.queue(project, {'method': 'POST', 'url': base + '/fixed/food-cart',
            'headers': {'content-type': 'application/json', 'x-test-account': 'alice'},
            'body': json.dumps({'product_id': 'bowl', 'quantity': 1, 'addons': ['drink'], 'coupon': 'FOOD10'})}, 'alice')
        policy.approve(action['id'])
        result = policy.execute(action['id'])
        expected = json.loads(result['response']['body'])
        assert expected['accepted_total_minor'] == expected['canonical_total_minor'] == 2140
        assert result['lab_verified'] and result['evidence_ids']
        for account, route, body in [('alice', 'food-cart', expected), ('bob', 'food-cart', {'empty': True}),
                                     ('alice', 'cart', {'empty': True})]:
            read = policy.queue(project, {'method': 'GET', 'url': base + '/fixed/' + route,
                'headers': {'x-test-account': account}, 'body': ''}, account)
            assert json.loads(policy.execute(read['id'])['response']['body']) == body
    finally:
        lab.stop()
