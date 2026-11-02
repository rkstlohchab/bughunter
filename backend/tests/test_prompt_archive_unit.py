import pytest
from bughunter.prompt_archive import analyze_source


def test_candidate_categories_keep_source_locators_and_do_not_label_success():
    result=analyze_source({'id':'source_1','filename':'prompts.zip','status':'ready','chunks':[
        {'id':'c1','locator':'cases/alpha.md','text':'Compare system prompt authority messages.'},
        {'id':'c2','locator':'cases/beta.txt','text':'Test base64 and multilingual translation.'},
    ]})
    assert result['ready'] and result['total_chunks']==2
    assert {'Authority and instruction spoofing','Encoding and obfuscation'} <= {r['name'] for r in result['categories']}
    assert any(x['locator']=='cases/alpha.md' for x in result['examples'])
    assert 'success' not in result


def test_pending_archive_and_other_files():
    assert analyze_source({'id':'source_1','filename':'a.ZIP','status':'queued','chunks':[]})['ready'] is False
    with pytest.raises(ValueError,match='ZIP archive'):
        analyze_source({'id':'source_2','filename':'a.pdf','chunks':[]})
