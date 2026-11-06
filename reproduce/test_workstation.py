#!/usr/bin/env python3
"""Offline tests: isolated homes/repos only. No package installs or live browser access."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import setup
from workstation_user import checked, configure, extension_policy
import verify

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'workstation.json').read_text())


class WorkstationTests(unittest.TestCase):
    def test_offline_plan_never_runs_commands(self):
        with patch.object(sys, 'argv', ['setup.py']), patch.object(setup.subprocess, 'run', side_effect=AssertionError('command')), patch.object(setup.subprocess, 'check_output', side_effect=AssertionError('command')), contextlib.redirect_stdout(io.StringIO()) as printed:
            setup.main()
        self.assertIn('OFFLINE PLAN', printed.getvalue())

    def test_manifests(self):
        names = setup.packages(MANIFEST)
        self.assertIn('kali-tools-rfid', names)
        self.assertIn('google-chrome-stable', names)
        self.assertIn('python3-venv', names)
        self.assertIn('gh', MANIFEST['external_packages'])
        self.assertIn('github-cli', {repo['name'] for repo in MANIFEST['repositories']})
        lock = json.loads((ROOT / MANIFEST['node_tools_lock']).read_text())
        deps = json.loads((ROOT / 'node-tools/package.json').read_text())['dependencies']
        self.assertEqual(deps, lock['packages']['']['dependencies'])
        for name, version in deps.items():
            package = lock['packages']['node_modules/' + name]
            self.assertEqual(package['version'], version)
            self.assertTrue(package['integrity'].startswith('sha512-'))
        self.assertNotIn('/home/drew', json.dumps(lock))
        for source in MANIFEST['sources'].values():
            self.assertRegex(source['commit'], r'^[a-f0-9]{40}$')
            if 'patch' in source:
                self.assertTrue((ROOT / source['patch']).is_file())

    def test_policy(self):
        policy = extension_policy(MANIFEST)['ExtensionSettings']
        self.assertEqual(len(policy), 7)
        for item in policy.values():
            self.assertEqual(item['installation_mode'], 'normal_installed')
            self.assertEqual(item['update_url'], 'https://clients2.google.com/service/update2/crx')
        bad = copy.deepcopy(MANIFEST)
        bad['chrome']['extensions'][0]['id'] = '../../outside'
        with self.assertRaises(ValueError):
            extension_policy(bad)

    def test_user_config_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sources = home / '.local/share/workstation-rebuild/sources'
            configure(home, MANIFEST, sources)
            browser = home / '.public-browser/harness/default/chrome-profile'
            state = json.loads((browser / 'Local State').read_text())
            self.assertEqual(state['profile']['info_cache']['Profile 1']['name'], 'Agent')
            self.assertEqual(state['profile']['last_used'], 'Profile 1')
            files = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            for p in home.rglob('*'):
                if p.is_file():
                    self.assertEqual(stat.S_IMODE(p.stat().st_mode) & 0o077, 0)
            configure(home, MANIFEST, sources)
            self.assertEqual(files, {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()})
            # A running browser's private state must not be overwritten on reapply.
            (browser / 'Profile 1/Preferences').write_text('{"private": "do-not-overwrite"}')
            mcp = home / '.pi/agent/mcp.json'
            custom = {'mcpServers': {'other': {'url': 'https://example.com/mcp'}, 'public-browser': {'command': 'custom'}}}
            mcp.write_text(json.dumps(custom))
            configure(home, MANIFEST, sources)
            self.assertEqual(json.loads((browser / 'Profile 1/Preferences').read_text()), {'private': 'do-not-overwrite'})
            self.assertEqual(json.loads(mcp.read_text()), custom)
            self.assertFalse((browser / 'Cookies').exists())
            self.assertFalse((home / '.pi/agent/auth.json').exists())

    def test_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            home.mkdir()
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            (home / '.config').symlink_to(outside)
            with self.assertRaises(ValueError):
                checked(home, '.config/test')
            with self.assertRaises(ValueError):
                checked(home, '../outside')
            self.assertFalse(list(outside.iterdir()))

    def test_user_tool_links_refuse_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            binary = home / 'binary'
            binary.write_text('test')
            setup.link_tool(home, 'example', binary)
            setup.link_tool(home, 'example', binary)
            self.assertTrue((home / '.local/bin/example').is_symlink())
            (home / '.local/bin/conflict').write_text('keep me')
            with self.assertRaises(RuntimeError):
                setup.link_tool(home, 'conflict', binary)
            self.assertEqual((home / '.local/bin/conflict').read_text(), 'keep me')

    def test_provision_modes_without_running_host_commands(self):
        for headless in (False, True):
            with self.subTest(headless=headless), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                commands = []
                def fake_run(args, **kwargs):
                    commands.append(list(map(str, args)))
                def fake_output(args, **kwargs):
                    if args[0] == 'node':
                        return '24.21.0'
                    if args[0] == 'gpg':
                        repo = next(r for r in MANIFEST['repositories'] if r['name'] == Path(args[-1]).stem)
                        return ''.join('pub:::::::::\nfpr:::::::::' + fingerprint + ':\n' for fingerprint in repo['fingerprints'])
                    if args[0] == 'id':
                        return 'test-user'
                    if args[0] == 'dpkg-query':
                        return 'installed\ti3-wm\t4.25\nconfig-files\tremoved\t'
                    raise AssertionError(args)
                def fake_checkout(name, spec, root):
                    return root / name
                with patch.dict(os.environ), patch.object(setup, 'run', side_effect=fake_run), patch.object(setup, 'output', side_effect=fake_output), patch.object(setup, 'checkout', side_effect=fake_checkout), patch.object(setup, 'install_user_tools'), patch.object(setup, 'install_artifacts'), contextlib.redirect_stdout(io.StringIO()):
                    setup.provision(MANIFEST, home, headless)
                receipt = json.loads((home / '.local/state/workstation-rebuild/receipt.json').read_text())
                self.assertEqual(receipt['desktop_mode'], 'headless' if headless else 'graphical-login')
                self.assertEqual(receipt['apt_installed_versions'], {'i3-wm': '4.25'})
                self.assertFalse(receipt['integration_tested'])
                self.assertIn('patches/voxl-workstation.patch', receipt['recipe_sha256'])
                command = ['systemctl', '--user', 'enable', '--now', 'workstation-headless.service']
                self.assertEqual(command in commands, headless)
                self.assertEqual(['sudo', 'systemctl', 'enable', 'lightdm.service'] in commands, not headless)
                self.assertNotIn(['sudo', 'systemctl', 'unmask', 'nginx.service'], commands)

    def test_exclusive_provisioning_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with setup.provisioning_lock(home):
                with self.assertRaises(RuntimeError):
                    with setup.provisioning_lock(home):
                        self.fail('Concurrent setup was allowed')
            with setup.provisioning_lock(home):
                pass

    def test_invalid_profile_does_not_publish_partial_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            manifest = copy.deepcopy(MANIFEST)
            manifest['chrome']['profiles'][1]['directory'] = '../outside'
            with self.assertRaises(ValueError):
                configure(home, manifest, home / '.local/share/workstation-rebuild/sources')
            self.assertFalse((home / '.public-browser/harness/default/chrome-profile').exists())

    def test_failed_checkout_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(setup, 'run', side_effect=RuntimeError('simulated network failure')):
                with self.assertRaises(RuntimeError):
                    setup.checkout('fixture', {'url': 'https://example.invalid/repo', 'commit': 'a' * 40}, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_artifact_rejects_wrong_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            def corrupt_download(args, **kwargs):
                Path(args[-1]).write_bytes(b'wrong artifact')
            with patch.object(setup, 'run', side_effect=corrupt_download):
                with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                    setup.install_artifacts(home, MANIFEST)
            self.assertFalse((home / '.local/bin/lightpanda').exists())
            self.assertEqual(list((home / '.local/share/workstation-rebuild/artifacts').iterdir()), [])

    def test_loopback_listener_parser(self):
        valid = 'LISTEN 0 128 127.0.0.1:6901 0.0.0.0:*\nLISTEN 0 128 [::1]:9222 [::]:*\n'
        self.assertEqual(verify.listeners(valid, (6901, 9222)), {6901: True, 9222: True})
        unsafe = valid + 'LISTEN 0 128 0.0.0.0:9222 0.0.0.0:*\n'
        self.assertFalse(verify.listeners(unsafe, (9222,))[9222])
        self.assertFalse(verify.listeners('', (6901,))[6901])
        self.assertFalse(verify.listeners('LISTEN 0 128 *:6901 *:*', (6901,))[6901])

    def test_runtime_verifier_requires_both_services_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            configure(home, MANIFEST, home / '.local/share/workstation-rebuild/sources')
            credential = home / '.config/voxl/stream.env'
            credential.parent.mkdir(parents=True)
            credential.write_text('VOXL_PASSWORD=' + 'a' * 48 + '\n')
            for extension in MANIFEST['chrome']['extensions']:
                target = home / '.public-browser/harness/default/chrome-profile/Profile 1/Extensions' / extension['id'] / '1.0'
                target.mkdir(parents=True)
                (target / 'manifest.json').write_text('{}')
            def commands(args):
                if args[0] == 'ss':
                    return 'LISTEN 0 128 127.0.0.1:6901 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:9222 0.0.0.0:*'
                return 'active'
            def http(path, password=None):
                return 200 if path == '/healthz' or password else 401
            with patch.object(verify, 'command', side_effect=commands), patch.object(verify, 'cdp_ready', return_value=True), patch.object(verify, 'expected_chrome_process', return_value=True), patch.object(verify, 'http_status', side_effect=http):
                verify.runtime_once(home, MANIFEST)
                with patch.object(verify, 'http_status', return_value=200):
                    with self.assertRaisesRegex(RuntimeError, 'unauthenticated denial'):
                        verify.runtime_once(home, MANIFEST)
                with patch.object(verify, 'command', side_effect=RuntimeError('inactive service')):
                    with self.assertRaisesRegex(RuntimeError, 'inactive service'):
                        verify.runtime_once(home, MANIFEST)

    def test_key_fingerprints(self):
        text = 'pub:::::::::\nfpr:::::::::PRIMARY:\nsub:::::::::\nfpr:::::::::SUBKEY:\n'
        self.assertEqual(setup.primary_fingerprints(text), ['PRIMARY'])

    def test_checkout_patches_and_refuses_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original, clones = tmp / 'upstream', tmp / 'clones'
            original.mkdir()
            clones.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(original), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Offline Test')
            git('config', 'user.email', 'offline@example.invalid')
            (original / 'file.txt').write_text('before\n')
            git('add', 'file.txt')
            git('commit', '-qm', 'fixture')
            commit = git('rev-parse', 'HEAD')
            (original / 'file.txt').write_text('after\n')
            patch_path = tmp / 'fixture.patch'
            patch_path.write_bytes(subprocess.check_output(['git', '-C', str(original), 'diff', '--binary', 'HEAD']))
            git('checkout', '--', 'file.txt')
            spec = {'url': str(original), 'commit': commit, 'patch': str(patch_path)}
            with contextlib.redirect_stdout(io.StringIO()):
                checkout = setup.checkout('fixture', spec, clones)
                self.assertEqual((checkout / 'file.txt').read_text(), 'after\n')
                setup.checkout('fixture', spec, clones)
                (checkout / 'file.txt').write_text('user changes\n')
                with self.assertRaises(RuntimeError):
                    setup.checkout('fixture', spec, clones)
            self.assertEqual((checkout / 'file.txt').read_text(), 'user changes\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
