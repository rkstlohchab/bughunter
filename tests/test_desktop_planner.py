import pytest
from bughunter.desktop_planner import validate_plan

def test_valid_ui_action():
    assert validate_plan('{"action":"click_button","value":"Proxy","reason":"Open proxy"}', 'Window: Burp\nButtons: Proxy, Repeater',True)['action']=='click_button'

def test_invalid_unobserved_button():
    with pytest.raises(ValueError,match='not observed'):
        validate_plan('{"action":"click_button","value":"Unknown"}', 'Buttons: Proxy',True)

def test_prevent_destructive_and_secret():
    with pytest.raises(ValueError,match='destructive'):
        validate_plan('{"action":"click_button","value":"Delete"}', 'Buttons: Delete',True)
    with pytest.raises(ValueError,match='Sensitive'):
        validate_plan('{"action":"type_text","value":"password: x"}', '',True)

def test_read_only():
    with pytest.raises(ValueError,match='read-only'):
        validate_plan('{"action":"type_text","value":"Hello"}', '',False)
    assert validate_plan('{"action":"done","value":"Observed button","reason":"finish"}', '',False)['action']=='done'

def test_no_shell():
    with pytest.raises(ValueError,match='unsupported'):
        validate_plan('{"action":"shell","value":"rm /"}', '',True)

def test_read_ui_and_key_validation():
    assert validate_plan('{"action":"read_ui","value":""}', 'Window: Test', False)['action']=='read_ui'
    with pytest.raises(ValueError,match='Unsupported keyboard key'):
        validate_plan('{"action":"key","value":"cmd+q"}', 'Window: Test', True)
