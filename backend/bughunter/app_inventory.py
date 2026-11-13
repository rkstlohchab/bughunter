"""Read installed-app metadata from standard application catalogs; never launch a target."""
from __future__ import annotations
import configparser
import platform
import plistlib
from pathlib import Path
import re


def _key(value):return re.sub(r'[^a-z0-9]','',str(value).casefold())


def _mac(roots):
    seen=set()
    for root in roots:
        try:entries=sorted(root.iterdir())
        except (OSError,PermissionError):continue
        bundles=[]
        for entry in entries[:2000]:
            if entry.suffix=='.app':bundles.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                try:bundles.extend(p for p in entry.iterdir() if p.suffix=='.app')
                except (OSError,PermissionError):pass
        for path in bundles:
            if str(path) in seen:continue
            seen.add(str(path));info={};info_path=None
            candidates=[path/'Contents/Info.plist',path/'Info.plist']
            try:candidates+=list((path/'Wrapper').glob('*.app/Info.plist'))
            except OSError:pass
            for candidate in candidates:
                try:
                    if candidate.stat().st_size>2_000_000:continue
                    with candidate.open('rb') as stream:info=plistlib.load(stream)
                    if isinstance(info,dict):info_path=candidate;break
                except (OSError,ValueError,plistlib.InvalidFileException):continue
            item={'name':info.get('CFBundleDisplayName') or info.get('CFBundleName') or path.stem,'path':str(path),
                  'bundle_id':info.get('CFBundleIdentifier'),'version':info.get('CFBundleShortVersionString'),
                  'build':str(info.get('CFBundleVersion','')) or None,'platform':'macOS','metadata_verified':bool(info_path)}
            if info_path:
                item['metadata_path']=str(info_path)
                platforms=info.get('CFBundleSupportedPlatforms') or []
                if 'iPhoneOS' in platforms:item['runtime']='iOS app packaged for macOS'
                else:item['runtime']='macOS bundle; runtime not launched'
                name=info.get('CFBundleExecutable','')
                if isinstance(name,str) and name and '/' not in name:
                    binary=info_path.parent/('MacOS' if info_path.parent.name=='Contents' else '')/name
                    item['executable']=str(binary);item['executable_found']=binary.is_file()
            yield item


def _linux(roots):
    seen=set()
    for root in roots:
        try:entries=list(root.glob('*.desktop'))
        except OSError:continue
        for path in entries[:2000]:
            if path.name in seen:continue
            seen.add(path.name)
            try:
                parser=configparser.ConfigParser(interpolation=None,strict=False);parser.read(path,encoding='utf-8')
                data=parser['Desktop Entry']
                if data.get('Type')!='Application' or data.get('Hidden')=='true':continue
                yield {'name':data.get('Name',path.stem),'path':str(path),'desktop_id':path.name,'version':None,
                       'runtime':'Linux desktop entry; runtime not launched','platform':'Linux','metadata_verified':True}
            except (OSError,ValueError,configparser.Error,KeyError):continue


def _windows():
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
        for prefix in ('SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall','SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall'):
            try:root=winreg.OpenKey(hive,prefix)
            except OSError:continue
            with root:
                for index in range(min(winreg.QueryInfoKey(root)[0],4000)):
                    try:
                        with winreg.OpenKey(root,winreg.EnumKey(root,index)) as key:
                            def value(name):
                                try:return winreg.QueryValueEx(key,name)[0]
                                except OSError:return None
                            name=value('DisplayName')
                            if name:yield {'name':name,'version':value('DisplayVersion'),'path':value('InstallLocation'),
                                           'publisher':value('Publisher'),'platform':'Windows','metadata_verified':True,
                                           'runtime':'Registered application; runtime not launched'}
                    except OSError:continue


def inventory(query='',limit=30,*,system=None,roots=None):
    if not isinstance(query,str) or len(query)>150:raise ValueError('Use an app name under 150 characters.')
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Choose 1–100 app results.')
    system=system or platform.system();search=_key(query)
    if system=='Darwin':rows=_mac(roots or [Path('/Applications'),Path.home()/'Applications',Path('/System/Applications')])
    elif system=='Linux':rows=_linux(roots or [Path('/usr/share/applications'),Path('/usr/local/share/applications'),Path.home()/'.local/share/applications'])
    elif system=='Windows':rows=_windows()
    else:rows=[]
    items=[];seen=set()
    for item in rows:
        if search and search not in _key(item['name']) and search not in _key(item.get('path') or ''):continue
        identity=(item['name'],item.get('path'))
        if identity in seen:continue
        seen.add(identity);items.append(item)
        if len(items)>limit:break
    return {'platform':system,'query':query,'items':items[:limit],'truncated':len(items)>limit,
            'evidence':'Read local application metadata only. A listed application is not proof of a running process, compatible game build, or working feature.'}
