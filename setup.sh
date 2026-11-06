#!/usr/bin/env bash
# Single entry point. No network or machine changes without --apply.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v python3 >/dev/null || { echo 'Install python3 first: sudo apt-get install python3' >&2; exit 1; }
exec python3 -B reproduce/setup.py "$@"
