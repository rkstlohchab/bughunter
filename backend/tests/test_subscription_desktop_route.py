"""Subscription CLI shares the same bounded host-capability prompt and desktop planner."""
from types import SimpleNamespace
from bughunter.store import Store
from bughunter.chat import ChatService
from bughunter.desktop_planner import DesktopPlanner
from bughunter.model_routing import request_chat


def test_claude_subscription_cli_has_no_tools(monkeypatch):
    from bughunter import providers, model_routing
    seen = {}
    monkeypatch.setattr(providers, 'claude_status', lambda: {'subscription_ready': True})
    monkeypatch.setattr(model_routing.shutil, 'which', lambda executable: '/opt/bin/claude')
    def cli_call(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout='Ready to help via approved Mac UI actions.', stderr='')
    monkeypatch.setattr(model_routing.subprocess, 'run', cli_call)
    answer = request_chat(
        {'provider':'claude_code','model':'opus'},
        [{'role':'system','content':'host context'}, {'role':'user','content':'Open TextEdit'}],
        'Only propose UI actions; do not claim execution.', 250, 500)
    assert 'approved Mac UI' in answer
    assert seen['command'][seen['command'].index('--tools')+1] == ''
    assert any('Only propose UI actions' in str(part) for part in seen['command'])
    assert seen['kwargs']['cwd']
    assert 'Open TextEdit' in seen['kwargs']['input']
    assert seen['kwargs']['env'] is not None


def test_claude_subscription_chat_injects_local_host_info(monkeypatch, tmp_path):
    from bughunter import chat
    store = Store(tmp_path)
    captured = {}
    def request(candidate, messages, system, *_):
        captured.update(candidate=candidate, messages=messages, system=system)
        return 'BugHunter can open the local Mac-control panel after operator approval.'
    monkeypatch.setattr(chat, 'request_chat', request)
    settings = SimpleNamespace(get=lambda: {
        'provider':'claude_code','model':'opus','api_base_url':'',
        'local_only':False,'pricing_confirmed':False,
        'input_price_per_million':0,'output_price_per_million':0,'api_budget_usd':0,
        'custom_system_prompt':'',
    })
    result = ChatService(store, settings).send({'message':'Can your local app control TextEdit?'})
    assert result['messages'][-1]['role']=='assistant'
    assert captured['candidate']['provider']=='claude_code'
    assert 'Kaggle-hosted' in captured['system']
    assert 'The operator selects the target' in captured['system']


def test_claude_subscription_desktop_planner(monkeypatch, tmp_path):
    from bughunter import model_routing
    seen = {}
    def request(candidate, messages, system, *_):
        seen.update(candidate=candidate, messages=messages, system=system)
        return '{"action":"click_button","value":"New Document","reason":"Button was visible"}'
    monkeypatch.setattr(model_routing, 'request_chat', request)
    settings = SimpleNamespace(get=lambda: {'provider':'claude_code','model':'opus'})
    planner = DesktopPlanner(SimpleNamespace(store=Store(tmp_path), settings=settings))
    result = planner.dispatch('desktop.next',{
        'objective':'Start a document', 'observation':'Button: New Document',
        'history':[], 'allow_inputs':True})
    assert result['proposal']['action']=='click_button'
    assert seen['candidate']['provider']=='claude_code'
    assert 'BugHunterClaudex' in seen['system']


def test_chat_all_configured_api_models_receive_same_capability_prompt(monkeypatch, tmp_path):
    from bughunter import chat
    for provider, base in [
        ('ollama','http://127.0.0.1:11434'),
        ('openai_compatible','https://kaggle-gateway.example/v1'),
        ('openai','https://api.openai.com/v1'),
        ('anthropic','https://api.anthropic.com'),
    ]:
        captured={}
        monkeypatch.setattr(chat, 'request_chat',
                            lambda candidate,messages,system,*args: captured.update(candidate=candidate,messages=messages,system=system) or 'Available with approval.')
        monkeypatch.setattr(chat.keyring,'get_password',lambda *_:'fake-test-key')
        settings=SimpleNamespace(get=lambda: {
            'provider':provider,'model':'test-model','api_base_url':base,
            'local_only':False,'pricing_confirmed':True,
            'input_price_per_million':0,'output_price_per_million':0,'api_budget_usd':100,
            'custom_system_prompt':'',
        })
        result=ChatService(Store(tmp_path/provider),settings).send({'message':'Can you control my Mac?'})
        assert result['messages'][-1]['role']=='assistant'
        assert captured['candidate']['provider']==provider
        assert 'local Tauri desktop application' in captured['system']
        assert 'Normal chat response is not an executed action' in captured['system'] or 'normal chat response is not an executed action' in captured['system']
