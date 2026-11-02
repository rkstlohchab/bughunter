"""Operator-approved, observed UI actions only."""
import json
import pytest
from bughunter.desktop_planner import validate_plan, visible_menu_items, visible_items

UI = '''Window: Offline Test Game
Button: Resume
Item: Level 1
Menu item: View > Zoom In
Menu item: File > Delete
Menu item: File > Open
Text field label: Search
'''


def plan(action, value, allow=True, observation=UI):
    return validate_plan(json.dumps({'action': action, 'value': value, 'reason':'test'}), observation, allow)


def test_menu_and_list_require_current_accessibility_evidence():
    assert plan('click_menu','View > Zoom In')['action']=='click_menu'
    assert plan('select_item','Level 1')['action']=='select_item'
    assert visible_menu_items(UI)=={'view > zoom in','file > delete','file > open'}
    assert visible_items(UI)=={'level 1'}
    for action,value in [('click_menu','View > Unknown'),('select_item','Level 2')]:
        with pytest.raises(ValueError,match='not observed'):
            plan(action,value)


def test_destructive_menus_and_readonly_sessions_are_blocked():
    with pytest.raises(ValueError,match='manual'):
        plan('click_menu','File > Delete')
    with pytest.raises(ValueError,match='read-only'):
        plan('click_menu','File > Open',False)
    with pytest.raises(ValueError,match='read-only'):
        plan('scroll','down',False)


def test_scrolling_is_one_page_and_direction_limited():
    assert plan('scroll','up')['value']=='up'
    with pytest.raises(ValueError,match='direction'):
        plan('scroll','infinite')


def test_model_cannot_select_hidden_controls():
    with pytest.raises(ValueError,match='not observed'):
        plan('click_menu','File > Open',observation='Window: Game\nText: File > Open')
