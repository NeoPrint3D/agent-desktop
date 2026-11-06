#!/usr/bin/env python3
"""Offline recipe checks. No provisioning, package installation or service changes."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
for path in root.glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
for path in list(root.glob('*.sh')) + list((root / 'desktop/bin').iterdir()):
    text = path.read_text()
    if text.startswith(('#!/usr/bin/env python3', '#!/usr/bin/python3')):
        ast.parse(text, filename=str(path))
    elif text.startswith(('#!/usr/bin/env bash', '#!/bin/bash', '#!/bin/sh')):
        subprocess.run(['bash', '-n', str(path)], check=True)
for path in (root / 'desktop').rglob('*'):
    if path.is_file():
        text = path.read_text()
        for forbidden in ('/home/drew', '/root/', '/run/user/1000', '10.200.1.86',
                          'BEGIN PRIVATE KEY', 'BEGIN OPENSSH PRIVATE KEY'):
            assert forbidden not in text, f'Unportable/private content in {path}: {forbidden}'
json.loads((root / 'sources.json').read_text())
json.loads((root / 'workstation.json').read_text())
subprocess.run(['bash', '-n', str(root.parent / 'setup.sh')], check=True)
# i3 layouts may contain several concatenated JSON objects.
text = (root / 'desktop/config/i3/files-layout.json').read_text().strip()
decoder = json.JSONDecoder()
while text:
    _, end = decoder.raw_decode(text)
    text = text[end:].strip()

# Check the real installer against an isolated fake home, not this user's config.
spec = importlib.util.spec_from_file_location('installer', root / 'install-desktop.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='voxl-recipe-check-') as tmp:
    home = Path(tmp)
    module.install(home, apply=False)
    assert not list(home.iterdir()), 'Dry run wrote files'
    module.install(home, apply=True)
    assert (home / '.local/bin/voxl-launch').is_file()
    assert (home / '.config/i3/config').is_file()
    for p in home.rglob('*'):
        if p.is_file():
            assert '@HOME@' not in p.read_text(), f'Unrendered template: {p}'
    if shutil.which('i3'):
        subprocess.run(['i3', '-C', '-c', str(home / '.config/i3/config')], check=True)
    module.install(home, apply=True)
    assert not (home / '.local/state/voxl-reproduce-backups').exists(), 'Idempotent run made backups'
    env = {**os.environ, 'HOME': str(home), 'REPROVISION_FRESH_GUEST': 'YES'}
    subprocess.run([sys.executable, str(root / 'prepare-stream.py')], env=env, check=True, capture_output=True)
    assert not (home / '.config/voxl/stream.env').exists(), 'Secret dry run wrote a file'
    created = subprocess.run([sys.executable, str(root / 'prepare-stream.py'), '--apply'],
                             env=env, check=True, capture_output=True, text=True)
    secret_path = home / '.config/voxl/stream.env'
    secret = secret_path.read_text()
    assert secret.startswith('VOXL_PASSWORD=') and len(secret.strip().split('=', 1)[1]) == 48
    assert secret.strip().split('=', 1)[1] not in created.stdout, 'Password leaked to stdout'
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    again = subprocess.run([sys.executable, str(root / 'prepare-stream.py'), '--apply'],
                           env=env, capture_output=True)
    assert again.returncode != 0 and secret_path.read_text() == secret, 'Existing password overwritten'
print('PASS: Python/shell syntax, template hygiene, JSON, dry runs, isolated install/idempotence, fresh-secret permissions/refusal to overwrite, available i3 config validation.')
subprocess.run([sys.executable, '-B', str(root / 'test_workstation.py')], check=True)
subprocess.run([sys.executable, '-B', str(root / 'smoke_desktop.py'), '--run'], check=True)
print('PASS: one-command workstation tests (offline plan, manifest/locks, Chrome policy, isolated user config/idempotence, symlink/conflict refusal, mocked provisioning modes and pinned-source patch handling).')
print('Not a full VM build, functional desktop test or comprehensive secret scan.')
