"""Power fixtures never write host sysfs, sleep the host or read its input."""
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('surface_power', ROOT / 'services/power.py')
power = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(power)


class PowerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / 'state'
        self.state.write_text('freeze mem disk\n')
        mem_sleep = self.base / 'mem_sleep'
        mem_sleep.write_text('[s2idle] deep\n')
        for name, value in (('STATE', self.state),
                            ('MEM_SLEEP', mem_sleep),
                            ('LOCKS', (self.base / 'library.lock', self.base / 'update.lock'))):
            patcher = patch.object(power, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_sleep_diagnostics_measure_suspended_time_without_changing_policy(self):
        with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                patch.object(power.os, 'sync'), patch.object(power.time, 'clock_gettime', side_effect=[102.0,112.0]), \
                patch.object(power.time, 'monotonic', side_effect=[100.0,105.0]), \
                patch('builtins.print') as output:
            self.assertEqual(power.suspend(), 'mem')
        import json
        events = [json.loads(call.args[0].removeprefix('SURFACE_POWER ')) for call in output.call_args_list]
        self.assertEqual([event['event'] for event in events], ['enter','resumed'])
        self.assertEqual(events[0]['id'], events[1]['id'])
        self.assertEqual(events[1]['sleep_seconds'], 5.0)
        self.assertEqual(events[1]['policy'], 's2idle')
        self.assertEqual(power.MEM_SLEEP.read_text(), '[s2idle] deep\n')

    def root_fstat(self, fd):
        info = os.stat(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)

    def test_suspend_prefers_mem_and_syncs_without_forcing_deep(self):
        with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                patch.object(power.os, 'sync') as sync:
            self.assertEqual(power.suspend(), 'mem')
        sync.assert_called_once_with()
        self.assertTrue(self.state.read_text().startswith('mem\n'))

    def test_freeze_fallback_and_no_supported_state_refuses(self):
        self.state.write_text('freeze\n')
        with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                patch.object(power.os, 'sync') as sync:
            self.assertEqual(power.suspend(), 'freeze')
            sync.assert_called_once()
            self.state.write_text('disk\n')
            sync.reset_mock()
            with self.assertRaisesRegex(power.PowerError, 'supported suspend'):
                power.suspend()
            sync.assert_not_called()

    def test_each_exclusive_storage_operation_blocks_suspend_without_sync_or_write(self):
        for lock in power.LOCKS:
            with self.subTest(lock=lock):
                fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                            patch.object(power.os, 'sync') as sync:
                        with self.assertRaisesRegex(power.PowerError, 'deferred'):
                            power.suspend()
                        sync.assert_not_called()
                    self.assertEqual(self.state.read_text(), 'freeze mem disk\n')
                finally:
                    os.close(fd)

    def test_shared_storage_locks_are_held_across_suspend_write(self):
        original_write = os.write

        def verify_write(fd, content):
            for lock in power.LOCKS:
                check = os.open(lock, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(check)
            return original_write(fd, content)

        with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                patch.object(power.os, 'sync'), patch.object(power.os, 'write', verify_write):
            power.suspend()

    def test_lock_and_state_symlinks_are_refused(self):
        target = self.base / 'target'
        target.write_text('mem\n')
        power.LOCKS[0].symlink_to(target)
        with self.assertRaises(OSError):
            with power.storage_idle():
                self.fail('Symlink lock accepted')
        power.LOCKS[0].unlink()
        self.state.unlink()
        self.state.symlink_to(target)
        with patch.object(power, 'guard'), patch.object(power.os, 'fstat', self.root_fstat), \
                patch.object(power.os, 'sync') as sync:
            with self.assertRaises(OSError):
                power.suspend()
            sync.assert_not_called()

    def test_guard_requires_root_trusted_image_and_actual_data_mount(self):
        marker = self.base / 'marker'
        marker.write_text('surface-go-fixture\n')
        mounts = self.base / 'mounts'
        mounts.write_text('/dev/fixture /data ext4 rw 0 0\n')
        with patch.object(power, 'MARKER', marker), patch.object(power, 'MOUNTS', mounts), \
                patch.object(power.os, 'geteuid', return_value=0), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)), \
                patch.object(power.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(power.PowerError, 'DATA'):
                power.guard()
        with patch.object(power, 'MARKER', marker), patch.object(power.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(power.PowerError, 'privileged'):
                power.guard()

    def test_evdev_capability_check_accepts_power_but_never_grabs_device(self):
        requests = []

        def ioctl(fd, request, data, *extra):
            requests.append(request)
            if isinstance(data, bytearray):
                data[116 // 8] |= 1 << (116 % 8)

        with patch.object(power.os, 'open', return_value=55), \
                patch.object(power.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_uid=0)), \
                patch.object(power.fcntl, 'ioctl', ioctl), patch.object(power.os, 'close') as close:
            self.assertEqual(power.open_button(Path('/synthetic/event0')), 55)
            close.assert_not_called()
        self.assertEqual(len(requests), 2)
        self.assertFalse(any((request & 0xff) == 0x90 for request in requests))  # EVIOCGRAB

    def test_regular_file_and_non_power_input_are_refused_and_closed(self):
        for mode in (stat.S_IFREG, stat.S_IFCHR):
            with self.subTest(mode=mode), patch.object(power.os, 'open', return_value=55), \
                    patch.object(power.os, 'fstat', return_value=SimpleNamespace(st_mode=mode | 0o600, st_uid=0)), \
                    patch.object(power.fcntl, 'ioctl'), patch.object(power.os, 'close') as close:
                with self.assertRaises(power.PowerError):
                    power.open_button(Path('/synthetic/event0'))
                close.assert_called_once_with(55)

    def test_watch_discards_stale_ready_entry_when_resume_removes_an_input(self):
        # Poll can return both buttons together. Resuming from the first one
        # can remove the second node, whose readiness entry is then stale.
        devices = self.base / 'input'
        devices.mkdir()
        paths = [devices / 'event0', devices / 'event1']
        for path in paths:
            path.touch()
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [(SimpleNamespace(fd=10, data=paths[0]), 1),
                                        (SimpleNamespace(fd=11, data=paths[1]), 1)]
        stopped = False
        reads = 0

        def read(fd, _size):
            nonlocal reads
            if fd == 11:
                raise OSError('Fixture device unplugged during resume')
            reads += 1
            if reads == 1:
                return (power.EVENT.pack(10, 0, power.EV_KEY, 116, 1)
                        + power.EVENT.pack(10, 100000, power.EV_KEY, 116, 0))
            raise BlockingIOError()

        def suspended():
            nonlocal stopped
            stopped = True

        with patch.object(power, 'guard'), patch.object(power, 'INPUT', devices), \
                patch.object(power, 'open_button', side_effect=[10, 11]), \
                patch.object(power.selectors, 'PollSelector', return_value=selector), \
                patch.object(power.os, 'read', read), patch.object(power.os, 'close') as close, \
                patch.object(power, 'suspend', side_effect=suspended) as suspend, \
                patch('builtins.print'):
            power.watch(lambda: stopped)
        suspend.assert_called_once_with()
        self.assertCountEqual([call.args[0] for call in close.call_args_list], [10, 11])
        self.assertEqual(selector.unregister.call_count, 2)

    def test_watch_closes_verified_node_if_poll_registration_fails(self):
        devices = self.base / 'input'
        devices.mkdir()
        (devices / 'event0').touch()
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.register.side_effect = ValueError('Fixture poll registration failed')
        selector.select.return_value = []
        stopped = Mock(side_effect=[False, True])
        with patch.object(power, 'guard'), patch.object(power, 'INPUT', devices), \
                patch.object(power, 'open_button', return_value=55), \
                patch.object(power.selectors, 'PollSelector', return_value=selector), \
                patch.object(power.os, 'close') as close:
            power.watch(stopped)
        close.assert_called_once_with(55)


class ButtonTapTests(unittest.TestCase):
    def setUp(self):
        self.detector = power.TapDetector()

    def key(self, value, timestamp, now=None, node='button', code=116):
        return self.detector.feed(node, power.EV_KEY, code, value, timestamp,
                                  timestamp if now is None else now)

    def test_tap_suspends_on_release_and_ignores_repeat(self):
        self.assertFalse(self.key(1, 10))
        self.assertFalse(self.key(2, 10.1))
        self.assertTrue(self.key(0, 10.2))

    def test_long_hold_and_buffered_long_hold_do_not_suspend(self):
        self.key(1, 10)
        self.assertFalse(self.key(0, 13))
        self.key(1, 20, now=30)
        self.assertFalse(self.key(0, 23, now=30))

    def test_resume_release_and_duplicate_button_do_not_resuspend(self):
        self.key(1, 10)
        self.key(1, 10, node='duplicate')
        self.assertTrue(self.key(0, 10.2))
        self.assertFalse(self.key(0, 10.3, node='duplicate'))
        self.detector.reset(20)
        self.assertFalse(self.key(0, 23))
        self.key(1, 24)
        self.assertTrue(self.key(0, 24.1))

    def test_syn_dropped_requires_new_press_after_report(self):
        self.key(1, 10)
        self.detector.feed('button', power.EV_SYN, power.SYN_DROPPED, 0, 10.1, 10.1)
        self.assertFalse(self.key(0, 10.2))
        self.assertFalse(self.key(1, 10.3))
        self.detector.feed('button', power.EV_SYN, power.SYN_REPORT, 0, 10.4, 10.4)
        self.assertFalse(self.key(0, 10.5))
        self.key(1, 11, code=142)
        self.assertTrue(self.key(0, 11.1, code=142))

    def test_hotplug_remove_and_other_keys_do_not_suspend(self):
        self.key(1, 10)
        self.detector.remove('button')
        self.assertFalse(self.key(0, 10.1))
        self.key(1, 11, code=30)
        self.assertFalse(self.key(0, 11.1, code=30))


if __name__ == '__main__':
    unittest.main()
