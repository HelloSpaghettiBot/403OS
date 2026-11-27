import importlib.util
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('bluetooth_input', ROOT / 'services/bluetooth-input.py')
bt_input = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bt_input)
ADDRESS = 'AA:BB:CC:DD:EE:FF'


class BluetoothInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sysfs = self.root / 'sys'
        self.dev = self.root / 'dev'
        self.sysfs.mkdir()
        self.dev.mkdir()

    def node(self, name, address=ADDRESS):
        device = self.sysfs / name / 'device'
        device.mkdir(parents=True)
        (device / 'uniq').write_text(address + '\n')

    def test_discovery_only_opens_exact_peer_event_nodes(self):
        self.node('event4', ADDRESS.lower())
        self.node('event5', '11:22:33:44:55:66')
        self.node('event_bad')
        inspect = mock.Mock(return_value=dict(node='event4', gamepad=True))
        self.assertEqual(bt_input.find_input(ADDRESS, self.sysfs, self.dev, inspect),
                         [dict(node='event4', gamepad=True)])
        inspect.assert_called_once_with(self.dev / 'event4', ADDRESS)

    def test_hotplug_failure_keeps_searching_for_another_peer_interface(self):
        self.node('event1')
        self.node('event2')
        inspect = mock.Mock(side_effect=[OSError('removed'), dict(node='event2', gamepad=True)])
        self.assertEqual(bt_input.find_input(ADDRESS, self.sysfs, self.dev, inspect),
                         [dict(node='event2', gamepad=True)])

    def test_gamepad_requirement_does_not_accept_keyboard_companion(self):
        self.node('event1')
        inspect = mock.Mock(return_value=dict(node='event1', gamepad=False))
        self.assertEqual(bt_input.find_input(ADDRESS, self.sysfs, self.dev, inspect,
                                            require_gamepad=True), [])
        self.assertEqual(len(bt_input.find_input(ADDRESS, self.sysfs, self.dev, inspect)), 1)

    def test_missing_input_times_out_even_if_bluetooth_link_is_connected(self):
        time = [0.0]
        sleeps = []
        def sleep(value):
            sleeps.append(value)
            time[0] += value
        with self.assertRaises(bt_input.InputNotReady):
            bt_input.wait_for_input(ADDRESS, timeout=0.5, sysfs_root=self.sysfs,
                dev_root=self.dev, clock=lambda: time[0], sleep=sleep)
        self.assertAlmostEqual(sum(sleeps), 0.5)
        self.assertTrue(all(value <= 0.2 for value in sleeps))

    def test_async_hog_node_is_waited_for(self):
        time = [0.0]
        def sleep(value):
            time[0] += value
            self.node('event9')
        inspect = mock.Mock(return_value=dict(node='event9', gamepad=True))
        result = bt_input.wait_for_input(ADDRESS, timeout=1, sysfs_root=self.sysfs,
            dev_root=self.dev, inspect=inspect, clock=lambda: time[0], sleep=sleep)
        self.assertEqual(result, [dict(node='event9', gamepad=True)])

    def test_existing_evdev_waits_for_udev_classification_and_application_permissions(self):
        self.node('event9')
        time = [0.0]
        def sleep(value):
            time[0] += value
        inspect = mock.Mock(side_effect=[
            ValueError('Controller input is not readable by applications yet'),
            ValueError('Controller input is not classified for SDL applications yet'),
            dict(node='event9', gamepad=True)])
        result = bt_input.wait_for_input(ADDRESS, timeout=1, sysfs_root=self.sysfs,
            dev_root=self.dev, inspect=inspect, require_gamepad=True,
            clock=lambda: time[0], sleep=sleep)
        self.assertEqual(result, [dict(node='event9', gamepad=True)])
        self.assertEqual(inspect.call_count, 3)
        self.assertAlmostEqual(time[0], .4)

    def test_invalid_address_rejected_before_any_device_access(self):
        with mock.patch.object(bt_input.os, 'open') as opened:
            with self.assertRaises(ValueError):
                bt_input.find_input('../../event0', self.sysfs, self.dev)
            opened.assert_not_called()

    def inspect_fixture(self, *, address=ADDRESS, bus=5, key=0x130, axis=True,
                        mode=stat.S_IFCHR | 0o660, uid=0):
        def ioctl(fd, request, buffer, mutate):
            self.assertEqual(fd, 7)
            self.assertTrue(mutate)
            self.assertEqual(request >> 30, 2)  # Only read-only ioctls.
            number = request & 0xff
            values = {0x02: struct.pack('@HHHH', bus, 0x1234, 0x5678, 1),
                      0x08: address.encode('ascii') + b'\0',
                      0x21: (0 if key is None else 1 << key).to_bytes(96, 'little'),
                      0x23: int(axis).to_bytes(8, 'little')}
            value = values[number]
            buffer[:len(value)] = value
        opened = self.enterContext(mock.patch.object(bt_input.os, 'open', return_value=7))
        self.enterContext(mock.patch.object(bt_input.os, 'fstat', return_value=SimpleNamespace(st_mode=mode, st_uid=uid)))
        closed = self.enterContext(mock.patch.object(bt_input.os, 'close'))
        self.enterContext(mock.patch.object(bt_input.fcntl, 'ioctl', side_effect=ioctl))
        self.access = self.enterContext(mock.patch.object(bt_input, 'require_application_access'))
        return opened, closed

    def test_opened_evdev_identity_is_checked_without_consuming_or_grabbing_input(self):
        opened, closed = self.inspect_fixture()
        with mock.patch.object(bt_input.os, 'read') as read:
            result = bt_input.inspect_event('/dev/input/event9', ADDRESS)
        self.assertTrue(result['gamepad'])
        self.assertEqual(result['bus'], 5)
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)
        read.assert_not_called()
        self.access.assert_called_once()
        closed.assert_called_once_with(7)

    def test_root_readable_evdev_is_not_ready_if_application_gate_fails(self):
        _, closed = self.inspect_fixture()
        self.access.side_effect = ValueError('Controller input is not readable by applications yet')
        with self.assertRaisesRegex(ValueError, 'not readable by applications'):
            bt_input.inspect_event('/dev/input/event9', ADDRESS)
        closed.assert_called_once_with(7)

    def test_reused_event_node_cannot_confirm_another_device(self):
        _, closed = self.inspect_fixture(address='11:22:33:44:55:66')
        with self.assertRaisesRegex(ValueError, 'different device'):
            bt_input.inspect_event('/dev/input/event9', ADDRESS)
        closed.assert_called_once_with(7)

    def test_wired_usb_controller_does_not_confirm_bluetooth_input(self):
        _, closed = self.inspect_fixture(bus=3)
        with self.assertRaisesRegex(ValueError, 'different device'):
            bt_input.inspect_event('/dev/input/event9', ADDRESS)
        closed.assert_called_once_with(7)

    def test_motion_only_controller_interface_is_not_controller_readiness(self):
        _, closed = self.inspect_fixture(key=None)
        with self.assertRaisesRegex(ValueError, 'no buttons or keys'):
            bt_input.inspect_event('/dev/input/event9', ADDRESS)
        closed.assert_called_once_with(7)

    def test_numbered_buttons_and_axes_support_generic_gamepads(self):
        self.inspect_fixture(key=0x100)
        self.assertTrue(bt_input.inspect_event('/dev/input/event9', ADDRESS)['gamepad'])

    def test_noncharacter_node_is_rejected_and_closed(self):
        _, closed = self.inspect_fixture(mode=stat.S_IFREG | 0o644)
        with self.assertRaisesRegex(ValueError, 'character device'):
            bt_input.inspect_event('/dev/input/event9', ADDRESS)
        closed.assert_called_once_with(7)


class ApplicationAccessTests(unittest.TestCase):
    def fixture(self, record=b'E:ID_INPUT=1\nE:ID_INPUT_JOYSTICK=1\n', **changes):
        self.info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0, st_gid=1100,
                                    st_rdev=os.makedev(13, 73), st_dev=9, st_ino=321)
        self.record = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0,
                                      st_size=len(record), st_mtime_ns=123, st_ctime_ns=456)
        for key, value in changes.items():
            setattr(self.info, key, value)
        self.enterContext(mock.patch.object(bt_input.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=1100)))
        self.opened = self.enterContext(mock.patch.object(bt_input.os, 'open', return_value=9))
        self.enterContext(mock.patch.object(bt_input.os, 'fstat', return_value=self.record))
        self.read = self.enterContext(mock.patch.object(bt_input.os, 'read', return_value=record))
        self.current = self.enterContext(mock.patch.object(bt_input.os, 'stat', return_value=self.info))
        self.closed = self.enterContext(mock.patch.object(bt_input.os, 'close'))

    def check(self):
        return bt_input.require_application_access('/dev/input/event9', self.info)

    def test_matching_udev_record_and_gamepad_read_permission_are_ready(self):
        self.fixture()
        self.check()
        path, flags = self.opened.call_args.args
        self.assertEqual(str(path), '/run/udev/data/c13:73')
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertTrue(flags & os.O_NONBLOCK)
        self.read.assert_called_once_with(9, 65537)
        self.current.assert_called_once_with('/dev/input/event9', follow_symlinks=False)
        self.closed.assert_called_once_with(9)

    def test_root_only_read_permission_does_not_confirm_application_input(self):
        self.fixture(st_mode=stat.S_IFCHR | 0o600)
        with self.assertRaisesRegex(ValueError, 'not readable by applications'):
            self.check()
        self.opened.assert_not_called()

    def test_input_group_without_gamepad_group_does_not_confirm_application_input(self):
        self.fixture(st_gid=104)
        with self.assertRaisesRegex(ValueError, 'not readable by applications'):
            self.check()
        self.opened.assert_not_called()

    def test_root_access_and_gamepad_permissions_do_not_replace_sdl_classification(self):
        self.fixture(record=b'E:ID_INPUT=1\nE:ID_INPUT_KEYBOARD=1\nE:ID_INPUT_JOYSTICK=0\n')
        with self.assertRaisesRegex(ValueError, 'not classified for SDL'):
            self.check()
        self.closed.assert_called_once_with(9)

    def test_udev_record_cannot_be_symlinked_or_untrusted_or_unbounded(self):
        self.fixture()
        for mode, uid, size in ((stat.S_IFLNK | 0o777, 0, 20),
                                (stat.S_IFREG | 0o644, 1000, 20),
                                (stat.S_IFREG | 0o666, 0, 20),
                                (stat.S_IFREG | 0o644, 0, 65537)):
            with self.subTest(mode=mode, uid=uid, size=size):
                self.record.st_mode, self.record.st_uid, self.record.st_size = mode, uid, size
                with self.assertRaisesRegex(ValueError, 'unsafe or incomplete'):
                    self.check()
        self.read.assert_not_called()

    def test_replaced_event_path_invalidates_matching_classification(self):
        self.fixture()
        replacement = vars(self.info).copy()
        replacement['st_ino'] += 1
        self.current.return_value = SimpleNamespace(**replacement)
        with self.assertRaisesRegex(ValueError, 'changed while checking'):
            self.check()

    def test_incomplete_udev_record_is_retried_instead_of_accepted(self):
        self.fixture()
        self.record.st_size += 1
        with self.assertRaisesRegex(ValueError, 'still changing'):
            self.check()


if __name__ == '__main__':
    unittest.main()
