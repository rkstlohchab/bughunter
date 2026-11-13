from pathlib import Path
import plistlib
from bughunter.app_inventory import inventory


def test_mac_wrapped_ios_bundle_is_discovered_by_spaced_name(tmp_path):
 root=tmp_path/'Applications';bundle=root/'AmongUs.app/Wrapper/AmongUs.app';bundle.mkdir(parents=True)
 (bundle/'Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Among Us','CFBundleIdentifier':'fixture.game','CFBundleExecutable':'AmongUs','CFBundleShortVersionString':'1.2','CFBundleVersion':'7','CFBundleSupportedPlatforms':['iPhoneOS']}))
 (bundle/'AmongUs').write_bytes(b'fixture')
 row=inventory('among us',system='Darwin',roots=[root])['items'][0]
 assert row['version']=='1.2' and row['build']=='7'
 assert row['runtime']=='iOS app packaged for macOS' and row['executable_found']
 assert inventory('absent',system='Darwin',roots=[root])['items']==[]


def test_linux_catalog_is_metadata_only(tmp_path):
 (tmp_path/'game.desktop').write_text('[Desktop Entry]\nType=Application\nName=Fixture App\nExec=never-run-this\n')
 assert inventory('fixture',system='Linux',roots=[tmp_path])['items'][0]['name']=='Fixture App'
