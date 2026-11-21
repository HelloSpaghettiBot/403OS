import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_runner', ROOT / 'services/windows-runner.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.windows = self.base / 'windows'
        self.windows.mkdir(mode=0o700)
        self.patch = patch.object(runner, 'WINDOWS', self.windows)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_host_guard_precedes_all_write_and_execution_paths(self):
        with patch.object(runner, 'MARKER', self.base / 'absent'), \
             patch.object(runner.os, 'open', side_effect=AssertionError('No open before host refusal')), \
             patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('No child before host refusal')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(['--setup-proton']), 1)
            self.assertEqual(runner.main(['--probe-wine']), 1)
            for name in ('wine', 'proton'):
                self.assertEqual(runner.main(['--launch-dw3', '--runner', name]), 1)
                self.assertEqual(runner.main(['--launch-exe', '/media/fixture.exe', '--runner', name]), 1)
                self.assertEqual(runner.main(['--register-exe', '/media/fixture.exe', '--title', 'Fixture', '--runner', name]), 1)
        self.assertIn('only inside the booted', output.getvalue())

    def test_low_space_refuses_setup_before_user_or_directory_changes(self):
        with patch.object(runner, 'require_guest'), patch.object(runner.os, 'access', return_value=True), \
             patch.object(runner.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1024})()), \
             patch.object(runner, 'prepare_user', side_effect=AssertionError('No identity or directory change')):
            with self.assertRaisesRegex(runner.Refused, 'No download started'):
                runner.setup_proton()

    def test_launch_never_performs_automatic_proton_setup(self):
        with patch.object(runner, 'require_guest'), patch.object(runner.os, 'access', return_value=True), \
             patch.object(runner, 'prepare_user', side_effect=AssertionError('No launch before explicit setup')):
            with self.assertRaisesRegex(runner.Refused, 'explicit Proton runtime setup first'):
                runner.launch_dw3('proton')

    def test_environment_removes_root_session_and_compatibility_overrides(self):
        with patch.dict(os.environ, {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0',
                   'LD_LIBRARY_PATH': '/unsafe', 'LD_PRELOAD': '/unsafe.so', 'PYTHONPATH': '/unsafe',
                   'SDL_VIDEO_DRIVER': 'wayland', 'SDL_VIDEODRIVER': 'wayland', 'SDL_AUDIO_DRIVER': 'pipewire'}, clear=True):
            env = runner.environment('proton')
        self.assertEqual(env['DISPLAY'], ':0')
        for name in ('WAYLAND_DISPLAY', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH',
                     'SDL_VIDEO_DRIVER', 'SDL_VIDEODRIVER', 'SDL_AUDIO_DRIVER'):
            self.assertNotIn(name, env)
        self.assertEqual(env['PROTON_USE_WINED3D'], '1')
        self.assertEqual(env['UMU_RUNTIME_UPDATE'], '0')
        self.assertEqual(env['WINEPREFIX'], str(self.windows / 'dw3-proton'))
        self.assertNotEqual(env['WINEPREFIX'], runner.environment('wine')['WINEPREFIX'])

    @contextlib.contextmanager
    def mapping_fixture(self):
        source = self.base / 'config/gamecontrollerdb.txt'
        source.parent.mkdir(mode=0o700, exist_ok=True)
        source.write_text('03000000000000000000000000000000,Owner Controller,a:b0,b:b1,platform:Linux,\n')
        source.chmod(0o600)
        # Constants are replaced only by isolated unit fixtures; the CLI has
        # no environment/argument mechanism for changing the trusted source.
        with patch.object(runner, 'SDL_MAPPING_SOURCE', source), \
             patch.object(runner, 'SDL_MAPPING_OWNER', os.getuid()), \
             patch.object(runner, 'require_guest'), patch.object(runner.os, 'geteuid', return_value=0):
            yield source

    def test_controller_copy_uses_only_known_source_and_private_destination(self):
        with self.mapping_fixture() as source:
            original = source.read_bytes()
            decoy = self.base / 'do-not-read'
            decoy.write_text('unrelated private file')
            with patch.dict(os.environ, {'SDL_GAMECONTROLLERCONFIG_FILE': str(decoy)}):
                self.assertTrue(runner.copy_controller_mapping(1000, 1000))
                env = runner.environment('wine')
            target = self.windows / 'gamecontrollerdb.txt'
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((target.stat().st_uid, target.stat().st_gid), (1000, 1000))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(env['SDL_GAMECONTROLLERCONFIG_FILE'], str(target))
            self.assertEqual(list(self.windows.glob('.controller-mapping-*')), [])

    def test_controller_copy_replaces_links_without_writing_their_targets(self):
        with self.mapping_fixture() as source:
            unrelated = self.base / 'preserved-file'
            unrelated.write_text('keep this unrelated file')
            target = self.windows / 'gamecontrollerdb.txt'
            for kind in ('symlink', 'hardlink'):
                with self.subTest(kind=kind):
                    target.unlink(missing_ok=True)
                    if kind == 'symlink':
                        target.symlink_to(unrelated)
                    else:
                        os.link(unrelated, target)
                    self.assertTrue(runner.copy_controller_mapping(1000, 1000))
                    self.assertFalse(target.is_symlink())
                    self.assertEqual(target.read_bytes(), source.read_bytes())
                    self.assertEqual(unrelated.read_text(), 'keep this unrelated file')

    def test_controller_copy_rejects_linked_sources_and_unbounded_or_unsafe_files(self):
        with self.mapping_fixture() as source:
            actual = source.with_name('actual-db')
            source.rename(actual)
            source.symlink_to(actual)
            with self.assertRaises(OSError):
                runner.copy_controller_mapping(1000, 1000)
            source.unlink()
            actual.rename(source)
            alias = self.base / 'config-alias'
            alias.symlink_to(source.parent, target_is_directory=True)
            with patch.object(runner, 'SDL_MAPPING_SOURCE', alias / source.name), self.assertRaises(OSError):
                runner.copy_controller_mapping(1000, 1000)
            source.chmod(0o666)
            with self.assertRaisesRegex(runner.Refused, 'trusted regular file'):
                runner.copy_controller_mapping(1000, 1000)
            source.chmod(0o600)
            with source.open('r+b') as output:
                output.truncate(runner.SDL_MAPPING_LIMIT + 1)
            with self.assertRaisesRegex(runner.Refused, 'bounded trusted regular file'):
                runner.copy_controller_mapping(1000, 1000)
            source.write_bytes(b'bad\0mapping')
            with self.assertRaisesRegex(runner.Refused, 'null bytes'):
                runner.copy_controller_mapping(1000, 1000)
            self.assertFalse((self.windows / 'gamecontrollerdb.txt').exists())

    def test_missing_mapping_removes_stale_copy_and_ignores_inherited_path(self):
        with self.mapping_fixture() as source:
            self.assertTrue(runner.copy_controller_mapping(1000, 1000))
            source.unlink()
            self.assertFalse(runner.copy_controller_mapping(1000, 1000))
            with patch.dict(os.environ, {'SDL_GAMECONTROLLERCONFIG_FILE': '/data/config/gamecontrollerdb.txt'}):
                self.assertNotIn('SDL_GAMECONTROLLERCONFIG_FILE', runner.environment('proton'))
            self.assertFalse((self.windows / 'gamecontrollerdb.txt').exists())

    def test_changed_mapping_preserves_previous_copy_and_cleans_staging(self):
        with self.mapping_fixture() as source:
            target = self.windows / 'gamecontrollerdb.txt'
            target.write_text('previous mapping')
            target.chmod(0o600)
            real_read = os.read
            changed = False
            def read_and_change(descriptor, size):
                nonlocal changed
                payload = real_read(descriptor, size)
                if not changed:
                    changed = True
                    source.write_text('changed while copying\n')
                return payload
            with patch.object(runner.os, 'read', side_effect=read_and_change), self.assertRaisesRegex(runner.Refused, 'changed while being copied'):
                runner.copy_controller_mapping(1000, 1000)
            self.assertEqual(target.read_text(), 'previous mapping')
            self.assertEqual(list(self.windows.glob('.controller-mapping-*')), [])

    def test_controller_copy_precedes_permanent_privilege_drop(self):
        calls = []
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name='gaming')
        with patch.object(runner, 'gaming_account', return_value=account), patch.object(runner, 'private_directory'), \
             patch.object(runner, 'copy_controller_mapping', side_effect=lambda *_: calls.append('copy')), \
             patch.object(runner.os, 'geteuid', side_effect=[0, 1000]), \
             patch.object(runner.os, 'initgroups', side_effect=lambda *_: calls.append('groups')), \
             patch.object(runner.os, 'setgid', side_effect=lambda *_: calls.append('gid')), \
             patch.object(runner.os, 'setuid', side_effect=lambda *_: calls.append('uid')), \
             patch.object(runner.os, 'umask'):
            runner.prepare_user()
        self.assertEqual(calls, ['copy', 'groups', 'gid', 'uid'])

    def test_controller_copy_host_guard_runs_before_any_open(self):
        with patch.object(runner, 'MARKER', self.base / 'absent'), \
             patch.object(runner.os, 'open', side_effect=AssertionError('No open before host guard')):
            with self.assertRaisesRegex(runner.Refused, 'only inside the booted'):
                runner.copy_controller_mapping(1000, 1000)

    def test_private_directory_does_not_follow_symlinks(self):
        target = self.base / 'private'
        runner.private_directory(target, os.getuid(), os.getgid())
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        alias = self.base / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            runner.private_directory(alias, os.getuid(), os.getgid())

    def test_proton_receipt_requires_probe_and_matching_runtime_artifacts(self):
        proton = self.windows / '.local/share/Steam/compatibilitytools.d/UMU-Proton-10.0'
        proton.mkdir(parents=True)
        (proton / 'proton').write_text('synthetic fixture, never executed')
        (proton / 'toolmanifest.vdf').write_text('"manifest" { "require_tool_appid" "1628350" }')
        (proton / 'version').write_text('synthetic-test\n')
        runtime = self.windows / '.local/share/umu/steamrt3'
        runtime.mkdir(parents=True)
        (runtime / '.installed.ok').write_text('ok\n')
        (runtime / 'toolmanifest.vdf').write_text('synthetic')
        (self.windows / 'dw3-proton/pfx').mkdir(parents=True)
        # Proton's Windows prefix lives below compatdata/pfx, independent of
        # whether a UMU release creates a real directory or a pfx -> . alias.
        (self.windows / 'dw3-proton/system.reg').write_text('wrong legacy location')
        state = runner.installed_protons()[0]
        runner.save_receipt(state)
        self.assertIsNone(runner.proton_state())
        (self.windows / 'dw3-proton/pfx/system.reg').write_text('synthetic')
        self.assertTrue(runner.proton_state())
        self.assertFalse(json.loads((self.windows / 'proton-setup.json').read_text())['gameplay_verified'])
        (runtime / '.installed.ok').unlink()
        self.assertIsNone(runner.proton_state())

    def test_descendants_exclude_unrelated_users_and_reused_pids(self):
        records = {10: dict(parent=1, start=100, uid=1000),
                   11: dict(parent=10, start=101, uid=1000),
                   12: dict(parent=11, start=102, uid=1000),
                   13: dict(parent=1, start=103, uid=1000),
                   14: dict(parent=10, start=104, uid=0),
                   15: dict(parent=1, start=999, uid=1000)}
        result = runner.collect_descendants(records, 10, {15: 105})
        self.assertEqual(result, {11: 101, 12: 102})

    def pe(self, path, machine=0x8664):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytearray(512)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<HH', data, 132, machine, 1)
        struct.pack_into('<HHH', data, 148, 240, 2, 0x20b if machine == 0x8664 else 0x10b)
        path.write_bytes(data)
        return path

    def test_pe_architecture_path_and_dll_restrictions(self):
        path = self.pe(self.windows / 'apps/test.exe')
        info = runner.approved_executable(path, 'wine')
        self.assertEqual(info['architecture'], 'AMD64')
        self.assertEqual(info['prefix'], str(self.windows / 'prefixes' / ('wine-' + info['id'])))
        x86 = self.pe(self.windows / 'apps/x86.exe', 0x14c)
        with self.assertRaisesRegex(runner.Refused, 'AMD64'):
            runner.approved_executable(x86, 'wine')
        self.assertEqual(runner.approved_executable(x86, 'proton')['architecture'], 'I386')
        with self.assertRaisesRegex(runner.Refused, 'control characters'):
            runner.approved_executable(str(path) + '\n', 'wine')
        with path.open('r+b') as output:
            output.seek(150)
            output.write(struct.pack('<H', 0x2002))
        with self.assertRaisesRegex(runner.Refused, 'DLL'):
            runner.approved_executable(path, 'wine')

    def test_external_pe_requires_actual_readonly_mount(self):
        media = self.base / 'media'
        path = self.pe(media / 'app.exe')
        with patch.object(runner, 'EXTERNAL_ROOTS', (media,)):
            with self.assertRaisesRegex(runner.Refused, 'mounted read only'):
                runner.approved_executable(path, 'wine', '1 0 8:1 / / rw - ext4 /dev/sda rw')
            record = f'2 1 8:2 / {media} ro,nosuid,nodev,noexec - ext4 /dev/sdb ro'
            self.assertEqual(runner.approved_executable(path, 'wine', record)['architecture'], 'AMD64')

    def test_registry_preserves_other_entries_and_records_literal_paths(self):
        first = self.pe(self.windows / 'apps/first app.exe')
        second = self.pe(self.windows / 'apps/second.exe')
        with patch.object(runner, 'require_guest'), patch.object(runner, 'prepare_user'), contextlib.redirect_stdout(io.StringIO()):
            runner.register_executable(first, 'First application', 'wine')
            runner.register_executable(second, 'Second application', 'proton')
        entries = runner.registered_executables()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['path'], str(first))
        self.assertEqual(entries[1]['runner'], 'proton')
        self.assertEqual((self.windows / 'apps/registry.json').stat().st_mode & 0o777, 0o600)

    def test_generic_launch_preserves_metacharacters_as_one_literal_argument(self):
        path = self.pe(self.windows / 'apps/owner $(never-run); app.exe')
        with patch.object(runner, 'require_guest'), patch.object(runner, 'prepare_user'), \
             patch.object(runner, 'prepare_directories'), patch.object(runner.os, 'access', return_value=True), \
             patch.object(runner, 'supervise', return_value=0) as execute:
            self.assertEqual(runner.launch_executable(path, 'wine'), 0)
        arguments, env, directory = execute.call_args.args
        self.assertEqual(arguments, [str(runner.WINE), str(path)])
        self.assertEqual(directory, path.parent)
        self.assertIn('wine-', env['WINEPREFIX'])
        self.assertNotIn('LD_PRELOAD', env)

    def test_wine_probe_uses_fixed_argv_separate_prefix_timeout_and_actual_exit(self):
        for code in (0, 17):
            with self.subTest(code=code), patch.object(runner, 'require_guest'), patch.object(runner, 'prepare_user'), \
                 patch.object(runner, 'prepare_directories'), patch.object(runner.os, 'access', return_value=True), \
                 patch.dict(os.environ, {'DISPLAY': ':0'}), patch.object(runner, 'supervise', return_value=code) as execute, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(runner.probe_wine(), code)
                arguments, env, directory = execute.call_args.args
                self.assertEqual(arguments, [str(runner.WINE), 'C:\\windows\\system32\\cmd.exe', '/c', 'exit', '0'])
                self.assertEqual(env['WINEPREFIX'], str(self.windows / 'wine-probe'))
                self.assertEqual(directory, self.windows)
                self.assertEqual(execute.call_args.kwargs['timeout'], 60)
                result = json.loads(output.getvalue())
                self.assertEqual(result['exit'], code)
                self.assertEqual(result['success'], code == 0)
                self.assertFalse(result['gameplay_verified'])

    def supervisor_fixture(self, child):
        script = self.base / 'supervisor-fixture.py'
        script.write_text(
            "import importlib.util,os,sys\n"
            f"spec=importlib.util.spec_from_file_location('r',{str(ROOT / 'services/windows-runner.py')!r})\n"
            "r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)\n"
            f"child={child!r}\n"
            f"sys.exit(r.supervise([sys.executable,'-c',child],dict(os.environ),{str(self.base)!r},timeout=10))\n")
        return script

    @unittest.skipUnless(os.getuid() == 1000, 'Disposable child supervision fixtures run only as UID 1000')
    def test_supervisor_cleans_detached_owned_grandchild_without_touching_sibling(self):
        # These are tiny native Python sleep fixtures, never Wine/UMU or a game.
        # Run the supervisor in its own disposable process so a sibling is
        # genuinely outside its ancestry and cannot be mistaken for a child.
        pidfile = self.base / 'grandchild.pid'
        child = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True);open(" + repr(str(pidfile)) + ",'w').write(str(p.pid))"
        script = self.supervisor_fixture(child)
        sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            completed = subprocess.run([sys.executable, str(script)], timeout=6, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIsNone(sibling.poll())
            grandchild = int(pidfile.read_text())
            self.assertFalse(Path(f'/proc/{grandchild}').exists(), 'Detached owned grandchild was not reaped')
        finally:
            sibling.terminate()
            sibling.wait(timeout=3)

    @unittest.skipUnless(os.getuid() == 1000, 'Disposable supervision fixtures require UID 1000')
    def test_home_sigterm_cleans_detached_child_within_kill_deadline(self):
        pidfile = self.base / 'grandchild.pid'
        child = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'],start_new_session=True);open(" + repr(str(pidfile)) + ",'w').write(str(p.pid));time.sleep(30)"
        script = self.supervisor_fixture(child)
        process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 2
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            grandchild = int(pidfile.read_text())
            time.sleep(0.1)  # Allow the disposable child to install its TERM handler.
            started = time.monotonic()
            process.terminate()
            output, error = process.communicate(timeout=4.5)
            self.assertEqual(process.returncode, 128 + signal.SIGTERM, (output, error))
            self.assertLess(time.monotonic() - started, 4.5)
            self.assertFalse(Path(f'/proc/{grandchild}').exists(), 'Detached TERM-resistant child survived HOME close')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


if __name__ == '__main__':
    unittest.main()
