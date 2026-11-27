"""Optical boot topology fixtures. No mounts, devices, or VM are created."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('iso_mount_data', ROOT / 'services/mount-data.py')
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)
LAYOUT = ('ROOT_OFFSET=68157440\nROOT_BYTES=2147483648\n'
          'IMAGE_BYTES=6510628864\nIMAGE_SHA256=' + 'a' * 64 + '\n')


def mount_line(dev, path, filesystem, writable, root='/', extra='', super_options=''):
    options = ('rw' if writable else 'ro') + (',' + extra if extra else '')
    return f'20 1 {dev} {root} {path} {options} - {filesystem} fixture {options},{super_options}\n'


class IsoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.devices = self.base / 'sys/devices'
        self.dev_block = self.base / 'sys/dev/block'
        self.dev_block.mkdir(parents=True)
        self.devices.mkdir(parents=True)
        self.nodes = {}
        self.node('sr0', '11:0', 2 * 1024**3)
        self.loop('loop0', '7:0', '/run/surface-iso/media/403os/payload.squashfs', True, 1024**3)
        parent = self.loop('loop1', '7:1', '/run/surface-iso/payload/SurfaceGoGamingOS/surface-go.img',
                           True, 6510628864)
        partition = self.node('loop1p2', '259:0', 2147483648, parent)
        (partition / 'partition').write_text('2')
        (partition / 'start').write_text('133120')
        self.loop('loop2', '7:2', '/run/surface-iso/live-data.ext4', False, 4 * 1024**3)
        self.inventory = [
            mount_line('0:100', '/', 'overlay', True, super_options=
                       'lowerdir=/run/surface-iso/lower,upperdir=/run/surface-iso/upper,workdir=/run/surface-iso/work'),
            mount_line('0:50', '/run', 'tmpfs', True, extra='nosuid,nodev', super_options='size=1572864k'),
            mount_line('11:0', '/run/surface-iso/media', 'iso9660', False),
            mount_line('7:0', '/run/surface-iso/payload', 'squashfs', False),
            mount_line('259:0', '/run/surface-iso/lower', 'ext4', False),
            mount_line('7:2', '/data', 'ext4', True),
            mount_line('7:0', '/data/SurfaceGoGamingOS', 'squashfs', False, root='/SurfaceGoGamingOS'),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def node(self, name, dev, size, parent=None):
        path = (parent or self.devices / 'virtual/block') / name
        path.mkdir(parents=True)
        (path / 'dev').write_text(dev)
        (path / 'size').write_text(str(size // 512))
        (self.dev_block / dev).symlink_to(path)
        self.nodes[name] = path
        return path

    def loop(self, name, dev, backing, readonly, size):
        path = self.node(name, dev, size)
        (path / 'ro').write_text('1' if readonly else '0')
        (path / 'loop').mkdir()
        for key, value in {'backing_file': backing, 'offset': '0', 'sizelimit': '0'}.items():
            (path / 'loop' / key).write_text(value)
        return path

    def discover(self):
        return data.discover_iso(data.mounts(''.join(self.inventory)), data.iso_layout(LAYOUT),
                                 self.dev_block, self.devices, self.base / 'dev')

    def test_valid_optical_topology_retains_distinct_readonly_root_and_volatile_data(self):
        result = self.discover()
        self.assertTrue(result['iso'])
        self.assertEqual(result['disk']['name'], 'sr0')
        self.assertEqual(result['root']['name'], 'loop1p2')
        self.assertEqual(result['data']['name'], 'loop2')
        self.assertEqual(result['data']['partuuid'], '')

    def test_layout_rejects_injection_duplicates_missing_fields_and_invalid_geometry(self):
        invalid = [LAYOUT + 'ROOT_BYTES=2147483648\n', LAYOUT.replace('ROOT_OFFSET=68157440\n', ''),
                   LAYOUT.replace('68157440', '$(touch /tmp/fixture)'),
                   LAYOUT.replace('68157440', '68157441'), LAYOUT.replace('2147483648', '999999999999'),
                   LAYOUT.replace('IMAGE_SHA256=' + 'a' * 64, 'IMAGE_SHA256=invalid')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(data.Refused):
                data.iso_layout(value)

    def test_each_required_mount_must_be_present_and_unique(self):
        original = list(self.inventory)
        for index in range(len(original)):
            with self.subTest(missing=index), self.assertRaises(data.Refused):
                self.inventory = original[:index] + original[index + 1:]
                self.discover()
            with self.subTest(duplicated=index), self.assertRaises(data.Refused):
                self.inventory = original + [original[index]]
                self.discover()

    def test_root_overlay_and_memory_bound_cannot_be_substituted(self):
        for index, before, after in ((0, '/run/surface-iso/lower', '/other/root'),
                                     (1, 'size=1572864k', 'size=8388608k'),
                                     (1, ',nodev', ''), (5, ' ext4 ', ' tmpfs ')):
            old = self.inventory[index]
            self.inventory[index] = old.replace(before, after)
            with self.subTest(index=index, after=after), self.assertRaises(data.Refused):
                self.discover()
            self.inventory[index] = old

    def test_media_payload_lower_and_installer_binding_must_be_readonly(self):
        for index in (2, 3, 4, 6):
            old = self.inventory[index]
            self.inventory[index] = old.replace(' ro ', ' rw ')
            with self.subTest(index=index), self.assertRaises(data.Refused):
                self.discover()
            self.inventory[index] = old

    def test_each_loop_must_use_exact_backing_file_range_and_write_access(self):
        for name in ('loop0', 'loop1', 'loop2'):
            for relative, replacement in (('loop/backing_file', '/data/personal.img'),
                                           ('loop/offset', '512'), ('loop/sizelimit', '4096'),
                                           ('ro', '1' if name == 'loop2' else '0')):
                path = self.nodes[name] / relative
                old = path.read_text()
                path.write_text(replacement)
                with self.subTest(name=name, relative=relative), self.assertRaises(data.Refused):
                    self.discover()
                path.write_text(old)

    def test_root_partition_geometry_and_image_capacities_must_match(self):
        for name, relative in (('loop1p2', 'partition'), ('loop1p2', 'start'),
                               ('loop1p2', 'size'), ('loop1', 'size'), ('loop2', 'size')):
            path = self.nodes[name] / relative
            old = path.read_text()
            path.write_text(str(int(old) + 1))
            with self.subTest(name=name, relative=relative), self.assertRaises(data.Refused):
                self.discover()
            path.write_text(old)

    def test_installer_binding_cannot_point_to_a_different_payload_or_directory(self):
        old = self.inventory[6]
        for value in (old.replace('7:0', '7:2'), old.replace(' /SurfaceGoGamingOS ', ' /Other ')):
            self.inventory[6] = value
            with self.subTest(value=value), self.assertRaises(data.Refused):
                self.discover()

    def test_init_script_has_valid_shell_syntax(self):
        subprocess.run(['sh', '-n', str(ROOT / 'external/board/surface-go1/iso-init')], check=True)


if __name__ == '__main__':
    unittest.main()
