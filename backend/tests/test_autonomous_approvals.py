from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from bughunter.model_tools import ModelTools


@pytest.mark.parametrize('effect', ['state_changing', 'uncertain', None])
@pytest.mark.parametrize('status', ['pending_approval', 'approved', 'unknown'])
def test_autonomous_dispatch_never_approves_or_sends_writes(effect, status):
    policy = Mock()
    tools = ModelTools(SimpleNamespace(policy=policy), 'run')
    result = tools._autodispatch({'id': 'a', 'effect': effect, 'status': status})
    assert result['status'] == status
    assert result['approval_required'] == (status == 'pending_approval')
    policy.approve.assert_not_called()
    policy.execute.assert_not_called()


def test_autonomous_read_uses_policy_execution():
    policy = Mock()
    policy.execute.return_value = {'id': 'a', 'status': 'completed'}
    tools = ModelTools(SimpleNamespace(policy=policy), 'run')
    assert tools._autodispatch({'id': 'a', 'effect': 'read_only'})['status'] == 'completed'
    policy.execute.assert_called_once_with('a')
    policy.approve.assert_not_called()


@pytest.mark.parametrize('name', ['memory_write', 'memory_attach', 'memory_tool', 'desktop_click', 'desktop_key'])
def test_investigation_cannot_invoke_desktop_bridge(name):
    from bughunter.model_tools import TOOL_SPECS
    assert name not in {spec['name'] for spec in TOOL_SPECS}
    tools = ModelTools(SimpleNamespace(), 'run')
    with pytest.raises(ValueError, match='not available'):
        tools.call(name, {})
