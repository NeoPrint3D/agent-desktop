#!/usr/bin/env bash
# Always dry-run unless --apply is explicitly requested. Only for a FRESH Kali guest.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
apply=false
lists=(packages.list)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --security-tools) lists+=(security-tools.list); shift ;;
    --extra-list)
      [[ $# -ge 2 && -f $2 ]] || { echo 'Missing --extra-list file' >&2; exit 2; }
      lists+=("$2"); shift 2 ;;
    *) printf 'Usage: %s [--apply] [--security-tools] [--extra-list /absolute/path/to/approved.list]\n' "$0" >&2; exit 2 ;;
  esac
done
if [[ $(id -u) == 0 ]]; then
  echo 'Run as the normal target user, not root; --apply uses sudo.' >&2; exit 1
fi
if ! grep -qx 'ID=kali' /etc/os-release; then
  echo 'This recipe targets Kali. Use a fresh Kali VM, not the host or another distro.' >&2; exit 1
fi
mapfile -t packages < <(grep -hEv '^[[:space:]]*(#|$)' "${lists[@]}" | sort -u)
for package in "${packages[@]}"; do
  [[ $package =~ ^[a-z0-9][a-z0-9+.-]*(:[a-z0-9-]+)?$ ]] || { echo "Invalid package name: $package" >&2; exit 2; }
done
if "$apply"; then
  if [[ ${REPROVISION_FRESH_GUEST:-} != YES ]]; then
    echo 'Refusing: set REPROVISION_FRESH_GUEST=YES only inside your fresh guest.' >&2; exit 1
  fi
  sudo apt-get update
  # No -y: allow review of changes/removals and package configuration questions.
  sudo apt-get install -- "${packages[@]}"
else
  printf 'DRY RUN: no packages will be installed. Run apt-get update in the guest first.\n'
  apt-get --simulate install -- "${packages[@]}"
fi
