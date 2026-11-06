#!/usr/bin/env python3
"""Generate portable, credential-free user configuration. Never read source profiles."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone


def extension_policy(manifest):
    ids = [e['id'] for e in manifest['chrome']['extensions']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch('[a-p]{32}', x) for x in ids):
        raise ValueError('Invalid/duplicate Chrome extension ID')
    # normal_installed installs automatically but allows the user to disable it.
    # A Linux machine policy necessarily affects every Chrome profile.
    return {'ExtensionSettings': {x: {
        'installation_mode': 'normal_installed',
        'update_url': 'https://clients2.google.com/service/update2/crx',
    } for x in ids}}


def checked(home, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError(f'Unsafe relative path: {relative}')
    dst = home / rel
    for parent in [dst, *dst.parents]:
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f'Refusing symlink: {parent}')
    if not dst.resolve().is_relative_to(home.resolve()):
        raise ValueError(f'Outside home: {dst}')
    return dst


def configure(home, manifest, source_root):
    home = home.resolve()
    backup = '.local/state/workstation-rebuild/backups/' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

    def put(relative, text, mode=0o600):
        dst = checked(home, relative)
        if dst.exists() and dst.read_text() == text:
            return
        if dst.exists():
            saved = checked(home, str(Path(backup) / relative))
            saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(dst, saved)
            saved.chmod(0o600)
        dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Replace rather than truncate, so a pre-existing hardlink isn't modified.
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.workstation-new-',
                                             dir=dst.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(text)
            temp.chmod(mode)
            temp.replace(dst)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def put_json(relative, data):
        put(relative, json.dumps(data, indent=2) + '\n')

    browser_rel = '.public-browser/harness/default/chrome-profile'
    browser = checked(home, browser_rel)
    # Only seed a completely new root. Never alter a live/existing Chrome database.
    if not browser.exists():
        cache, seeds = {}, {}
        for profile in manifest['chrome']['profiles']:
            directory, name = profile['directory'], profile['name']
            if not re.fullmatch(r'Default|Profile [0-9]+', directory) or directory in cache:
                raise ValueError('Invalid/duplicate Chrome profile directory')
            cache[directory] = {'name': name, 'is_using_default_name': False}
            seeds[f'{directory}/Preferences'] = {'profile': {
                'name': name, 'is_using_default_name': False,
            }}
        if cache.get('Profile 1', {}).get('name') != 'Agent':
            raise ValueError('Harness requires Profile 1 named Agent')
        seeds['Local State'] = {'profile': {
            'info_cache': cache, 'last_used': 'Profile 1', 'last_active_profiles': ['Profile 1'],
        }}
        browser.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Publish only a complete seed, so interruptions cannot leave a half-profile.
        with tempfile.TemporaryDirectory(prefix='.profile-seed-', dir=browser.parent) as staging:
            seed = Path(staging) / 'profile'
            seed.mkdir(mode=0o700)
            for relative, data in seeds.items():
                dst = seed / relative
                dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                dst.write_text(json.dumps(data, indent=2) + '\n')
                dst.chmod(0o600)
            (seed / 'First Run').touch(mode=0o600)
            seed.rename(browser)

    pb = source_root / 'public-browser'
    # Never overwrite a hand-edited MCP config or import source credentials.
    mcp_rel = '.pi/agent/mcp.json'
    mcp_path = checked(home, mcp_rel)
    config = json.loads(mcp_path.read_text()) if mcp_path.exists() else {}
    servers = config.setdefault('mcpServers', {})
    servers.setdefault('public-browser', {
        'command': '/usr/bin/node',
        'args': [str(pb / 'build/index.js'), '--attach', '--host', '127.0.0.1', '--port', '9222', '--agent-id', 'auto'],
        'exposure': 'codemode',
        'env': {'PUBLIC_BROWSER_DOWNLOAD_DIR': str(home / 'Downloads')},
    })
    put_json(mcp_rel, config)
    # Local overrides are deliberately git-ignored by the upstream harness.
    local = pb / 'harness/profiles.local.json'
    expected = {'ownerUid': os.getuid(), 'stateDir': str(home / '.public-browser/harness'),
                'profiles': {'default': {
                    'userDataDir': str(browser), 'profileDirectory': 'Profile 1',
                    'expectedProfileName': 'Agent', 'downloadDir': str(home / 'Downloads'),
                    'headless': False, 'startupUrl': 'about:blank', 'restoreLastSession': True,
                    'extraArgs': ['--no-portal'],
                }}}
    # Write through the same checked/backup helper as all other generated files.
    put_json(str(local.relative_to(home)), expected)
    checked(home, 'Downloads').mkdir(mode=0o700, exist_ok=True)
    put('.local/bin/workstation-headless', (Path(__file__).resolve().parent / 'headless-desktop.sh').read_text(), 0o700)
    put('.config/systemd/user/workstation-headless.service', '''[Unit]
Description=Private Xvfb i3 workstation desktop

[Service]
Type=simple
ExecStart=%h/.local/bin/workstation-headless
Restart=on-failure
RestartSec=5
KillMode=control-group
Environment=VOXL_SCREEN_SIZE=1920x1080x24
UnsetEnvironment=DISPLAY XAUTHORITY

[Install]
WantedBy=default.target
''')
    put('.dmrc', '[Desktop]\nSession=i3\n')
    put('.config/workstation-rebuild/enable-browser', '')
    put('.local/bin/open-chrome-harness', '#!/usr/bin/env bash\nset -euo pipefail\nexec node '
        + '"$HOME/.local/share/workstation-rebuild/sources/public-browser/harness/harness.mjs" up default\n', 0o700)
    put('.local/bin/pb-harness', '#!/usr/bin/env bash\nset -euo pipefail\nexec node '
        + '"$HOME/.local/share/workstation-rebuild/sources/public-browser/harness/harness.mjs" "$@"\n', 0o700)
    put_json('.local/state/workstation-rebuild/remaining-gaps.json', manifest['remaining_gaps'])
