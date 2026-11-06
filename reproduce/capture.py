#!/usr/bin/env python3
"""Read-only, metadata-only inventory. Never copy credentials, profiles or configs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()


def capture(output, voxl_source=None):
    # Refuse symlinks/existing directories rather than overwriting private data.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    packages = []
    text = run(['dpkg-query', '-W', '-f=${db:Status-Status}\t${binary:Package}\t${Version}\t${Architecture}\n'])
    for line in text.splitlines():
        status, name, version, arch = line.split('\t')
        if status == 'installed':
            packages.append({'name': name, 'version': version, 'arch': arch})
    packages.sort(key=lambda p: p['name'])
    manual = sorted(run(['apt-mark', 'showmanual']).splitlines())
    (output / 'apt-installed.json').write_text(json.dumps(packages, indent=2) + '\n')
    (output / 'apt-manual.list').write_text('\n'.join(manual) + '\n')
    (output / 'apt-exact.list').write_text(''.join(f'{p["name"]}={p["version"]}\n' for p in packages))
    release = '\n'.join(line for line in Path('/etc/os-release').read_text().splitlines()
                        if line.startswith(('ID=', 'VERSION_ID=', 'VERSION_CODENAME=')))
    (output / 'os-release.txt').write_text(release + '\n')
    metadata = {'architecture': run(['dpkg', '--print-architecture'])}
    for name, args in {'virtualization': ['systemd-detect-virt'],
                       'rust': ['rustc', '--version'], 'cargo': ['cargo', '--version'],
                       'node': ['node', '--version'], 'esbuild': ['esbuild', '--version'],
                       'bun': ['bun', '--version'], 'uv': ['uv', '--version']}.items():
        try:
            metadata[name] = run(args)
        except (OSError, subprocess.CalledProcessError):
            metadata[name] = 'unavailable'
    # Names and versions only, not paths, npm config, repository URLs or environment.
    try:
        npm = json.loads(run(['npm', 'list', '-g', '--depth=0', '--json']))
        metadata['npm_global'] = {name: item.get('version') for name, item in npm.get('dependencies', {}).items()}
    except (OSError, ValueError, subprocess.CalledProcessError):
        metadata['npm_global'] = 'unavailable'
    if voxl_source is not None:
        try:
            metadata['voxl_commit'] = run(['git', '-C', str(voxl_source), 'rev-parse', 'HEAD'])
            metadata['voxl_has_uncommitted_changes'] = bool(run(['git', '-C', str(voxl_source), 'status', '--porcelain']))
        except subprocess.CalledProcessError:
            metadata['voxl_commit'] = 'not a Git checkout'
    else:
        metadata['voxl_commit'] = 'not supplied; use --voxl-source to inventory separately'
    (output / 'tools.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (output / 'README.txt').write_text(
        'Package names/versions and public runtime metadata only. Review before sharing.\n'
        'apt-exact.list is an AUDIT LOCK, not an automatic install list. Kali Rolling\n'
        'may no longer serve these versions. It includes LXC-specific/base packages.\n'
        'No apt URLs, config files, environment, Git remotes, logs, credentials,\n'
        'personal files, agent history, browser data or machine identifiers copied.\n')
    checksums = []
    for path in sorted(output.iterdir()):
        path.chmod(0o600)
        checksums.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}')
    (output / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
    (output / 'SHA256SUMS').chmod(0o600)
    print(f'Created metadata-only inventory at {output} ({len(packages)} installed packages).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory; must not already exist')
    parser.add_argument('--voxl-source', type=Path, help='optional, separate Voxl Git checkout')
    args = parser.parse_args()
    os.umask(0o077)
    capture(args.output, args.voxl_source)
