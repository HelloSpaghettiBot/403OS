import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'services/profiles.py'
spec = importlib.util.spec_from_file_location('surface_profiles', SOURCE)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)
PASSWORD = 'fixture-only-password-123'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.data = self.base / 'data'
        self.runtime = self.base / 'run'
        self.data.mkdir(mode=0o755)
        self.runtime.mkdir(mode=0o755)
        self.store = profiles.Store(self.data, self.runtime / 'session.json')

    def tearDown(self):
        self.temporary.cleanup()

    def enroll(self):
        return self.store.request(dict(op='enroll-root', password=PASSWORD, confirm=PASSWORD))

    def create(self, name='player'):
        return self.store.request(dict(op='create', name=name, password=PASSWORD, confirm=PASSWORD))

    def test_first_profile_is_literal_root_and_no_plaintext_is_persisted(self):
        status = self.store.request({'op': 'status'})
        self.assertFalse(status['enrolled'])
        status = self.enroll()
        self.assertEqual(status['current']['name'], 'root')
        self.assertEqual(status['current']['uid'], 0)
        self.assertTrue(status['current']['admin'])
        for path in self.base.rglob('*'):
            if path.is_file():
                self.assertNotIn(PASSWORD.encode(), path.read_bytes())
        self.assertEqual(self.store.database.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('password', json.dumps(status))
        with self.assertRaises(profiles.Refused):
            self.enroll()

    def test_salted_hashes_differ_and_compare_password_correctly(self):
        first = profiles.password_hash(PASSWORD)
        second = profiles.password_hash(PASSWORD)
        self.assertNotEqual(first['salt'], second['salt'])
        self.assertNotEqual(first['digest'], second['digest'])
        self.assertTrue(profiles.password_matches(PASSWORD, first))
        self.assertFalse(profiles.password_matches('different-password', first))
        with self.assertRaises(profiles.Refused):
            profiles.password_matches(PASSWORD, dict(first, iterations=1))

    def test_password_confirmation_and_bounds(self):
        for password in ('short', 'x' * 129, 'nul\x00password'):
            with self.assertRaises(profiles.Refused):
                self.store.request(dict(op='enroll-root', password=password, confirm=password))
        with self.assertRaises(profiles.Refused):
            self.store.request(dict(op='enroll-root', password=PASSWORD, confirm='not-the-same'))
        self.assertFalse(self.store.request({'op': 'status'})['enrolled'])

    def test_wrong_password_refused_bounded_backoff_and_success(self):
        self.enroll()
        self.store.request({'op': 'logout'})
        for index in range(10):
            with patch.object(profiles.time, 'time', return_value=1000 + index * 100):
                with self.assertRaisesRegex(profiles.Refused, 'Incorrect password'):
                    self.store.request(dict(op='login', name='root', password='incorrect-password'))
                db = self.store.load()
                self.assertLessEqual(db['attempts']['root']['until'] - profiles.time.time(), 30)
                self.assertIsNone(self.store.current(db))
        with patch.object(profiles.time, 'time', return_value=1900):
            with self.assertRaisesRegex(profiles.Refused, 'Too many attempts'):
                self.store.request(dict(op='login', name='root', password=PASSWORD))
        with patch.object(profiles.time, 'time', return_value=1931):
            self.assertTrue(self.store.request(dict(op='login', name='root', password=PASSWORD))['authenticated'])

    def test_failed_login_clears_an_existing_session(self):
        self.enroll()
        with self.assertRaises(profiles.Refused):
            self.store.request(dict(op='login', name='root', password='incorrect-password'))
        self.assertFalse(self.store.request({'op': 'status'})['authenticated'])

    def test_only_root_can_create_distinct_unix_profiles_and_homes(self):
        with self.assertRaisesRegex(profiles.Refused, 'root administrator'):
            self.create()
        self.enroll()
        first = self.create()
        self.create('second')
        self.assertEqual([row['uid'] for row in first['profiles']], [0, 2000])
        self.store.request({'op': 'logout'})
        current = self.store.request(dict(op='login', name='player', password=PASSWORD))['current']
        self.assertEqual((current['uid'], current['gid']), (2000, 2000))
        self.assertFalse(current['admin'])
        self.assertEqual(current['home'], str(self.data / 'profiles/player/home'))
        self.assertEqual(current['state'], str(self.data / 'profiles/player/state'))
        with self.assertRaisesRegex(profiles.Refused, 'root administrator'):
            self.create('third')

    def test_username_and_request_boundaries(self):
        self.enroll()
        for name in ('root', '../other', 'MixedCase', 'bad:name', '', 'x' * 25):
            with self.assertRaises(profiles.Refused):
                self.create(name)
        for request in ({'op': 'status', 'root': str(self.base)}, {'op': 'execute'}, {'op': 'login', 'name': 'root'}):
            with self.assertRaises(profiles.Refused):
                self.store.request(request)

    def test_logout_and_cold_boot_do_not_restore_authentication(self):
        self.enroll()
        reopened = profiles.Store(self.data, self.store.session)
        self.assertTrue(reopened.request({'op': 'status'})['authenticated'])
        cold = profiles.Store(self.data, self.runtime / 'new-boot-session.json')
        self.assertTrue(cold.request({'op': 'status'})['enrolled'])
        self.assertFalse(cold.request({'op': 'status'})['authenticated'])
        self.assertFalse(reopened.request({'op': 'logout'})['authenticated'])
        self.assertFalse(reopened.request({'op': 'status'})['authenticated'])

    def test_symlink_state_and_database_permissions_rejected(self):
        self.enroll()
        self.store.database.chmod(0o644)
        with self.assertRaises(profiles.Refused):
            self.store.request({'op': 'status'})
        self.store.database.chmod(0o600)
        self.store.database.rename(self.base / 'saved.json')
        self.store.database.symlink_to(self.base / 'saved.json')
        with self.assertRaises(OSError):
            self.store.request({'op': 'status'})

    def test_session_identity_cannot_be_edited_to_gain_root(self):
        self.enroll()
        current = json.loads(self.store.session.read_text())
        current['home'] = '/untrusted'
        profiles.atomic(self.store.session, current)
        with self.assertRaisesRegex(profiles.Refused, 'Invalid authenticated'):
            self.store.request({'op': 'status'})

    def test_status_does_not_change_parent_directory_permissions(self):
        self.enroll()
        self.assertEqual(self.runtime.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o755)

    def test_private_init_umask_preserves_new_profile_parent_traversal(self):
        previous = os.umask(0o077)
        try:
            self.store.request({'op': 'status'})
            profile = self.data / 'profiles' / 'fixture'
            os.close(profiles.directory(profile, mode=0o711))
            home = profile / 'home'
            os.close(profiles.directory(home, owner=(os.getuid(), os.getgid())))
        finally:
            os.umask(previous)
        self.assertEqual(self.store.base.stat().st_mode & 0o777, 0o711)
        self.assertEqual(profile.stat().st_mode & 0o777, 0o711)
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.database.parent.stat().st_mode & 0o022, 0)
        # Checking a pre-existing container is not authorization to chmod it.
        profile.chmod(0o700)
        os.close(profiles.directory(profile, mode=0o711))
        self.assertEqual(profile.stat().st_mode & 0o777, 0o700)

    def test_target_account_reconciliation_locks_passwords_and_refuses_collisions(self):
        root = dict(name='root', uid=0, gid=0, home='/data/home')
        player = dict(name='player', uid=2000, gid=2000, home='/data/profiles/player/home')
        passwd, group, shadow = profiles.account_files('root:x:0:0:root:/root:/bin/sh\n', 'root:x:0:\n', 'root::0:0:99999:7:::\n', [root, player])
        self.assertIn('player:x:2000:2000:403 OS profile:/data/profiles/player/home:/sbin/nologin', passwd)
        self.assertIn('player:x:2000:', group)
        self.assertIn('root:!:', shadow)
        self.assertIn('player:!:', shadow)
        with self.assertRaisesRegex(profiles.Refused, 'conflicts'):
            profiles.account_files(passwd.replace('player:x:2000:', 'other:x:2000:'), group, shadow, [root, player])

    def test_fixture_child_uses_exact_uid_home_and_clean_environment(self):
        home = self.base / 'app-home'
        home.mkdir(mode=0o700)
        identity = dict(name='fixture', uid=os.getuid(), gid=os.getgid(), home=str(home))
        environment = profiles.launch_environment(identity, dict(PATH=os.defpath, LD_PRELOAD='untrusted', PYTHONPATH='/untrusted', SURFACE_PROFILE_PASSWORD='never inherit', API_TOKEN='private-session-token'))
        code = ('import importlib.util,os,sys;'
                f's=importlib.util.spec_from_file_location("profiles",{str(SOURCE)!r});'
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                'm.drop_identity({"uid":os.getuid(),"gid":os.getgid()},os.getgroups());'
                'os.execve(sys.executable,[sys.executable,"-c",\'import os,json;print(json.dumps([os.getuid(),os.geteuid(),os.environ["HOME"],os.getcwd()]))\'],os.environ)')
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=home, env=environment, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [os.getuid(), os.geteuid(), str(home), str(home)])
        self.assertNotIn('LD_PRELOAD', environment)
        self.assertNotIn('PYTHONPATH', environment)
        self.assertNotIn('SURFACE_PROFILE_PASSWORD', environment)
        self.assertNotIn('API_TOKEN', environment)

    @unittest.skipUnless(os.getuid() == os.getgid(), 'socket fixture uses matching UID/GID')
    def test_wayland_socket_hardlink_connects_without_exposing_private_runtime(self):
        source = self.base / 'compositor'
        source.mkdir(mode=0o700)
        runtime = self.base / 'user-runtime'
        server = socket.socket(socket.AF_UNIX)
        client = socket.socket(socket.AF_UNIX)
        try:
            server.bind(str(source / 'wayland-0'))
            server.listen(1)
            profiles.link_wayland(source / 'wayland-0', runtime, os.getgid())
            client.connect(str(runtime / 'wayland-0'))
            accepted, _ = server.accept()
            accepted.close()
            self.assertEqual(source.stat().st_mode & 0o777, 0o700)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertEqual((source / 'wayland-0').stat().st_ino, (runtime / 'wayland-0').stat().st_ino)
        finally:
            client.close()
            server.close()

    @unittest.skipUnless(os.getuid() == os.getgid(), 'runtime fixtures use matching UID/GID')
    def test_socket_revocation_restricts_inode_and_removes_only_known_link(self):
        source = self.base / 'compositor'
        source.mkdir(mode=0o700)
        runtime = self.base / 'user-runtime'
        server = socket.socket(socket.AF_UNIX)
        try:
            server.bind(str(source / 'wayland-0'))
            profiles.link_wayland(source / 'wayland-0', runtime, os.getgid())
            outside = self.base / 'untouched'
            outside.write_text('retain')
            (runtime / 'unrelated').symlink_to(outside)
            profiles.revoke_socket(source / 'wayland-0', runtime, os.getuid(), os.getgid())
            self.assertEqual((source / 'wayland-0').stat().st_mode & 0o777, 0o600)
            self.assertFalse((runtime / 'wayland-0').exists())
            self.assertEqual(outside.read_text(), 'retain')
            self.assertTrue((runtime / 'unrelated').is_symlink())
        finally:
            server.close()

    def test_controller_mapping_snapshot_does_not_follow_existing_user_symlink(self):
        source = self.base / 'trusted-controller-db'
        source.write_text('0300fixture,Fixture pad,a:b0,\n')
        source.chmod(0o600)
        runtime = self.base / 'user-runtime'
        runtime.mkdir(mode=0o700)
        outside = self.base / 'outside'
        outside.write_text('unchanged')
        (runtime / 'gamecontrollerdb.txt').symlink_to(outside)
        result = profiles.copy_controller_mapping(source, runtime, os.getuid(), os.getgid(), os.getuid())
        self.assertEqual(Path(result).read_text(), source.read_text())
        self.assertFalse(Path(result).is_symlink())
        self.assertEqual(Path(result).stat().st_mode & 0o777, 0o600)
        self.assertEqual(outside.read_text(), 'unchanged')
        source.chmod(0o666)
        with self.assertRaises(profiles.Refused):
            profiles.copy_controller_mapping(source, runtime, os.getuid(), os.getgid(), os.getuid())

    def test_process_cleanup_and_account_mutations_guard_host_before_work(self):
        with patch.object(profiles, 'require_guest', side_effect=profiles.Refused('host fixture')):
            with patch.object(profiles.os, 'pidfd_open') as pidfd:
                with self.assertRaises(profiles.Refused):
                    profiles.terminate_profile_processes(2000)
                pidfd.assert_not_called()
            with patch.object(profiles, 'replace_account') as accounts:
                with self.assertRaises(profiles.Refused):
                    profiles.Store(self.data, self.runtime / 'session.json', provision=True).request({'op': 'status'})
                accounts.assert_not_called()
        self.assertTrue(profiles.dedicated_process('Name:\tfixture\nUid:\t2000\t2000\t2000\t2000\n', 2000))
        for uid, fields in ((0, '0 0 0 0'), (1000, '1000 1000 1000 1000'), (2000, '2000 0 2000 2000')):
            self.assertFalse(profiles.dedicated_process('Uid: '+fields, uid))

    def test_host_cli_refuses_before_profile_or_account_changes(self):
        if Path('/etc/surface-image-id').exists():
            self.skipTest('host-guard check must not run inside an image')
        result = subprocess.run([sys.executable, str(SOURCE), '--request'],
                                input=json.dumps(dict(op='enroll-root', password=PASSWORD, confirm=PASSWORD)),
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['event'], 'profile-error')


if __name__ == '__main__':
    unittest.main()
