#!/usr/bin/env bash
# Read-only verification: never clicks or types into a Mac app.
set -Eeuo pipefail
if [[ "$(uname -s)" != 'Darwin' ]]; then echo 'Run this on your Mac.' >&2; exit 2; fi
if ! command -v agent-desktop >/dev/null 2>&1; then echo 'Missing agent-desktop. Rerun: bash setup-mac.sh --core-only' >&2; exit 3; fi
printf 'CLI version:\n'
agent-desktop version
printf '\nmacOS permission status:\n'
agent-desktop permissions
printf '\nForeground app snapshot (read-only):\n'
if agent-desktop snapshot -i --compact; then
  echo 'PASS: agent-desktop can inspect the currently focused app.'
else
  echo 'No accessible foreground app or missing Accessibility permission.' >&2
  echo 'Run: agent-desktop permissions --guide' >&2
  exit 4
fi
