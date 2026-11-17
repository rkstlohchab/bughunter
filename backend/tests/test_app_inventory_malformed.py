"""One malformed third-party Info.plist must not break the installed-app scan."""
import plistlib

from bughunter.app_inventory import inventory


def test_malformed_plist_is_skipped_and_other_apps_still_listed(tmp_path):
    bad = tmp_path / 'Broken Game.app' / 'Contents'
    bad.mkdir(parents=True)
    (bad / 'Info.plist').write_bytes(b'<?xml version="1.0"?>\n<plist><dict><key>CFBundleName</key>\n\x01bad</dict></plist>')
    good = tmp_path / 'Good.app' / 'Contents'
    good.mkdir(parents=True)
    (good / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleName': 'Good', 'CFBundleShortVersionString': '1.2'}))
    weird = tmp_path / 'List.app' / 'Contents'
    weird.mkdir(parents=True)
    (weird / 'Info.plist').write_bytes(plistlib.dumps(['not', 'a', 'dict']))
    items = {i['name']: i for i in inventory(system='Darwin', roots=[tmp_path])['items']}
    assert items['Good']['version'] == '1.2' and items['Good']['metadata_verified']
    assert items['Broken Game']['metadata_verified'] is False
    assert items['List']['metadata_verified'] is False
