#!/usr/bin/env python3
"""One-command fresh Kali provisioning. Default is an offline, read-only plan."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

from workstation_user import checked, configure, extension_policy

ROOT = Path(__file__).resolve().parent


def run(args, **kwargs):
    print('+ ' + shlex.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def output(args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, **kwargs).strip()


def packages(manifest):
    names = set(manifest['external_packages'])
    for filename in manifest['apt_lists']:
        for line in (ROOT / filename).read_text().splitlines():
            line = line.partition('#')[0].strip()
            if line:
                names.add(line)
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(:[a-z0-9-]+)?', p) for p in names):
        raise ValueError('Invalid APT package name')
    return sorted(names)


def primary_fingerprints(colons):
    result, primary = [], False
    for line in colons.splitlines():
        fields = line.split(':')
        if fields[0] == 'pub':
            primary = True
        elif fields[0] == 'fpr' and primary:
            result.append(fields[9])
            primary = False
    return sorted(result)


def install_repositories(manifest):
    run(['sudo', 'install', '-d', '-m', '755', '/etc/apt/keyrings'])
    with tempfile.TemporaryDirectory(prefix='workstation-keys-') as tmp:
        tmp = Path(tmp)
        for repo in manifest['repositories']:
            name = repo['name']
            key, binary = tmp / (name + '.key'), tmp / (name + '.gpg')
            run(['curl', '--fail', '--location', '--silent', '--show-error', '--retry', '3',
                 '--proto', '=https', '--proto-redir', '=https', '--max-time', '120',
                 repo['key_url'], '-o', key])
            listing = output(['gpg', '--batch', '--no-options', '--homedir', tmp,
                              '--show-keys', '--with-colons', key])
            if primary_fingerprints(listing) != sorted(repo['fingerprints']):
                raise RuntimeError(f'{name}: signing key mismatch; review vendor rotation, do not bypass verification')
            # Import then export also handles a vendor serving binary instead of armored keys.
            run(['gpg', '--batch', '--no-options', '--homedir', tmp, '--import', key], stdout=subprocess.DEVNULL)
            with binary.open('wb') as f:
                run(['gpg', '--batch', '--no-options', '--homedir', tmp, '--export',
                     *repo['fingerprints']], stdout=f)
            keyring = '/etc/apt/keyrings/workstation-' + name + '.gpg'
            run(['sudo', 'install', '-m', '644', binary, keyring])
            source = tmp / (name + '.list')
            source.write_text(f'deb [arch=amd64 signed-by={keyring}] {repo["url"]} {repo["suite"]} {repo["components"]}\n')
            run(['sudo', 'install', '-m', '644', source, '/etc/apt/sources.list.d/workstation-' + name + '.list'])


def link_tool(home, name, executable):
    directory = checked(home, '.local/bin')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    link = directory / name
    if link.is_symlink() and link.resolve() == executable.resolve():
        return
    if link.exists() or link.is_symlink():
        raise RuntimeError(f'Existing non-recipe executable: {link}; move it aside explicitly before retrying')
    link.symlink_to(executable)


def install_user_tools(home):
    node_root = checked(home, '.local/share/workstation-rebuild/node-tools')
    node_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('package.json', 'package-lock.json'):
        dst = checked(home, str((node_root / name).relative_to(home)))
        dst.write_bytes((ROOT / 'node-tools' / name).read_bytes())
    # Pin transitive dependencies and integrity hashes, then run only the two
    # explicitly selected packages' binary setup scripts, never arbitrary lifecycle hooks.
    run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=node_root)
    run(['npm', 'rebuild', 'bun', '@opencode/cli'], cwd=node_root)
    for name in ('pi', 'bw', 'opencode', 'bun', 'bunx'):
        link_tool(home, name, node_root / 'node_modules/.bin' / name)
    python_root = checked(home, '.local/share/workstation-rebuild/python-tools')
    if not python_root.exists():
        run([sys.executable, '-m', 'venv', python_root])
    run([python_root / 'bin/python', '-m', 'pip', 'install', '--only-binary=:all:',
         '--require-hashes', '--no-deps', '-r', ROOT / 'python-tools.requirements.txt'])
    for name in ('uv', 'uvx'):
        link_tool(home, name, python_root / 'bin' / name)
    for name in ('pi', 'bw', 'opencode', 'bun', 'uv'):
        run([home / '.local/bin' / name, '--version'])


def install_artifacts(home, manifest):
    root = checked(home, '.local/share/workstation-rebuild/artifacts')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for item in manifest.get('artifacts', []):
        if not re.fullmatch(r'[a-z][a-z0-9-]*', item['name']) or not re.fullmatch(r'[a-zA-Z0-9.+-]+', item['version']):
            raise ValueError('Unsafe artifact name/version')
        if not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) or not item['url'].startswith('https://'):
            raise ValueError('Artifact needs HTTPS and a SHA-256 pin')
        destination = checked(home, str((root / (item['name'] + '-' + item['version'])).relative_to(home)))
        binary_hash = item.get('binary_sha256', item['sha256'])
        if not re.fullmatch(r'[a-f0-9]{64}', binary_hash):
            raise ValueError('Artifact binary needs a SHA-256 pin')
        if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != binary_hash:
            with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as staging:
                binary = Path(staging) / item['name']
                run(['curl', '--fail', '--location', '--silent', '--show-error', '--retry', '3',
                     '--proto', '=https', '--proto-redir', '=https', '--max-time', '180', item['url'], '-o', binary])
                if hashlib.sha256(binary.read_bytes()).hexdigest() != item['sha256']:
                    raise RuntimeError(f'{item["name"]}: downloaded artifact checksum mismatch')
                if item.get('archive_member'):
                    # Never extract paths/symlinks from an archive into the filesystem.
                    with tarfile.open(binary, mode='r:gz') as archive:
                        member = archive.getmember(item['archive_member'])
                        if not member.isfile() or member.size > 256 * 1024 * 1024:
                            raise RuntimeError('Artifact archive member is not a bounded regular file')
                        with archive.extractfile(member) as stream:
                            payload = stream.read(256 * 1024 * 1024 + 1)
                    if hashlib.sha256(payload).hexdigest() != binary_hash:
                        raise RuntimeError(f'{item["name"]}: extracted binary checksum mismatch')
                    binary.write_bytes(payload)
                binary.chmod(0o700)
                binary.replace(destination)
        actual = output([destination, *item['version_args']])
        if actual != item.get('version_output', item['version']):
            raise RuntimeError(f'{item["name"]}: version does not match the manifest')
        link_tool(home, item['name'], destination)


@contextmanager
def provisioning_lock(home):
    path = checked(home, '.local/state/workstation-rebuild/setup.lock')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another setup process is already running for this user') from None
        yield
    finally:
        os.close(fd)


def checkout(name, spec, source_root):
    target = source_root / name
    if target.is_symlink():
        raise RuntimeError(f'Refusing symlink checkout: {target}')
    if not target.exists():
        # Publish only a completed checkout; failed downloads remain retryable.
        with tempfile.TemporaryDirectory(prefix=f'.{name}-staging-', dir=source_root) as staging:
            staged = Path(staging) / 'checkout'
            run(['git', 'clone', '--no-checkout', '--', spec['url'], staged])
            run(['git', '-C', staged, 'checkout', '--detach', spec['commit']])
            staged.rename(target)
    if output(['git', '-C', target, 'rev-parse', 'HEAD']) != spec['commit']:
        raise RuntimeError(f'{target}: unexpected commit; preserve your work and move it aside before retrying')
    if output(['git', '-C', target, 'remote', 'get-url', 'origin']) != spec['url']:
        raise RuntimeError(f'{target}: unexpected origin')
    patch = ROOT / spec['patch'] if spec.get('patch') else None
    diff = subprocess.check_output(['git', '-C', str(target), 'diff', '--binary', 'HEAD'])
    if diff and (patch is None or diff != patch.read_bytes()):
        raise RuntimeError(f'{target}: unrecognized local changes; refusing to overwrite')
    if output(['git', '-C', target, 'ls-files', '--others', '--exclude-standard']):
        raise RuntimeError(f'{target}: untracked files; review/move them before retrying')
    if patch and not diff:
        run(['git', '-C', target, 'apply', '--check', patch])
        run(['git', '-C', target, 'apply', patch])
    return target


def provision(manifest, home, headless=False):
    os.umask(0o077)
    os.environ['PATH'] = str(home / '.local/bin') + ':' + os.environ.get('PATH', '')
    os.environ['REPROVISION_FRESH_GUEST'] = 'YES'
    source_root = checked(home, '.local/share/workstation-rebuild/sources')
    source_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    run(['sudo', '-v'])
    # Do not accidentally publish the distro Nginx welcome page while installing.
    run(['sudo', 'systemctl', 'mask', 'nginx.service', 'lightdm.service'])
    run(['sudo', 'apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'])
    run(['sudo', 'apt-get', 'install', '-y', '--no-remove', 'ca-certificates', 'curl', 'gpg', 'git'])
    install_repositories(manifest)
    # Keep the vendor package from replacing our signed-by source on install/cron.
    with tempfile.TemporaryDirectory(prefix='workstation-chrome-defaults-') as tmp:
        defaults = Path(tmp) / 'google-chrome'
        defaults.write_text('repo_add_once="false"\nrepo_reenable_on_distupgrade="false"\n')
        run(['sudo', 'install', '-m', '644', defaults, '/etc/default/google-chrome'])
    run(['sudo', 'apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'])
    # Resolve the entire requested set first. Never silently skip an unavailable application.
    run(['apt-get', '--simulate', 'install', '--no-remove', '--', *packages(manifest)])
    run(['sudo', 'apt-get', 'install', '-y', '--no-remove', '--', *packages(manifest)])
    node_version = output(['node', '-p', 'process.versions.node']).split('.')
    if tuple(map(int, node_version)) < (22, 19, 0):
        raise RuntimeError('Pi requires Node >=22.19.0; the configured Kali archive does not supply it')
    install_user_tools(home)
    install_artifacts(home, manifest)
    sources = {name: checkout(name, spec, source_root) for name, spec in manifest['sources'].items()}
    run(['npm', 'ci', '--ignore-scripts'], cwd=sources['public-browser'])
    run(['npm', 'run', 'build'], cwd=sources['public-browser'])
    run([sys.executable, ROOT / 'install-desktop.py', '--apply'])
    configure(home, manifest, source_root)
    for extension in manifest['editor_extensions']:
        run(['codium', '--user-data-dir', home / '.config/VSCodium',
             '--extensions-dir', home / '.vscode-oss/extensions', '--install-extension', extension])
    with tempfile.TemporaryDirectory(prefix='workstation-policy-') as tmp:
        policy = Path(tmp) / 'workstation-extensions.json'
        policy.write_text(json.dumps(extension_policy(manifest), indent=2) + '\n')
        run(['sudo', 'install', '-d', '-m', '755', '/etc/opt/chrome/policies/managed'])
        run(['sudo', 'install', '-m', '644', policy,
             '/etc/opt/chrome/policies/managed/workstation-extensions.json'])
    env = {**os.environ, 'VOXL_SOURCE_DIR': str(sources['voxl'])}
    run(['bash', ROOT / 'build-voxl.sh', '--apply'], env=env)
    credential = checked(home, '.config/voxl/stream.env')
    if not credential.exists():
        run([sys.executable, ROOT / 'prepare-stream.py', '--apply'])
    else:
        print('Keeping existing Voxl authentication; not reading or printing it.')
    # Fail static acceptance before starting any newly configured session.
    run([sys.executable, ROOT / 'verify.py'])
    if headless:
        run(['sudo', 'systemctl', 'set-default', 'multi-user.target'])
        run(['sudo', 'loginctl', 'enable-linger', output(['id', '-un'])])
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'enable', '--now', 'workstation-headless.service'])
    else:
        run(['sudo', 'systemctl', 'unmask', 'lightdm.service'])
        run(['sudo', 'systemctl', 'enable', 'lightdm.service'])
        run(['sudo', 'systemctl', 'set-default', 'graphical.target'])
    # Type=simple service startup alone is not a readiness check.
    if headless:
        run([sys.executable, ROOT / 'verify.py', '--runtime', '--wait', '180'])
    receipt = checked(home, '.local/state/workstation-rebuild/receipt.json')
    receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    audit_files = [Path(__file__).resolve(), ROOT / 'workstation_user.py', ROOT / 'workstation.json',
                   ROOT / 'node-tools/package.json', ROOT / 'node-tools/package-lock.json',
                   ROOT / 'python-tools.requirements.txt', ROOT / 'install-desktop.py', ROOT / 'verify.py',
                   ROOT / 'build-voxl.sh', ROOT / 'prepare-stream.py', ROOT / 'headless-desktop.sh']
    audit_files += [ROOT / name for name in manifest['apt_lists']]
    audit_files += [ROOT / spec['patch'] for spec in manifest['sources'].values() if spec.get('patch')]
    audit_files += [p for p in (ROOT / 'desktop').rglob('*') if p.is_file()]
    installed = output(['dpkg-query', '-W', '-f=${db:Status-Status}\\t${binary:Package}\\t${Version}\\n'])
    versions = {}
    for line in installed.splitlines():
        fields = line.split('\t')
        if fields[0] == 'installed':
            _, name, version = fields
            versions[name] = version
    receipt.write_text(json.dumps({
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'manifest_sha256': hashlib.sha256((ROOT / 'workstation.json').read_bytes()).hexdigest(),
        'apt_requested': packages(manifest),
        'apt_installed_versions': versions,
        'recipe_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in audit_files},
        'source_commits': {name: s['commit'] for name, s in manifest['sources'].items()},
        'integration_tested': False,
        'desktop_mode': 'headless' if headless else 'graphical-login',
        'remaining_gaps': manifest['remaining_gaps'],
    }, indent=2) + '\n')
    receipt.chmod(0o600)
    if headless:
        print('\nProvisioning completed. Private Xvfb/i3 desktop enabled at boot and started now.')
        print('Connect to Voxl via SSH forwarding; never expose CDP port 9222 to the network.')
    else:
        print('\nProvisioning completed. Reboot when ready and select i3 at login.')
        print('Chrome/harness and loopback-only Voxl start in the graphical session, not in this SSH session.')
    print('Chrome extensions download on first launch; verify chrome://policy and chrome://extensions.')
    print(f'Fresh Voxl password: {credential} (not displayed). Nginx remains masked.')
    print(f'Receipt: {receipt}. Remaining limitations:')
    for gap in manifest['remaining_gaps']:
        print(' - ' + gap)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='provision THIS fresh Kali machine (uses sudo)')
    parser.add_argument('--verify', action='store_true', help='check an installed target without provisioning; combine with --headless for runtime checks')
    parser.add_argument('--yes', action='store_true', help='acknowledge fresh guest without typing confirmation')
    parser.add_argument('--headless', action='store_true', help='boot into a persistent Xvfb/i3 streaming desktop instead of a display manager')
    args = parser.parse_args()
    if args.apply and args.verify:
        parser.error('--apply and --verify are mutually exclusive')
    if args.verify:
        command = [sys.executable, str(ROOT / 'verify.py')]
        if args.headless:
            command += ['--runtime']
        raise SystemExit(subprocess.run(command).returncode)
    manifest = json.loads((ROOT / 'workstation.json').read_text())
    extension_policy(manifest)
    if not args.apply:
        print('OFFLINE PLAN: no network, sudo, installations, or files changed.')
        print('Target: fresh Kali amd64, normal user with sudo, systemd, Internet access.')
        print('APT applications: ' + ' '.join(packages(manifest)))
        print('Vendor repositories: ' + ', '.join(r['name'] for r in manifest['repositories']))
        print('Sources: ' + ', '.join(f'{k}@{v["commit"]}' for k, v in manifest['sources'].items()))
        tools = json.loads((ROOT / 'node-tools/package.json').read_text())['dependencies']
        print('Locked Node tools: ' + ', '.join(f'{name}@{version}' for name, version in tools.items()))
        print('Will install i3/XFCE, new Chrome profiles, seven managed extensions, VSCodium extensions, uv/uvx, public-browser and patched Voxl.')
        print('Will enable ' + ('a lingering private Xvfb/i3 user service at boot.' if args.headless else 'graphical login for the next boot.'))
        print('No auto-login, reboot, public CDP, TLS or credential import.')
        print('Run ./setup.sh --apply ONLY on the new Kali machine. Remaining gaps:')
        for gap in manifest['remaining_gaps']:
            print(' - ' + gap)
        return
    if os.getuid() == 0:
        parser.error('Run as your normal desktop user, not root. sudo is used only for system steps.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'kali' or output(['dpkg', '--print-architecture']) != manifest['architecture']:
        parser.error('This recipe requires fresh Kali amd64.')
    if not Path('/run/systemd/system').is_dir():
        parser.error('This recipe requires a booted systemd guest.')
    home = Path.home().resolve()
    # This source desktop is not a provisioning target.
    if (ROOT / 'inventory').is_dir():
        parser.error('Source-machine inventory detected: do not apply to the original workstation.')
    if not args.yes:
        print('This installs a large desktop/security stack, trusts the listed vendor keys, applies a machine-wide Chrome extension policy,')
        print('masks Nginx and sets the requested desktop boot mode. It is for a FRESH guest, never your existing workstation.')
        if input('Type SETUP FRESH KALI to continue: ') != 'SETUP FRESH KALI':
            parser.error('Cancelled; no changes made.')
    try:
        if args.headless and os.environ.get('DISPLAY'):
            parser.error('--headless requires a non-graphical login and a dedicated desktop user.')
        if shutil.disk_usage(home).free < 20 * 1024 ** 3:
            raise RuntimeError('Less than 20 GiB free; use a larger fresh guest for the desktop/security stack and Rust build')
        if args.headless:
            # Detect missing SSH/login user bus before any privileged machine changes.
            run(['systemctl', '--user', 'show-environment'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with provisioning_lock(home):
            previous = checked(home, '.local/state/workstation-rebuild/receipt.json')
            wanted_mode = 'headless' if args.headless else 'graphical-login'
            if previous.exists() and json.loads(previous.read_text()).get('desktop_mode') != wanted_mode:
                raise RuntimeError('Desktop mode differs from the successful previous setup; do not run two desktop modes for one user')
            provision(manifest, home, args.headless)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError, tarfile.TarError, KeyError) as exc:
        print(f'\nSETUP FAILED: {exc}\nNo success receipt created for this attempt. Fix the reported error and rerun the same command.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
