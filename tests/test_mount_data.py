"""Boot disk topology fixtures; no block devices are mounted or modified."""
import importlib.util
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mount_data', ROOT / 'services/mount-data.py')
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)
GPT_SPEC = importlib.util.spec_from_file_location('mount_data_gpt_fixture', ROOT / 'tests/test_grow_data.py')
gpt_fixture = importlib.util.module_from_spec(GPT_SPEC)
GPT_SPEC.loader.exec_module(gpt_fixture)
ROOT_UUID = '8b4a530e-d6d4-4e69-9307-310000000011'
DATA_UUID = '8b4a530e-d6d4-4e69-9307-310000000012'


def mount_line(dev, target='/', filesystem='ext4', options='rw', root='/'):
    return f'20 1 {dev} {root} {target} {options} - {filesystem} /dev/example {options}\n'


class MountDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.devices = self.base / 'sys/devices'
        self.dev_block = self.base / 'sys/dev/block'
        self.dev_block.mkdir(parents=True)
        self.devices.mkdir(parents=True)
        self.device_nodes = self.base / 'dev'
        self.device_nodes.mkdir()
        self.disk('sda', '8:0', '8:2', '8:3')
        self.disk('sdb', '8:16', '8:18', '8:19')

    def tearDown(self):
        self.temp.cleanup()

    def disk(self, name, disk_dev, root_dev, data_dev):
        disk = self.devices / 'pci0000:00/block' / name
        disk.mkdir(parents=True)
        (disk / 'dev').write_text(disk_dev)
        (disk / 'size').write_text(str(gpt_fixture.DISK_BYTES // 512))
        suffix = 'p' if name[-1].isdigit() else ''
        for number, dev, identity in ((2, root_dev, ROOT_UUID), (3, data_dev, DATA_UUID)):
            node = disk / f'{name}{suffix}{number}'
            node.mkdir()
            (node / 'partition').write_text(str(number))
            (node / 'dev').write_text(dev)
            first = 133120 if number == 2 else gpt_fixture.DATA_START
            size = 2 * 1024 ** 3 if number == 2 else gpt_fixture.grow.ORIGINAL_DATA_BYTES
            (node / 'start').write_text(str(first))
            (node / 'size').write_text(str(size // 512))
            # Linux 6.12 exposes partition number/name, without PARTUUID.
            (node / 'uevent').write_text(f'DEVNAME={node.name}\nPARTN={number}\n')
            (self.dev_block / dev).symlink_to(node)
        return disk

    def select(self, text=None):
        return data.discover(data.mounts(text or mount_line('8:18')), self.dev_block, self.devices, self.device_nodes)

    def test_duplicate_ids_on_other_disk_never_select_its_data(self):
        # Both image clones deliberately have the same partition UUIDs here.
        chosen = self.select()
        self.assertEqual(chosen['disk']['name'], 'sdb')
        self.assertEqual(chosen['root']['name'], 'sdb2')
        self.assertEqual(chosen['data']['name'], 'sdb3')
        self.assertNotIn('partuuid', chosen['root'])
        self.assertNotIn('partuuid', chosen['data'])
        self.assertEqual(self.select(mount_line('8:2'))['data']['name'], 'sda3')

    def test_nvme_and_emmc_names_are_resolved_from_topology(self):
        for name, major in (('nvme0n1', 259), ('mmcblk0', 179)):
            with self.subTest(name=name):
                self.disk(name, f'{major}:0', f'{major}:2', f'{major}:3')
                self.assertEqual(self.select(mount_line(f'{major}:2'))['data']['name'], name + 'p3')

    def test_wrong_root_filesystem_subdirectory_or_duplicate_mount_refused(self):
        for value in (mount_line('8:18', filesystem='overlay'), mount_line('8:18', root='/subdir'),
                      mount_line('8:18') + mount_line('8:2')):
            with self.subTest(value=value), self.assertRaises(data.Refused):
                self.select(value)

    def test_root_must_be_partition_two_with_matching_device_identity(self):
        node = self.dev_block / '8:18'
        (node / 'partition').write_text('1')
        with self.assertRaisesRegex(data.Refused, 'partition 2'):
            self.select()
        (node / 'partition').write_text('2')
        (node / 'dev').write_text('8:2')
        with self.assertRaisesRegex(data.Refused, 'identities disagree'):
            self.select()

    def test_missing_or_duplicate_data_partition_refused(self):
        node = self.dev_block / '8:19'
        (node / 'partition').write_text('4')
        with self.assertRaises(data.Refused):
            self.select()
        (node / 'partition').write_text('3')
        duplicate = node.resolve().parent / 'sdb4'
        duplicate.mkdir()
        (duplicate / 'partition').write_text('3')
        with self.assertRaises(data.Refused):
            self.select()

    def test_data_symlink_cannot_point_to_other_disk(self):
        node = (self.dev_block / '8:19').resolve()
        for child in node.iterdir():
            child.unlink()
        node.rmdir()
        node.symlink_to((self.dev_block / '8:3').resolve())
        with self.assertRaisesRegex(data.Refused, 'does not belong'):
            self.select()

    def test_uevent_partuuid_is_not_required_or_used_to_select_the_disk(self):
        for value in ('', 'not-a-uuid', '../sda3'):
            (self.dev_block / '8:18' / 'uevent').write_text('PARTUUID=' + value)
            with self.subTest(value=value):
                self.assertEqual(self.select()['data']['name'], 'sdb3')

    def test_larger_physical_disk_gpt_supplies_identity_without_uevent_partuuid(self):
        image = self.base / 'gpt.img'
        gpt_fixture.fixture(image)
        selected = self.select()
        with image.open('rb') as stream:
            layout = gpt_fixture.grow.read_media_gpt(stream.fileno(), selected['disk']['bytes'])
            # A raw USB image's backup GPT remains at the image end even
            # when the physical USB has much more capacity beyond it.
            self.assertLess(layout['primary']['backup'] * 512, selected['disk']['bytes'] // 2)
            result = data.read_partition_guids(stream.fileno(), selected, gpt_fixture.grow.read_media_gpt)
        self.assertEqual(result['root']['partuuid'], gpt_fixture.grow.ROOT_UUID)
        self.assertEqual(result['data']['partuuid'], gpt_fixture.grow.DATA_UUID)

    def test_corrupt_gpt_backup_and_kernel_range_mismatch_refused(self):
        image = self.base / 'gpt.img'
        gpt_fixture.fixture(image)
        selected = self.select()
        selected['data']['first'] += 1
        with image.open('rb') as stream, self.assertRaisesRegex(data.Refused, 'geometry differs'):
            data.read_partition_guids(stream.fileno(), selected, gpt_fixture.grow.read_media_gpt)
        with image.open('r+b') as stream:
            stream.seek((gpt_fixture.DATA_END + 8) * 512 + 40)
            stream.write(b'corrupt table fixture')
        with image.open('rb') as stream, self.assertRaisesRegex(ValueError, 'CRC|backup'):
            data.read_partition_guids(stream.fileno(), self.select(), gpt_fixture.grow.read_media_gpt)

    def test_wrong_or_read_only_already_mounted_data_refused(self):
        selected = self.select()
        for extra in (mount_line('8:3', '/data'), mount_line('8:19', '/data', options='ro'),
                      mount_line('8:19', '/data', filesystem='vfat'),
                      mount_line('8:19', '/data', root='/subdir')):
            with self.subTest(extra=extra), self.assertRaises(data.Refused):
                data.confirm_data(data.mounts(extra), selected)
        self.assertFalse(data.confirm_data([], selected))
        self.assertTrue(data.confirm_data(data.mounts(mount_line('8:19', '/data')), selected))

    def test_regular_files_and_symlink_device_nodes_refused(self):
        node = self.device_nodes / 'sdb3'
        node.write_bytes(b'fixture, never a block device')
        record = self.select()['data']
        with self.assertRaisesRegex(data.Refused, 'Opened block device'):
            data.opened_block(record)
        node.unlink()
        node.symlink_to(self.device_nodes / 'sdb2')
        with self.assertRaises(OSError):
            data.opened_block(record)

    def test_probe_requires_unambiguous_ext4_with_data_label_and_inherited_fd(self):
        for status, output in ((0, 'TYPE=ext4\nLABEL=SURFACE_DATA\n'), (2, ''),
                               (0, 'TYPE=ext4\nLABEL=OTHER\n'), (0, 'TYPE=vfat\nLABEL=SURFACE_DATA\n')):
            with self.subTest(output=output), patch.object(data, 'run', return_value=SimpleNamespace(
                    returncode=status, stdout=output)) as run:
                if output.startswith('TYPE=ext4\nLABEL=SURFACE_DATA') and status == 0:
                    data.probe_data(7)
                else:
                    with self.assertRaises(data.Refused):
                        data.probe_data(7)
                self.assertEqual(run.call_args.kwargs['pass_fds'], (7,))
                self.assertEqual(run.call_args.args[0][-1], '/proc/self/fd/7')

    def test_missing_serial_console_does_not_fail_boot_diagnostics(self):
        with patch('builtins.print', side_effect=OSError('no console')), \
                patch.object(data.os, 'open', side_effect=OSError('no UART')):
            data.boot_marker('SURFACE_BOOT_DISK=/dev/example')

    def test_mount_uses_held_descriptors_and_confirms_same_disk(self):
        selected = self.select()
        mountinfo = self.base / 'mountinfo'
        mountinfo.write_text(mount_line('8:18'))
        target = self.base / 'data'
        target.mkdir()
        # Buildroot's user table creates this home in the root filesystem.
        # It must be covered by DATA, never mistaken for an existing mount.
        (target / 'windows').mkdir()
        opened = []
        def fake_open(record):
            path = self.base / record['name']
            path.write_bytes(b'disposable regular-file fixture')
            fd = os.open(path, os.O_RDONLY)
            opened.append(fd)
            return fd
        def fake_mount(command, pass_fds=()):
            self.assertEqual(command[:8], ['/bin/mount', '-n', '-i', '-c', '-t', 'ext4', '-o', 'rw'])
            self.assertEqual(command[8], f'/proc/self/fd/{opened[2]}')
            self.assertEqual(len(pass_fds), 2)
            mountinfo.write_text(mount_line('8:18') + mount_line('8:19', '/data'))
            return SimpleNamespace(returncode=0)
        with patch.object(data, 'require_guest'), patch.object(data, 'discover', return_value=selected), \
                patch.object(data, 'MOUNTINFO', mountinfo), patch.object(data, 'DATA', target), \
                patch.object(data, 'opened_block', side_effect=fake_open), patch.object(data, 'probe_data'), \
                patch.object(data, 'gpt_reader'), patch.object(data, 'read_partition_guids', return_value=selected), \
                patch.object(data.fcntl, 'ioctl', return_value=struct.pack('Q', selected['disk']['bytes'])), \
                patch.object(data.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)), \
                patch.object(data, 'run', side_effect=fake_mount):
            self.assertEqual(data.mount_data(), selected)
        self.assertTrue((target / 'windows').is_dir())
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)


if __name__ == '__main__':
    unittest.main()
