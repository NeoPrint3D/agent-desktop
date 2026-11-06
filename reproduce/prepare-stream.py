#!/usr/bin/env python3
"""Generate new target-only Voxl authentication; never copies or prints old secrets."""
import argparse
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
if os.getuid() == 0:
    raise SystemExit('Run as the target desktop user, not root.')
if not args.apply:
    print('Would generate ~/.config/voxl/stream.env and an enable-stream marker. No changes made.')
    raise SystemExit(0)
if os.environ.get('REPROVISION_FRESH_GUEST') != 'YES':
    raise SystemExit('Refusing: set REPROVISION_FRESH_GUEST=YES only in the fresh guest.')
root = Path.home() / '.config/voxl'
if root.is_symlink() or not root.resolve().is_relative_to(Path.home().resolve()):
    raise SystemExit('Refusing symlink/outside-home credential directory.')
os.umask(0o077)
root.mkdir(mode=0o700, parents=True, exist_ok=True)
root.chmod(0o700)
# Exclusive create: never overwrite an existing password, even through a symlink.
fd = os.open(root / 'stream.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write('VOXL_PASSWORD=' + secrets.token_hex(24) + '\n')
marker = root / 'enable-stream'
if not marker.exists():
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
print(f'Created a NEW password in {root / "stream.env"}; read it locally on the guest.')
print('No service started. Log into/reload i3 to import DISPLAY/XAUTHORITY and start Voxl.')
