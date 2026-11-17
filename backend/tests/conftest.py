import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'approval_gate: exercise the opt-in approval gate (autonomy_mode default off)')


@pytest.fixture(autouse=True)
def _approval_gate_default(request):
    """Autonomous mode is the product default. Modules that test the approval
    gate opt in with ``pytestmark = pytest.mark.approval_gate`` so the gate is
    active unless the test explicitly enables autonomy again.

    Uses a private MonkeyPatch so it never changes the teardown order of a
    test's own ``monkeypatch`` fixture.
    """
    if not request.node.get_closest_marker('approval_gate'):
        yield
        return
    from bughunter import autonomy
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(autonomy, 'enabled', lambda store: bool(store.setting('autonomy_mode', False)))
        yield
