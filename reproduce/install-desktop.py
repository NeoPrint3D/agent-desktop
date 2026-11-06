#!/usr/bin/env python3
"""Install reviewed templates for the current guest user. Default: print plan only."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil


def install(home, apply=False):
    if os.getuid() == 0:
        raise SystemExit('Run as the normal target user, not root.')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(home)):
        raise SystemExit('Unsupported home path: templates require a simple absolute path.')
    source = Path(__file__).resolve().parent / 'desktop'
    files = []
    for src in sorted(source.rglob('*')):
        if not src.is_file():
            continue
        rel = src.relative_to(source)
        dst = home / ('.local' if rel.parts[0] == 'bin' else '.config') / Path(*rel.parts[1:])
        if rel.parts[0] == 'bin':
            dst = home / '.local/bin' / src.name
        if dst.is_symlink() or not dst.resolve().is_relative_to(home.resolve()):
            raise SystemExit(f'Refusing symlink/outside-home destination: {dst}')
        text = src.read_text().replace('@HOME@', str(home))
        files.append((src, dst, text, rel.parts[0] == 'bin'))
    backup = home / '.local/state/voxl-reproduce-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for src, dst, text, executable in files:
        if dst.exists() and dst.read_text() == text:
            print(f'Unchanged: {dst}')
            continue
        print(f'{"Install" if apply else "Would install"}: {dst}')
        if not apply:
            continue
        if dst.exists():
            saved = backup / dst.relative_to(home)
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(dst, saved)
            saved.chmod(0o600)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)
        dst.chmod(0o700 if executable else 0o600)
    if apply:
        print(f'Previous files, if any, backed up under {backup}')
        print('No service was started. Log into an i3 session after validation.')
    else:
        print('DRY RUN: no changes. Use --apply only in a fresh guest after review.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and os.environ.get('REPROVISION_FRESH_GUEST') != 'YES':
        raise SystemExit('Refusing: set REPROVISION_FRESH_GUEST=YES only inside your fresh guest.')
    install(Path.home(), args.apply)
