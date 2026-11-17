"""Focus-free keyboard delivery: validation, registration and engine sequencing."""
import pytest

from bughunter import background_input as bi
from bughunter.harness_modes import READ_TOOLS
from bughunter.operator_tools import OperatorTools, TOOL_CAPABILITIES, HARNESS_TOOLS


def test_registered_as_computer_tool_and_not_in_plan_mode():
    assert any(s['name'] == 'desktop_background_input' for s in OperatorTools.TOOL_SPECS)
    assert TOOL_CAPABILITIES['desktop_background_input'] == 'computer'
    assert 'desktop_background_input' in HARNESS_TOOLS and 'desktop_background_input' not in READ_TOOLS


@pytest.mark.parametrize('steps', [[], [{'key': 'a', 'text': 'b'}], [{'key': 'right', 'hold_ms': 99999}], [{'text': ''}], 'right'])
def test_rejects_bad_steps(steps):
    with pytest.raises(ValueError):
        bi.validate_steps(steps)


def test_sends_in_order_and_stops_when_cancelled():
    log = []

    class Fake:
        def key(self, pid, key, mods, hold): log.append(('key', pid, key, tuple(mods), hold))
        def text(self, pid, text): log.append(('text', pid, text))
    out = bi.send(4242, [{'key': 'cmd+s'}, {'key': 'right', 'hold_ms': 400}, {'text': 'hi'}], engine=Fake())
    assert log == [('key', 4242, 's', ('cmd',), 0), ('key', 4242, 'right', (), 400), ('text', 4242, 'hi')]
    assert out['steps_sent'] == 3 and out['focus_changed'] is False

    def stop(): raise InterruptedError('stopped')
    with pytest.raises(InterruptedError):
        bi.send(4242, [{'key': 'a'}], engine=Fake(), check=stop)
    with pytest.raises(ValueError):
        bi.send(1, [{'key': 'a'}], engine=Fake())


def test_macos_refuses_with_working_alternatives(monkeypatch):
    monkeypatch.setattr(bi.platform, 'system', lambda: 'Darwin')
    with pytest.raises(RuntimeError, match='semantic actions'):
        bi.backend()
