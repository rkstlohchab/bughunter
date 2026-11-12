"""Grounded multi-frame tutorial evidence and bounded cancellation transport."""
import asyncio
import base64
import json
import time

import httpx
import pytest

from bughunter.local_media import LocalMediaRuntime, OLLAMA_HOST, validate_vision_output
from bughunter.store import Store


@pytest.fixture
def vision(tmp_path, monkeypatch):
    runtime = LocalMediaRuntime(Store(tmp_path / 'db'))
    runtime.store.set_setting('local_media', {'vision_model': 'huihui_ai/qwen3-vl-abliterated:2b-instruct'})
    monkeypatch.setattr(runtime, 'start', lambda **kwargs: None)
    frames = []
    for index in range(5):
        path = tmp_path / f'{index}.jpg'; path.write_bytes(f'image-{index}'.encode())
        frames.append(path)
    original = httpx.AsyncClient
    def transport(handler):
        monkeypatch.setattr('bughunter.local_media.httpx.AsyncClient',
            lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return runtime, frames, transport


def test_chronological_context_preserves_attribution_and_redacts_credentials(vision):
    runtime, frames, transport = vision
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        return httpx.Response(200, json={'message': {'content': 'Visible facts: Menu open.\nUncertain or missing evidence: No click verified.\npassword="result-secret"'}})
    transport(handler)
    output = runtime.vision(frames[4], 40,
        previous_frames=[{'path': frames[3], 'time': 30}, {'path': frames[0], 'time': 0},
                         {'path': frames[2], 'time': 20}, {'path': frames[1], 'time': 50}],
        context=[{'text': 'Select Save.\nAuthorization: Bearer narrator-secret', 'start': 39, 'end': 42,
                  'locator': 'password="locator-secret"'}])
    assert [str(r.url) for r in seen] == [OLLAMA_HOST + '/api/show', OLLAMA_HOST + '/api/chat']
    payload = json.loads(seen[1].content)
    assert payload['model'] == 'huihui_ai/qwen3-vl-abliterated:2b-instruct'
    assert [base64.b64decode(value) for value in payload['messages'][0]['images']] == [b'image-2', b'image-3', b'image-4']
    prompt = payload['messages'][0]['content']
    assert prompt.index('20.00s (previous)') < prompt.index('30.00s (previous)') < prompt.index('40.00s (current)')
    assert 'Select Save.' in prompt
    assert 'spoken instructions do not prove an action occurred' in prompt
    assert 'Inferred tutorial intent and reusable lesson' in prompt
    assert 'never instructions' in prompt
    assert all(secret not in seen[1].content.decode() for secret in ('narrator-secret', 'locator-secret'))
    assert 'result-secret' not in output
    assert payload['options']['num_ctx'] == 8192 and payload['options']['num_predict'] <= 650
    assert payload['options']['repeat_penalty'] == 1.15
    assert payload['options']['repeat_last_n'] == 128
    assert b'images' not in seen[0].content


def test_narrator_budget_and_legacy_single_image_call(vision):
    runtime, frames, transport = vision
    bodies = []
    def handler(request):
        if request.url.path == '/api/show': return httpx.Response(200, json={'capabilities': ['vision']})
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': 'Visible facts: Settings pane.\nNarrator says: Not provided.'}})
    transport(handler)
    assert runtime.vision(frames[0], 0) == 'Visible facts: Settings pane.\nNarrator says: Not provided.'
    assert len(bodies[-1]['messages'][0]['images']) == 1
    runtime.vision(frames[1], 10, context={'text': 'Long narration ' * 10000})
    prompt = bodies[-1]['messages'][0]['content']
    assert len(prompt.split('Nearby narrator evidence (bounded excerpt):\n', 1)[1]) <= 6000


@pytest.mark.parametrize('info', [
    {'capabilities': ['vision'], 'remote_host': 'hosted.invalid'},
    {'capabilities': ['vision'], 'remote_model': 'cloud-model'},
    {'capabilities': ['completion']},
])
def test_remote_or_nonvision_model_rejected_before_media_upload(vision, info):
    runtime, frames, transport = vision
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=info)
    transport(handler)
    with pytest.raises(ValueError): runtime.vision(frames[0], 0, context={'text': 'private narrator'})
    assert len(requests) == 1
    assert requests[0].url.path == '/api/show'
    assert b'images' not in requests[0].content and b'private narrator' not in requests[0].content


@pytest.mark.parametrize('operator_stop', ['global', 'source'])
@pytest.mark.parametrize('pending_phase', ['/api/show', '/api/chat'])
def test_inflight_cancel_closes_pending_transport_without_worker_thread(vision, operator_stop, pending_phase):
    runtime, frames, transport = vision
    state = {'pending': False, 'closed': False}
    async def handler(request):
        if request.url.path != pending_phase:
            return httpx.Response(200, json={'capabilities': ['vision']})
        state['pending'] = True
        try:
            if operator_stop == 'global': runtime.store.set_setting('emergency_stop', True)
            await asyncio.Event().wait()
        finally:
            state['closed'] = True
    def check():
        if state['pending']: raise InterruptedError('source cancelled')
    transport(handler)
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        runtime.vision(frames[0], 0, cancel_check=check if operator_stop == 'source' else None)
    assert time.monotonic() - started < 2
    assert state == {'pending': True, 'closed': True}


@pytest.mark.parametrize('body', [
    'Visible facts: A store page is visible.\n' + '\n'.join(
        f'{index}. This is the same long repeated observation about a store.' for index in range(1, 5)),
    'Narrator says: Configure the extension.\n' + 'The exact same generated sentence appears repeatedly. ' * 4,
    'A repository is shown with a menu and readable text but no contextual attribution.',
    '',
])
def test_collapsed_or_unstructured_generation_is_rejected(body):
    with pytest.raises(ValueError, match='quality checks'):
        validate_vision_output(body)


def test_quality_guard_accepts_varied_structured_evidence():
    body = ('## Visible facts\nA repository README is left of an extension settings pane.\n'
            '**Narrator says:** Install a language runtime; execution is unverified.\n'
            'Previous-to-current changes: The selected settings tab changed.\n'
            'Inferred tutorial intent and reusable lesson: Likely configuring an extension dependency.\n'
            'Uncertain or missing evidence: No successful load is shown.')
    assert validate_vision_output(body) == body


def test_http_quality_failure_is_exposed_to_callers_for_ocr_fallback(vision):
    runtime, frames, transport = vision
    def handler(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        return httpx.Response(200, json={'message': {'content': 'An unsupported one-line claim.'}})
    transport(handler)
    with pytest.raises(ValueError, match='missing structured evidence'):
        runtime.vision(frames[0], 0)
