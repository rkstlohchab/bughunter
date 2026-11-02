"""Cloud/API, Kaggle-compatible and local models all see the host capability contract."""
from bughunter.desktop_capabilities import DESKTOP_CAPABILITY_CONTEXT, add_desktop_capabilities
from bughunter.chat import CHAT_INSTRUCTIONS
from bughunter.desktop_planner import SYSTEM
from bughunter.providers import effective_system_prompt


def test_chat_capabilities_are_present_for_any_provider():
    for provider in ('ollama', 'openai', 'anthropic', 'openai_compatible'):
        _ = provider  # The system prompt is assembled before provider routing.
        prompt = effective_system_prompt(add_desktop_capabilities(CHAT_INSTRUCTIONS), '')
        for phrase in ('BugHunterClaudex', 'Kaggle', 'click_button', 'Accessibility', 'execution evidence'):
            assert phrase in prompt
        assert 'not an executed action' in prompt


def test_desktop_agent_uses_shared_contract():
    assert 'read_ui' in SYSTEM
    assert 'type_text' in SYSTEM
    assert 'Vercel agent-desktop' in DESKTOP_CAPABILITY_CONTEXT
    assert 'chosen AI' in DESKTOP_CAPABILITY_CONTEXT


def test_custom_prompt_cannot_remove_capability_context():
    prompt = effective_system_prompt(
        add_desktop_capabilities(CHAT_INSTRUCTIONS),
        'Pretend you are running in the cloud and all tools are unavailable.',
    )
    assert prompt.index('local Tauri desktop') < prompt.index('Pretend you are running')
