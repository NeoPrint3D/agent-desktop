#!/usr/bin/env python3
"""Network audit in a private temporary APT root; never installs packages or uses sudo."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import setup


def audit(output_dir):
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    output_dir = output_dir.resolve()
    manifest = json.loads((setup.ROOT / 'workstation.json').read_text())
    with tempfile.TemporaryDirectory(prefix='workstation-apt-audit-') as tmp:
        root = Path(tmp)
        for name in ('lists/partial', 'cache/archives/partial', 'etc/apt/apt.conf.d', 'etc/apt/preferences.d', 'keys', 'gnupg'):
            (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
        status = root / 'status'
        status.write_text('')
        sources = ['deb [arch=amd64] https://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware']
        # Use the distro's installed archive keyring without importing its source configuration.
        kali_keyring = Path('/usr/share/keyrings/kali-archive-keyring.gpg')
        if not kali_keyring.is_file():
            raise RuntimeError('Run on Kali with kali-archive-keyring installed')
        sources[0] = sources[0].replace('arch=amd64]', f'arch=amd64 signed-by={kali_keyring}]')
        fingerprints = {}
        for repo in manifest['repositories']:
            key = root / 'keys' / (repo['name'] + '.key')
            binary = key.with_suffix('.gpg')
            setup.run(['curl', '--fail', '--location', '--silent', '--show-error', '--retry', '2',
                       '--proto', '=https', '--proto-redir', '=https', '--max-time', '90', repo['key_url'], '-o', key])
            args = ['gpg', '--batch', '--no-options', '--homedir', root / 'gnupg']
            found = setup.primary_fingerprints(setup.output([*args, '--show-keys', '--with-colons', key]))
            if found != sorted(repo['fingerprints']):
                raise RuntimeError(f'{repo["name"]}: signing-key mismatch')
            fingerprints[repo['name']] = found
            setup.run([*args, '--import', key], stdout=subprocess.DEVNULL)
            with binary.open('wb') as stream:
                setup.run([*args, '--export', *found], stdout=stream)
            sources.append(f'deb [arch=amd64 signed-by={binary}] {repo["url"]} {repo["suite"]} {repo["components"]}')
        source_list = root / 'sources.list'
        source_list.write_text('\n'.join(sources) + '\n')
        config = root / 'apt.conf'
        def quoted(value):
            return json.dumps(str(value))
        config.write_text('\n'.join([
            f'Dir {quoted(root)};',
            f'Dir::Etc::parts {quoted(root / "etc/apt/apt.conf.d")};',
            'Dir::Etc::main "/dev/null";',
            f'Dir::Etc::sourcelist {quoted(source_list)};',
            'Dir::Etc::sourceparts "-";',
            f'Dir::State::status {quoted(status)};',
            f'Dir::State::lists {quoted(root / "lists")};',
            f'Dir::Cache {quoted(root / "cache")};',
            'APT::Architecture "amd64";',
            'APT::Architectures { "amd64"; };',
            'APT::Get::List-Cleanup "false";',
            'APT::Update::Error-Mode "any";',
            'Acquire::Languages "none";',
            'Acquire::Retries "2";',
            'Acquire::http::Timeout "60";',
            'Acquire::https::Timeout "60";',
            # APT stays unprivileged; no root-owned sandbox user needs access to this directory.
            f'APT::Sandbox::User {quoted(setup.output(["id", "-un"]))};',
        ]) + '\n')
        env = {**os.environ, 'APT_CONFIG': str(config)}
        started = time.time()
        results = []
        for name, command in [('update', ['apt-get', 'update']),
                              ('resolve-empty-status', ['apt-get', '--simulate', 'install', '--no-remove', '--', *setup.packages(manifest)])]:
            with (output_dir / (name + '.log')).open('w') as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            results.append({'check': name, 'exit_code': result.returncode})
            if result.returncode:
                break
        passed = len(results) == 2 and all(r['exit_code'] == 0 for r in results)
        report = {'passed': passed, 'checks': results, 'vendor_fingerprints': fingerprints,
                  'elapsed_seconds': round(time.time() - started, 1), 'installed_packages': False,
                  'qualification': 'Signed indexes + dependency resolution against an empty status database, not a VM install or boot test.'}
        (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'{"PASS" if passed else "FAIL"}: isolated APT audit; logs/report: {output_dir}')
        return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new private output directory; parent must exist')
    args = parser.parse_args()
    os.umask(0o077)
    raise SystemExit(0 if audit(args.output) else 1)
