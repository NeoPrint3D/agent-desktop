#!/usr/bin/env bash
# Optional alternative to a native i3 login, run as the target guest user.
set -euo pipefail
if [[ $(id -u) == 0 ]]; then echo 'Do not run the desktop as root.' >&2; exit 1; fi
if [[ -n ${DISPLAY:-} ]]; then
  echo 'Already in an X session. Use the native desktop, or a separate headless-only user.' >&2
  exit 1
fi
: "${XDG_RUNTIME_DIR:?Log in normally as the guest user so systemd creates its runtime directory}"
size=${VOXL_SCREEN_SIZE:-1920x1080x24}
[[ $size =~ ^[0-9]+x[0-9]+x24$ ]] || { echo 'Expected WIDTHxHEIGHTx24' >&2; exit 1; }
trap 'systemctl --user stop voxl-stream.service 2>/dev/null || true' EXIT
# xvfb-run creates a new ephemeral Xauthority cookie. No -ac or public X TCP port.
xvfb-run -a -s "-screen 0 $size -shmem -nolisten tcp" dbus-run-session -- i3
