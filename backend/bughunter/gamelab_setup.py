"""Read-only environment discovery before selecting a game workflow in Chat."""
from pathlib import Path
import platform
from .tool_installer import executable
from .process_runtime import run


def inspect_setup(app):
    adb=executable('adb');devices=[];error=None
    if adb:
        try:
            reply=run([adb,'devices','-l'],timeout=12)
            if reply['exit_code']:error=reply['stderr'][-600:]
            for line in reply['stdout'].splitlines():
                parts=line.split()
                if len(parts)>=2 and parts[1] in {'device','offline','unauthorized','recovery','sideload'}:
                    devices.append({'serial':parts[0],'state':parts[1],'details':' '.join(parts[2:]),'emulator':parts[0].startswith('emulator-')})
        except (OSError,ValueError,TimeoutError) as exc:error=str(exc)
    root=Path(__file__).resolve().parents[2]
    weights=root/'cheats/mobile-bgmi/weights/bgmi_v8n.pt'
    return {'host_platform':platform.system(),'adb_installed':bool(adb),'scrcpy_installed':bool(executable('scrcpy')),
            'devices':devices,'discovery_error':error,'apk_required':False,
            'apk_explanation':'An APK is needed to inspect package contents. Device discovery and screen mirroring do not require attaching one.',
            'screen_analysis':{'weights_found':weights.is_file(),'default_weights':str(weights),'capability':'Visible-screen detection only; it cannot reveal occluded or off-screen entities.'},
            'runtime_analysis':{'build_verified':False,'offsets_verified':False,'detail':'Needs the selected device/runtime and exact game build. Templates and feature flags are not verification.'},
            'next_question':('Which detected device or desktop runtime should I use, and which game build is installed?' if devices else
              'Where is the game installed: an Android phone, emulator, or this computer? Connect/unlock the device if needed.'),
            'missing_tools':[name for name in ('adb','scrcpy') if not executable(name)],
            'native_memory':'Process-memory operations retain the existing native bridge and its review controls.'}
