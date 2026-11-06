#!/usr/bin/env python3
"""Target-side acceptance checks. No provisioning, browser navigation, or secret output."""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from workstation_user import checked, extension_policy

ROOT = Path(__file__).resolve().parent


def command(args, accepted_codes=(0,)):
    result = subprocess.run(list(map(str, args)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=30)
    if result.returncode not in accepted_codes:
        raise RuntimeError(f'{Path(str(args[0])).name}: check exited {result.returncode}')
    return result.stdout.strip()


def listeners(text, ports):
    found = {port: [] for port in ports}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        address, _, port = parts[3].rpartition(':')
        if not port.isdigit() or int(port) not in found:
            continue
        address = address.strip('[]')
        try:
            local = ipaddress.ip_address(address).is_loopback
        except ValueError:
            local = False
        found[int(port)].append(local)
    return {port: bool(values) and all(values) for port, values in found.items()}


def http_status(path, password=None):
    # Direct loopback only, no environment proxies, redirects or cookie persistence.
    connection = http.client.HTTPConnection('127.0.0.1', 6901, timeout=3)
    try:
        headers = {'Authorization': 'Bearer ' + password} if password else {}
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        return response.status
    finally:
        connection.close()


def cdp_ready():
    connection = http.client.HTTPConnection('127.0.0.1', 9222, timeout=3)
    try:
        connection.request('GET', '/json/version')
        response = connection.getresponse()
        if response.status != 200:
            return False
        data = json.loads(response.read(65536))
        return bool(data.get('Browser') and data.get('webSocketDebuggerUrl', '').startswith('ws://127.0.0.1:9222/'))
    finally:
        connection.close()


def expected_chrome_process(browser):
    wanted = {'--user-data-dir=' + str(browser), '--profile-directory=Profile 1'}
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            args = path.read_bytes().decode().split('\0')
            if args[0].endswith('/chrome') and wanted.issubset(args) and not any(a.startswith('--type=') for a in args):
                return True
        except (OSError, UnicodeError):
            continue
    return False


def inspect_profiles(home, manifest):
    root = checked(home, '.public-browser/harness/default/chrome-profile')
    local = checked(home, str((root / 'Local State').relative_to(home)))
    state = json.loads(local.read_text())
    cache = state.get('profile', {}).get('info_cache', {})
    for item in manifest['chrome']['profiles']:
        if cache.get(item['directory'], {}).get('name') != item['name']:
            raise RuntimeError('Chrome profile definition does not match the recipe')
    return root


def static_checks(home, manifest):
    checks = []
    def passed(name):
        checks.append(name)
        print('PASS: ' + name, flush=True)
    import setup
    installed = {}
    for line in command(['dpkg-query', '-W', '-f=${db:Status-Status}\\t${binary:Package}\\t${Version}\\n']).splitlines():
        fields = line.split('\t')
        if fields[0] == 'installed':
            installed[fields[1]] = fields[2]
    missing = [name for name in setup.packages(manifest) if name not in installed]
    if missing:
        raise RuntimeError('Missing APT applications: ' + ', '.join(missing))
    passed('all requested APT packages installed')
    tools = json.loads((ROOT / 'node-tools/package.json').read_text())['dependencies']
    expected = {'pi': tools['@earendil-works/pi-coding-agent'], 'bw': tools['@bitwarden/cli'],
                'opencode': tools['@opencode/cli'], 'bun': tools['bun']}
    for name, version in expected.items():
        actual = command([home / '.local/bin' / name, '--version'])
        if actual not in (version, 'v' + version, 'opencode v' + version):
            raise RuntimeError(f'{name}: installed version differs from lock')
    for name in ('uv', 'uvx'):
        if not command([home / '.local/bin' / name, '--version']).startswith(name + ' 0.13.7 '):
            raise RuntimeError(f'{name}: installed version differs from wheel pin')
    for artifact in manifest.get('artifacts', []):
        path = home / '.local/bin' / artifact['name']
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.get('binary_sha256', artifact['sha256']):
            raise RuntimeError('Pinned artifact checksum does not match: ' + artifact['name'])
        if command([path, *artifact['version_args']]) != artifact.get('version_output', artifact['version']):
            raise RuntimeError('Pinned artifact version does not match: ' + artifact['name'])
    passed('locked CLI versions and binary artifact hashes')
    for filename in ('package.json', 'package-lock.json'):
        installed_lock = checked(home, '.local/share/workstation-rebuild/node-tools/' + filename)
        if installed_lock.read_bytes() != (ROOT / 'node-tools' / filename).read_bytes():
            raise RuntimeError('Installed Node tool inputs differ from the recipe')
    for name, spec in manifest['sources'].items():
        source = checked(home, '.local/share/workstation-rebuild/sources/' + name)
        if command(['git', '-C', source, 'rev-parse', 'HEAD']) != spec['commit']:
            raise RuntimeError('Unexpected source commit: ' + name)
        diff = subprocess.check_output(['git', '-C', str(source), 'diff', '--binary', 'HEAD'])
        expected_diff = (ROOT / spec['patch']).read_bytes() if spec.get('patch') else b''
        if diff != expected_diff:
            raise RuntimeError('Unexpected source modifications: ' + name)
    passed('source commits, runtime patch, and installed Node locks')
    editor = command(['codium', '--user-data-dir', home / '.config/VSCodium',
                      '--extensions-dir', home / '.vscode-oss/extensions', '--list-extensions', '--show-versions'])
    if not set(s.lower() for s in manifest['editor_extensions']).issubset(set(editor.lower().splitlines())):
        raise RuntimeError('VSCodium extension versions do not match the recipe')
    passed('both pinned VSCodium extensions installed')
    command(['i3', '-C', '-c', home / '.config/i3/config'])
    passed('installed i3 configuration validates')
    inspect_profiles(home, manifest)
    policy = json.loads(Path('/etc/opt/chrome/policies/managed/workstation-extensions.json').read_text())
    if policy != extension_policy(manifest):
        raise RuntimeError('Installed Chrome machine policy differs from the recipe')
    passed('profile definitions and Chrome machine policy')
    secret = checked(home, '.config/voxl/stream.env')
    info = secret.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
        raise RuntimeError('Voxl credential ownership/type/permissions are unsafe')
    if not re.fullmatch(r'VOXL_PASSWORD=[a-f0-9]{48}\n', secret.read_text()):
        raise RuntimeError('Voxl credential format is invalid (not printed)')
    if not checked(home, '.config/voxl/enable-stream').is_file():
        raise RuntimeError('Voxl stream enable marker is missing')
    passed('private target-generated authentication and stream enable marker')
    if command(['systemctl', 'is-enabled', 'nginx.service'], accepted_codes=(0, 1)) != 'masked':
        raise RuntimeError('Nginx must remain masked until HTTPS is deliberately configured')
    passed('Nginx remains masked')
    return checks


def runtime_once(home, manifest):
    for service in ('workstation-headless.service', 'voxl-stream.service'):
        if command(['systemctl', '--user', 'is-active', service]) != 'active':
            raise RuntimeError('Required service is not active: ' + service)
    if not all(listeners(command(['ss', '-H', '-ltn']), (6901, 9222)).values()):
        raise RuntimeError('Voxl/CDP listeners are missing or not exclusively loopback')
    browser = inspect_profiles(home, manifest)
    if not cdp_ready() or not expected_chrome_process(browser):
        raise RuntimeError('CDP is not healthy in the expected user-owned Agent browser process')
    for extension in manifest['chrome']['extensions']:
        parent = checked(home, str((browser / 'Profile 1/Extensions' / extension['id']).relative_to(home)))
        if not any(parent.glob('*/manifest.json')):
            raise RuntimeError('Chrome extension has not downloaded: ' + extension['name'])
    if http_status('/healthz') != 200:
        raise RuntimeError('Voxl health endpoint is not ready')
    for path in ('/', '/files/list', '/files/upload', '/screenshot'):
        if http_status(path) != 401:
            raise RuntimeError('Voxl failed an unauthenticated denial check')
    secret = checked(home, '.config/voxl/stream.env').read_text().strip().split('=', 1)[1]
    if http_status('/', secret) != 200:
        raise RuntimeError('Voxl rejected the target-generated password (not printed)')


def runtime_checks(home, manifest, wait):
    deadline = time.monotonic() + wait
    while True:
        try:
            runtime_once(home, manifest)
            print('PASS: active headless/stream services, Agent CDP, seven downloaded extensions, loopback listeners and Voxl authentication', flush=True)
            return ['headless runtime readiness and authentication']
        except (RuntimeError, OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Runtime acceptance check failed: {error}') from None
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', action='store_true', help='also check the running headless desktop and local services')
    parser.add_argument('--wait', type=int, default=120, help='runtime startup/download grace period in seconds')
    args = parser.parse_args()
    home = Path.home().resolve()
    if not checked(home, '.config/workstation-rebuild/enable-browser').is_file():
        parser.error('This is a target-side verifier; no generated workstation configuration found')
    manifest = json.loads((ROOT / 'workstation.json').read_text())
    try:
        static_checks(home, manifest)
        if args.runtime:
            runtime_checks(home, manifest, max(0, args.wait))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('FAIL: ' + str(error))
        raise SystemExit(1)
    print('Acceptance checks passed; this does not validate reboot, input/audio, extension functionality or a large upload.')


if __name__ == '__main__':
    main()
