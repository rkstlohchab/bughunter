"""Verify a locally built Tauri .app bundle on macOS (no signing identity required)."""
import argparse
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',type=Path,default=ROOT/'src-tauri/target/release/bundle/macos/BugHunterClaudex.app')
    parser.add_argument('--smoke',action='store_true',help='Run full packaged backend stdio smoke')
    args=parser.parse_args()
    if platform.system()!='Darwin':
        parser.error('macOS validation must run on a real macOS machine')
    bundle=args.bundle.expanduser().resolve()
    if not (bundle/'Contents/Info.plist').is_file():raise FileNotFoundError('Missing macOS Info.plist')
    info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier')!='dev.bughunterclaudex.workbench':
        raise ValueError('Unexpected bundle identifier')
    execname=info.get('CFBundleExecutable')
    main_binary=bundle/'Contents/MacOS'/execname
    if not main_binary.is_file(): raise FileNotFoundError('Main application executable missing')
    run('codesign','--verify','--deep','--strict',str(bundle))
    architecture=platform.machine()
    if architecture not in run('lipo','-archs',str(main_binary)).split():
        raise ValueError('Bundle lacks host CPU architecture')
    # Tauri removes the build-time target triple when bundling externalBin.
    sidecars=[p for p in (bundle/'Contents/MacOS').iterdir()
              if p.is_file() and (p.name=='bughunter-backend' or p.name.startswith('bughunter-backend-'))]
    if not sidecars:
        raise FileNotFoundError('Packaged Python backend sidecar missing')
    compatible=[p for p in sidecars if architecture in run('lipo','-archs',str(p)).split()]
    if not compatible:
        raise ValueError('Backend sidecar has wrong CPU architecture')
    resource=(bundle/'Contents')
    if not any(p.name=='mac_memory_mcp.py' for p in resource.rglob('mac_memory_mcp.py')):
        raise FileNotFoundError('macOS memory resource missing')
    if args.smoke:
        subprocess.run([sys.executable,str(ROOT/'scripts/smoke_production_workflow.py'),str(compatible[0])],check=True)
        subprocess.run([sys.executable,str(ROOT/'scripts/smoke_backend.py'),str(compatible[0])],check=True)
    print('PASS: signature, bundle id, host architectures, packaged sidecar, resources' + (' and backend smoke' if args.smoke else ''))
    print('NOTE: ad-hoc signing verification is not Apple notarization; verify distribution separately.')

if __name__=='__main__': main()
