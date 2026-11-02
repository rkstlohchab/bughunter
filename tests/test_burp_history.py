from bughunter.burp_history import safe_item
P={'scope':['https://example.org/'], 'authorization':'owned and authorized', 'exclusions':[]}
def test_in_scope_burp_metadata():
    item={'method':'GET','scheme':'https','host':'example.org','path':'/hello','status':200,'burp_history_id':42}
    assert safe_item(item,P)['status']==200
    assert safe_item({**item,'host':'other.org'},P) is None
    assert safe_item({**item,'path':'/hello?token=abc'},P) is None

def test_invalid_history_items_rejected():
    assert safe_item({'method':'TRACE','scheme':'https','host':'example.org','path':'/','status':200},P) is None
    assert safe_item({'method':'GET','scheme':'file','host':'example.org','path':'/','status':200},P) is None
