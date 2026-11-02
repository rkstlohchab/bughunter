"""Private local backup/verify/restore CLI. Backup while app running is supported;
restore only into an empty directory with the desktop app closed.
"""
import argparse
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from bughunter.backup import create, verify, restore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Private BugHunterClaudex data backup')
    parser.add_argument('action', choices=['create', 'verify', 'restore'])
    parser.add_argument('--data-dir', help='Source data directory when creating a backup')
    parser.add_argument('--archive', required=True, help='Backup archive path')
    parser.add_argument('--destination', help='New empty restore destination')
    args = parser.parse_args()
    if args.action == 'create':
        from bughunter.store import Store
        source = Path(args.data_dir).expanduser() if args.data_dir else Store().root
        result = create(source, args.archive)
    elif args.action == 'verify':
        result = verify(args.archive)
    else:
        if not args.destination:
            parser.error('--destination is required for restore')
        result = restore(args.archive, args.destination)
    print(json.dumps(result, indent=2))

if __name__ == '__main__': main()
