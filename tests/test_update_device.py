"""Offline update fixtures only: regular files, no host block opens or mounts."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_device', ROOT / 'services/update-device.py')
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)
ARTIFACTS = ROOT / 'artifacts/tests/update-device'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


class OfflineUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'surface-go.img'
        self.target = self.base / 'installed.img'
        self.data = self.base / 'data'
        self.data.mkdir()
        self.updates = self.data / 'updates'
        self.parts = [dict(number=1, name='boot', first=128, last=639, bytes=512 * 512,
                           type='fixture-efi', attributes=4, uuid='fixture-efi-uuid'),
                      dict(number=2, name='root', first=640, last=1663, bytes=1024 * 512,
                           type='fixture-root', attributes=0, uuid='fixture-root-uuid'),
                      dict(number=3, name='data', first=1664, last=4062, bytes=2399 * 512,
                           type='fixture-data', attributes=0, uuid='fixture-data-uuid')]
        self.size = 2 * 1024 * 1024
        old = bytearray(b'G' * self.size)
        new = bytearray(old)
        for part, before, after in zip(self.parts[:2], (b'1', b'2'), (b'A', b'B')):
            offset = part['first'] * 512
            old[offset:offset + part['bytes']] = before * part['bytes']
            new[offset:offset + part['bytes']] = after * part['bytes']
        private = b'PASSWORD HASH / SAVE DATA / GAME ROM / SETTINGS MUST STAY'
        at = self.parts[2]['first'] * 512 + 4096
        old[at:at + len(private)] = private
        new[at:at + len(private)] = private
        self.target.write_bytes(old)
        self.source.write_bytes(new)
        self.old = bytes(old)
        self.new = bytes(new)
        self.gpt = dict(parts=copy.deepcopy(self.parts), primary=dict(backup=self.size // 512 - 1),
                        fingerprint='c' * 64, disk_guid='fixture-guid')
        self.existing = dict(gpt=self.gpt, filesystem=dict(uuid='fixture-data-fs', bytes=self.parts[2]['bytes']),
                             marker='surface-go-old-fixture')
        self.info = dict(device=str(self.target), name='fixture', stable_id='FIXTURE-INTERNAL-001',
                         bytes=self.size, model='Fixture internal disk', devnum='252:16', logical_sector=512,
                         eligible=True, reason='', family_devnums=['252:16', '252:17', '252:18', '252:19'])
        self.source_info = self.info_for_source()
        self.open_writable_count = 0
        self.grow = dict(current_target=lambda *args: dict(target=str(self.target)),
                         open_readonly_target=lambda target: os.open(self.target, os.O_RDONLY),
                         read_gpt=lambda fd, size: copy.deepcopy(self.gpt),
                         read_ext4=lambda fd, part, **kwargs: dict(self.existing['filesystem']))
        self.installer = dict(require_guest=lambda: None, growth_helper=lambda: self.grow,
                              seeding_helper=lambda: dict(mounted_data=lambda: None),
                              source_info=lambda path: dict(self.source_info),
                              current_target=lambda *args: dict(self.info),
                              same_target=self.same_target,
                              boot_partition_devnums=lambda: {'252:1'},
                              open_clone_target=self.open_writable)
        self.patches = [patch.object(update, 'UPDATES', self.updates), patch.object(update, 'DATA', self.data),
                        patch.object(update, 'LOCK', self.base / 'update.lock'),
                        patch.object(update, 'installed_marker', return_value=self.existing['marker'])]
        # Unit fixtures model root ownership without requiring test runners to
        # run as root. Production has no ownership/fixture override or CLI path.
        proxy = SimpleNamespace(**vars(os))
        proxy.fstat = self.root_owned_fixture_stat
        self.patches.append(patch.object(update, 'os', proxy))
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.temp.cleanup()

    def root_owned_fixture_stat(self, fd):
        actual = os.fstat(fd)
        values = {name: getattr(actual, name) for name in dir(actual) if name.startswith('st_')}
        values['st_uid'] = 0
        # The synthetic trusted namespace starts at the private fixture tree.
        # Parent project directory permissions are unrelated to /data on guest.
        if not Path('/proc/self/fd/' + str(fd)).resolve().is_relative_to(self.base):
            values['st_mode'] &= ~0o022
        return SimpleNamespace(**values)

    def info_for_source(self):
        value = self.source.stat()
        return dict(path=str(self.source), bytes=value.st_size, inode=value.st_ino,
                    mtime_ns=value.st_mtime_ns, filesystem_devnum=f'{os.major(value.st_dev)}:{os.minor(value.st_dev)}',
                    sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())

    @staticmethod
    def same_target(before, after):
        if before != after:
            raise ValueError('Target identity or mount state changed')

    def open_writable(self, *args):
        self.open_writable_count += 1
        return os.open(self.target, os.O_RDWR)

    def arguments(self, **changes):
        value = argparse.Namespace(target=str(self.target), source=str(self.source),
                                   expected_target_id=self.info['stable_id'],
                                   expected_source_sha256=self.source_info['sha256'],
                                   confirm='UPDATE ' + self.info['stable_id'], review_token=None, backup=None)
        for key, item in changes.items():
            setattr(value, key, item)
        _, _, _, plan = update.selected_plan(self.installer, value, {'252:1'})
        value.review_token = plan['review_token']
        return value, plan

    def execute(self, args=None):
        args = args or self.arguments()[0]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            update.execute_update(self.installer, args)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def assert_data_and_gpt_untouched(self):
        actual = self.target.read_bytes()
        self.assertEqual(actual[:self.parts[0]['first'] * 512], self.old[:self.parts[0]['first'] * 512])
        at = self.parts[2]['first'] * 512
        self.assertEqual(actual[at:], self.old[at:])  # Every DATA byte and backup GPT.

    def test_update_replaces_only_efi_and_root_and_preserves_all_data_bytes(self):
        events = self.execute()
        self.assertEqual(self.open_writable_count, 1)
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assert_data_and_gpt_untouched()
        complete = events[-1]
        self.assertEqual(complete['event'], 'complete')
        self.assertEqual(complete['operation'], 'update')
        self.assertTrue(complete['verified'])
        self.assertTrue(complete['data_untouched'])
        self.assertTrue(complete['gpt_unchanged'])
        backup = Path(complete['backup'])
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        for path in backup.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        manifest = json.loads((backup / 'manifest.json').read_text())
        for part in manifest['parts']:
            actual = (backup / part['file']).read_bytes()
            self.assertEqual(actual, self.old[part['offset']:part['offset'] + part['bytes']])
            self.assertEqual(hashlib.sha256(actual).hexdigest(), part['sha256'])
        phases = {item['phase'] for item in events if item['event'] == 'progress'}
        self.assertEqual(phases, {'verify-source', 'backup-system', 'verify-backup', 'write-system', 'verify-system'})
        self.assertLess(next(i for i, x in enumerate(events) if x['event'] == 'backup-ready'),
                        next(i for i, x in enumerate(events) if x.get('phase') == 'write-system'))

    def test_source_checksum_failure_never_opens_target_writable(self):
        args, _ = self.arguments()
        self.source_info['sha256'] = '0' * 64
        args.expected_source_sha256 = '0' * 64
        _, _, _, plan = update.selected_plan(self.installer, args, {'252:1'})
        args.review_token = plan['review_token']
        with self.assertRaisesRegex(update.Refused, 'SHA256 verification failed'), contextlib.redirect_stdout(io.StringIO()):
            update.execute_update(self.installer, args)
        self.assertEqual(self.open_writable_count, 0)
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertFalse(self.updates.exists())

    def test_source_changed_after_review_is_rejected_before_writable_open(self):
        args, _ = self.arguments()
        self.source.write_bytes(self.new[:-1])
        with self.assertRaisesRegex(update.Refused, 'source changed'):
            self.execute(args)
        self.assertEqual(self.open_writable_count, 0)

    def test_review_binds_geometry_checksum_and_exact_confirmation(self):
        args, plan = self.arguments()
        self.assertTrue(plan['data_preserved'])
        self.assertEqual(plan['confirmation'], 'UPDATE FIXTURE-INTERNAL-001')
        self.assertEqual([x['number'] for x in plan['partitions']], [1, 2])
        self.assertEqual(plan['backup_bytes'], 768 * 1024)
        self.assertFalse(plan['automatic_restart'])
        args.confirm = 'ERASE FIXTURE-INTERNAL-001'
        with self.assertRaisesRegex(update.Refused, 'Exact typed UPDATE'):
            self.execute(args)
        args.confirm = plan['confirmation']
        args.review_token = '0' * 64
        with self.assertRaisesRegex(update.Refused, 'changed since review'):
            self.execute(args)
        self.assertEqual(self.open_writable_count, 0)

    def test_changed_partition_geometry_is_not_an_in_place_update(self):
        source = copy.deepcopy(self.gpt)
        source['parts'][1]['first'] += 1
        with self.assertRaisesRegex(update.Refused, 'geometry changed'):
            update.plan_update(self.info, self.source_info, self.existing, source)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_source_on_target_and_ineligible_target_refused(self):
        source = dict(self.source_info, filesystem_devnum='252:18')
        with self.assertRaisesRegex(update.Refused, 'stored on the target'):
            update.plan_update(self.info, source, self.existing, self.gpt)
        target = dict(self.info, eligible=False, reason='mounted boot filesystem')
        with self.assertRaisesRegex(update.Refused, 'mounted boot filesystem'):
            update.plan_update(target, self.source_info, self.existing, self.gpt)

    def test_pending_data_journal_allowed_through_execution_without_changing_data(self):
        self.existing['filesystem']['journal_recovery_pending'] = True
        calls = []
        def pending(fd, part, **kwargs):
            calls.append(kwargs)
            if not kwargs.get('allow_journal_recovery'):
                raise ValueError('DATA requires journal recovery')
            return dict(self.existing['filesystem'])
        self.grow['read_ext4'] = pending
        self.execute()
        self.assertGreaterEqual(len(calls), 4)
        start = self.parts[2]['first'] * 512
        self.assertEqual(self.target.read_bytes()[start:], self.old[start:])

    def test_wrong_os_and_mounted_or_boot_target_never_get_backup_or_write(self):
        with patch.object(update, 'installed_marker', side_effect=update.Refused('not an existing 403 OS')):
            with self.assertRaisesRegex(update.Refused, 'not an existing 403 OS'):
                self.arguments()
        self.installer['current_target'] = lambda *args: (_ for _ in ()).throw(ValueError('mounted running boot filesystem'))
        with self.assertRaisesRegex(update.Refused, 'mounted running boot'):
            self.arguments()
        self.assertEqual(self.open_writable_count, 0)
        self.assertFalse(self.updates.exists())

    def test_backup_capacity_and_directory_permissions_checked_before_write(self):
        with patch.object(update.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)):
            with self.assertRaisesRegex(update.Refused, 'enough free space'):
                self.arguments()
        self.updates.mkdir(mode=0o755)
        with self.assertRaisesRegex(update.Refused, '0700 permissions'), contextlib.redirect_stdout(io.StringIO()):
            self.execute()
        self.assertEqual(self.open_writable_count, 0)

    def test_backup_symlink_or_shared_writable_directory_refused(self):
        other = self.base / 'elsewhere'
        other.mkdir()
        self.updates.symlink_to(other, target_is_directory=True)
        with self.assertRaises(OSError), contextlib.redirect_stdout(io.StringIO()):
            self.execute()
        self.updates.unlink()
        self.updates.mkdir(mode=0o777)
        self.updates.chmod(0o777)
        with self.assertRaisesRegex(update.Refused, 'without shared write'), contextlib.redirect_stdout(io.StringIO()):
            self.execute()
        self.assertEqual(self.open_writable_count, 0)

    def test_backup_verification_failure_precedes_any_writable_open(self):
        original = update.transfer
        def corrupt(*args, **kwargs):
            actual = original(*args, **kwargs)
            return '0' * 64 if args[3] == 'verify-backup' else actual
        with patch.object(update, 'transfer', side_effect=corrupt):
            with self.assertRaisesRegex(update.Refused, 'backup readback failed'), contextlib.redirect_stdout(io.StringIO()):
                self.execute()
        self.assertEqual(self.open_writable_count, 0)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_source_changed_during_backup_is_rejected_before_writable_open(self):
        original = update.backup_system
        def changed(*args):
            value = original(*args)
            with self.source.open('r+b') as stream:
                stream.seek(self.parts[0]['first'] * 512)
                stream.write(b'changed source')
            return value
        with patch.object(update, 'backup_system', side_effect=changed), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(update.Refused, 'source changed'):
                self.execute()
        self.assertEqual(self.open_writable_count, 0)
        self.assertEqual(self.target.read_bytes(), self.old)

    def alter_source_preserving_metadata(self, part):
        before = self.source.stat()
        with self.source.open('r+b') as stream:
            stream.seek(part['first'] * 512 + 123)
            stream.write(b'CHANGED SYSTEM BYTES')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, before.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)

    def test_source_rewritten_after_backup_with_restored_mtime_cannot_succeed(self):
        original = update.backup_system
        def changed(*args):
            value = original(*args)
            self.alter_source_preserving_metadata(self.parts[1])
            return value
        with patch.object(update, 'backup_system', side_effect=changed), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(update.Refused, 'partition changed after verification'):
                update.execute_update(self.installer, self.arguments()[0])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(any(x['event'] == 'complete' for x in events))
        self.assertEqual(events[-1]['event'], 'recovery')
        self.assertTrue((Path(events[-1]['backup']) / 'manifest.json').is_file())
        self.assert_data_and_gpt_untouched()

    def test_source_changes_during_copy_cannot_replace_pinned_expected_hashes(self):
        original = update.transfer
        changed = False
        def change_after_efi(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if args[3] == 'write-system' and not changed:
                changed = True
                self.alter_source_preserving_metadata(self.parts[1])
            return result
        with patch.object(update, 'transfer', side_effect=change_after_efi), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(update.Refused, 'partition changed after verification'):
                update.execute_update(self.installer, self.arguments()[0])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(any(x['event'] == 'complete' for x in events))
        self.assertEqual(events[-1]['event'], 'recovery')
        self.assert_data_and_gpt_untouched()

    def test_mount_or_identity_change_after_backup_rejected_before_writable_open(self):
        original = update.backup_system
        def changed(*args):
            value = original(*args)
            self.installer['current_target'] = lambda *args: (_ for _ in ()).throw(ValueError('target became mounted'))
            return value
        with patch.object(update, 'backup_system', side_effect=changed), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(update.Refused, 'became mounted'):
                self.execute()
        self.assertEqual(self.open_writable_count, 0)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_updated_system_readback_failure_never_emits_success(self):
        original = update.transfer
        def corrupt(*args, **kwargs):
            value = original(*args, **kwargs)
            return '0' * 64 if args[3] == 'verify-system' else value
        with patch.object(update, 'transfer', side_effect=corrupt), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(update.Refused, 'readback failed'):
                update.execute_update(self.installer, self.arguments()[0])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(any(x['event'] == 'complete' for x in events))
        self.assertEqual(events[-1]['event'], 'recovery')
        self.assert_data_and_gpt_untouched()

    def test_partial_write_keeps_recoverable_backup_and_never_emits_complete(self):
        original = update.transfer
        def fail_on_root(*args, **kwargs):
            if args[3] == 'write-system' and args[1] == self.parts[1]['first'] * 512:
                raise OSError('simulated failed root write')
            return original(*args, **kwargs)
        with patch.object(update, 'transfer', side_effect=fail_on_root), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(OSError, 'failed root write'):
                update.execute_update(self.installer, self.arguments()[0])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(any(x['event'] == 'complete' for x in events))
        self.assertEqual(events[-1]['event'], 'recovery')
        self.assertTrue((Path(events[-1]['backup']) / 'manifest.json').is_file())
        self.assert_data_and_gpt_untouched()

    def test_restore_verified_backup_recovers_corrupt_root_without_marker(self):
        events = self.execute()
        backup = events[-1]['backup']
        with self.target.open('r+b') as stream:
            stream.seek(self.parts[1]['first'] * 512)
            stream.write(b'CORRUPT ROOT AFTER INTERRUPTED UPDATE')
        args = argparse.Namespace(target=str(self.target), expected_target_id=self.info['stable_id'],
                                  backup=backup, confirm='RESTORE ' + self.info['stable_id'])
        with patch.object(update, 'installed_marker', side_effect=AssertionError('Corrupt roots must be restorable')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            update.restore_update(self.installer, args)
        self.assertEqual(self.target.read_bytes(), self.old)
        complete = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(complete['operation'], 'restore')
        self.assertTrue(complete['data_untouched'])

    def test_restore_corrupt_backup_cannot_open_target_writable(self):
        events = self.execute()
        backup = Path(events[-1]['backup'])
        (backup / '2-root.img').write_bytes(b'corrupt')
        args = argparse.Namespace(target=str(self.target), expected_target_id=self.info['stable_id'],
                                  backup=str(backup), confirm='RESTORE ' + self.info['stable_id'])
        before = self.open_writable_count
        with self.assertRaisesRegex(update.Refused, 'size is unsafe'), contextlib.redirect_stdout(io.StringIO()):
            update.restore_update(self.installer, args)
        self.assertEqual(self.open_writable_count, before)

    def test_restore_wrong_disk_and_tampered_manifest_refused(self):
        events = self.execute()
        backup = Path(events[-1]['backup'])
        manifest_path = backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['target_id'] = 'WRONG-DISK'
        manifest_path.write_text(json.dumps(manifest))
        args = argparse.Namespace(target=str(self.target), expected_target_id=self.info['stable_id'],
                                  backup=str(backup), confirm='RESTORE ' + self.info['stable_id'])
        before = self.open_writable_count
        with self.assertRaisesRegex(update.Refused, 'target identity'):
            update.restore_update(self.installer, args)
        self.assertEqual(self.open_writable_count, before)

    def test_restore_backup_checksum_and_owner_permissions_checked_before_write(self):
        events = self.execute()
        backup = Path(events[-1]['backup'])
        root = backup / '2-root.img'
        with root.open('r+b') as stream:
            stream.write(b'BAD')
        args = argparse.Namespace(target=str(self.target), expected_target_id=self.info['stable_id'],
                                  backup=str(backup), confirm='RESTORE ' + self.info['stable_id'])
        before = self.open_writable_count
        with self.assertRaisesRegex(update.Refused, 'checksum verification failed'), contextlib.redirect_stdout(io.StringIO()):
            update.restore_update(self.installer, args)
        root.chmod(0o644)
        with self.assertRaisesRegex(update.Refused, 'permissions or size'), contextlib.redirect_stdout(io.StringIO()):
            update.restore_update(self.installer, args)
        self.assertEqual(self.open_writable_count, before)

    def test_restore_rejects_invalid_manifest_object_and_symlink_backup_file(self):
        events = self.execute()
        backup = Path(events[-1]['backup'])
        manifest = backup / 'manifest.json'
        original = manifest.read_bytes()
        manifest.write_text('[]')
        with self.assertRaisesRegex(update.Refused, 'Unsupported update backup'):
            update.read_backup(backup)
        manifest.write_bytes(original)
        root = backup / '2-root.img'
        root.unlink()
        root.symlink_to(self.source)
        args = argparse.Namespace(target=str(self.target), expected_target_id=self.info['stable_id'],
                                  backup=str(backup), confirm='RESTORE ' + self.info['stable_id'])
        before = self.open_writable_count
        with self.assertRaises(OSError), contextlib.redirect_stdout(io.StringIO()):
            update.restore_update(self.installer, args)
        self.assertEqual(self.open_writable_count, before)

    def test_cli_host_guard_precedes_lock_creation_or_any_update_operation(self):
        installer = dict(self.installer)
        installer['require_guest'] = lambda: (_ for _ in ()).throw(ValueError('Refusing host execution'))
        with patch.object(update, 'installer_helper', return_value=installer), \
             patch.object(update, 'update_lock', side_effect=AssertionError('No lock before host guard')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            for mode in ('--list', '--plan', '--execute', '--restore'):
                self.assertEqual(update.main([mode]), 2)
        self.assertIn('Refusing host execution', output.getvalue())
        self.assertEqual(self.open_writable_count, 0)


if __name__ == '__main__':
    unittest.main()
