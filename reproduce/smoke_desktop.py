#!/usr/bin/env python3
"""Opt-in real i3/Xvfb layout smoke test in a fake home. Never launches a browser."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def nodes(node):
    yield node
    for key in ('nodes', 'floating_nodes'):
        for child in node.get(key, []):
            yield from nodes(child)


def inside(home):
    processes = []
    def tree():
        return json.loads(subprocess.check_output(['i3-msg', '-t', 'get_tree'], stderr=subprocess.DEVNULL))
    def wait_for(predicate, description):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                result = predicate(tree())
                if result:
                    return result
            except (subprocess.SubprocessError, ValueError):
                pass
            time.sleep(.2)
        raise RuntimeError('Timeout: ' + description)
    with (home / 'i3.log').open('w') as log:
        try:
            processes.append(subprocess.Popen(['i3', '-c', str(home / '.config/i3/config')],
                                              stdout=log, stderr=log, start_new_session=True))
            wait_for(lambda t: t.get('type') == 'root', 'isolated i3 IPC')
            for cls, name in (('XTerm', 'audit-terminal'), ('Geany', 'audit-editor'), ('Thunar', 'audit-explorer')):
                processes.append(subprocess.Popen(['xterm', '-class', cls, '-name', name],
                                                  stdout=log, stderr=log, start_new_session=True))
            def has_windows(t):
                classes = {(n.get('window_properties', {}).get('class') or '').lower() for n in nodes(t)}
                return {'xterm', 'geany', 'thunar'}.issubset(classes)
            wait_for(has_windows, 'three test windows')
            helper = home / '.local/bin/i3-stream-layout'
            for _ in range(3):
                subprocess.run([str(helper), '--ensure'], check=True, stdout=log, stderr=log)
                time.sleep(.3)
            snapshot = tree()
            workspaces = {n['name']: n for n in nodes(snapshot) if n.get('type') == 'workspace'}
            def classes(n):
                return {(x.get('window_properties', {}).get('class') or '').lower() for x in nodes(n) if x.get('window')}
            assert 'xterm' in classes(workspaces['1:term']), 'terminal routing failed'
            assert {'geany', 'thunar'}.issubset(classes(workspaces['3:files'])), 'file-app routing failed'
            def marked(t, mark):
                return next(n for n in nodes(t) if mark in n.get('marks', []))
            editors, explorer = marked(snapshot, 'files-editors'), marked(snapshot, 'files-explorer')
            assert 'geany' in classes(editors) and 'thunar' in classes(explorer), 'split app placement failed'
            ratio = editors['rect']['width'] / (editors['rect']['width'] + explorer['rect']['width'])
            assert .54 <= ratio <= .66, f'initial split is not approximately 60/40: {ratio}'
            subprocess.run(['i3-msg', '[con_mark="^files-editors$"] resize set width 70 ppt'],
                           check=True, stdout=log, stderr=log)
            before = marked(tree(), 'files-editors')['rect']['width']
            subprocess.run([str(helper), '--ensure'], check=True, stdout=log, stderr=log)
            after = marked(tree(), 'files-editors')['rect']['width']
            assert abs(before - after) <= 2, 'layout watchdog reset a manually resized pane'
            assert not any('placeholder' in mark for n in nodes(tree()) for mark in n.get('marks', [])), 'placeholder survived'
            print('PASS: real isolated Xvfb/i3, terminal/file routing, 60/40 split, app placement, resize persistence and placeholder cleanup')
        finally:
            for process in reversed(processes):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='start a disposable, isolated X server and test windows')
    parser.add_argument('--inside', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        if Path.home() != args.inside or os.environ.get('WORKSTATION_ISOLATED_DESKTOP_TEST') != 'YES':
            parser.error('internal invocation requires an isolated fake home')
        inside(args.inside)
        return
    if not args.run:
        parser.print_help()
        return
    for program in ('Xvfb', 'xvfb-run', 'xauth', 'dbus-run-session', 'i3', 'i3-msg', 'xterm'):
        if not shutil.which(program):
            parser.error('Missing smoke-test prerequisite: ' + program)
    with tempfile.TemporaryDirectory(prefix='workstation-desktop-smoke-') as tmp:
        home = Path(tmp)
        spec = importlib.util.spec_from_file_location('desktop_installer', ROOT / 'install-desktop.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installer.install(home, apply=True)
        from workstation_user import configure
        configure(home, json.loads((ROOT / 'workstation.json').read_text()), home / '.local/share/workstation-rebuild/sources')
        runtime = home / 'runtime'
        runtime.mkdir(mode=0o700)
        unit_dir = home / 'verify-units'
        unit_dir.mkdir(mode=0o700)
        for name in ('voxl-stream.service', 'workstation-headless.service'):
            text = (home / '.config/systemd/user' / name).read_text().replace('%h', str(home))
            (unit_dir / name).write_text(text)
        if shutil.which('systemd-analyze'):
            subprocess.run(['systemd-analyze', '--user', '--man=no', 'verify',
                            str(unit_dir / 'voxl-stream.service'), str(unit_dir / 'workstation-headless.service')],
                           env={'PATH': '/usr/bin:/bin', 'HOME': tmp, 'XDG_RUNTIME_DIR': str(runtime),
                                'SYSTEMD_UNIT_PATH': str(unit_dir) + ':/usr/lib/systemd/user:/lib/systemd/user'}, check=True)
            print('PASS: both generated systemd user units validate with the isolated home paths', flush=True)
        config = home / '.config/i3/config'
        # Keep routing/layout/window rules intact, but never start the source's
        # audio, browser, streaming service, bar or session-manager imports.
        config.write_text('\n'.join(line for line in config.read_text().splitlines()
                                    if not line.startswith(('exec ', 'exec_always '))) + '\n')
        # Do not pass provider tokens or source desktop/session variables to test clients.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SHELL': '/bin/sh'}
        env.update(HOME=tmp, XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'),
                   XDG_CACHE_HOME=str(home / '.cache'), XDG_RUNTIME_DIR=str(runtime),
                   WORKSTATION_ISOLATED_DESKTOP_TEST='YES')
        result = subprocess.run(['dbus-run-session', '--', 'xvfb-run', '-a', '-s',
                                 '-screen 0 1440x900x24 -nolisten tcp', sys.executable, '-B', str(Path(__file__).resolve()),
                                 '--inside', tmp], env=env)
        if result.returncode:
            print('Isolated i3 diagnostic log (no browser/credentials were loaded):')
            print((home / 'i3.log').read_text()[-16000:])
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
