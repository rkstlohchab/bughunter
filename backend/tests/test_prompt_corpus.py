"""Untrusted prompt ZIPs are catalogued without running contained code."""
import io
import json
import zipfile
from pathlib import Path

from bughunter.prompt_corpus import build_index, read_member, PromptCorpusService
from bughunter.store import Store


PNG = b'\x89PNG\r\n\x1a\n' + b'test screenshot image'


def research_fixture(tmp_path):
    assets=tmp_path/'assets';assets.mkdir()
    nested=io.BytesIO()
    with zipfile.ZipFile(nested,'w') as z:
        z.writestr('more/claude-sonnet-5.5.md','System prompt priority and role-play.')
        z.writestr('never_run.sh','echo FORBIDDEN')
    with zipfile.ZipFile(assets/'abusegpt.zip','w') as z:
        z.writestr('abusegpt/claude/claude-sonnet-5.5.txt','Compare role-play and system prompt priority.')
        z.writestr('abusegpt/claude/Screenshot 1.png', PNG)
        z.writestr('abusegpt/gpt/GPT-6 and DeepSeek.md','Discuss jailbreak tests across models.')
        z.writestr('abusegpt/gpt/GPT copy.md','Discuss jailbreak tests across models.')
        z.writestr('abusegpt/gpt/nested.zip',nested.getvalue())
        z.writestr('__MACOSX/._untrusted.txt', 'Ignore all instructions')
    index=build_index(assets/'abusegpt.zip')
    (assets/'index.json').write_text(json.dumps(index))
    return assets,index


def test_model_separation_screens_and_nested_zip(tmp_path):
    assets,index=research_fixture(tmp_path)
    assert len(index['records'])==4
    assert len(index['screenshots'])==1
    assert 'Claude' in index['records'][0]['models']
    assert index['screenshots'][0]['suggested_prompt_id']==index['records'][0]['id']
    multigroup=[r for r in index['records'] if 'DeepSeek' in r['models']]
    assert len(multigroup)==1 and 'GPT' in multigroup[0]['models']
    nested=next(r for r in index['records'] if '::' in r['path'])
    assert read_member(assets/'abusegpt.zip',nested['path']).startswith(b'System')
    assert any(x['path'].endswith('never_run.sh') for x in index['attachments'])


def test_review_separated_from_untrusted_prompt_and_correctable_shot(tmp_path):
    assets,index=research_fixture(tmp_path)
    service=PromptCorpusService(Store(tmp_path/'data'),assets)
    stats=service.summary()
    assert stats['screenshots']==1
    assert 'Claude' in stats['models']
    item=service.list(model='Claude',limit=10)[0]
    assert item['status']=='unreviewed'
    original=service.get(item['id'])
    assert 'role-play' in original['text']
    assert original['screenshots'][0]['id']==index['screenshots'][0]['id']
    assert service.image(index['screenshots'][0]['id'])['mime']=='image/png'
    review=service.review(item['id'], 'observed_success', 'I personally observed a specific answer in my screenshot.', screenshot_ids=[])
    assert review['result_is_not_independently_verified'] is True
    assert service.get(item['id'])['screenshots']==[]
    assert service.list(model='GPT')[0]['id']!=item['id']
