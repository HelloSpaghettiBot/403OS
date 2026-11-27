"""Public ISO packaging fixtures: no mounts, images, devices or heavy tools."""
import gzip
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('iso_packaging', ROOT / 'scripts/build-iso.py')
iso = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(iso)


def unpack_newc(path):
    data = gzip.decompress(path.read_bytes())
    offset, records = 0, {}
    while True:
        if data[offset:offset + 6] != b'070701':
            raise AssertionError('Invalid archive magic')
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        offset += 110
        name = data[offset:offset + fields[11] - 1].decode()
        offset = (offset + fields[11] + 3) // 4 * 4
        payload = data[offset:offset + fields[6]]
        offset = (offset + fields[6] + 3) // 4 * 4
        if name == 'TRAILER!!!':
            return records
        records[name] = (fields, payload)


class ISOPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_layout_is_numeric_bounded_metadata_without_shell_input(self):
        value = dict(ROOT_OFFSET=68157440, ROOT_BYTES=2147483648,
                     IMAGE_BYTES=6510628864, IMAGE_SHA256='a' * 64)
        self.assertEqual(iso.layout_text(value).splitlines()[0], b'ROOT_OFFSET=68157440')
        for change in ({'ROOT_OFFSET': '../device'}, {'ROOT_BYTES': 1},
                       {'ROOT_BYTES': 6510628864}, {'IMAGE_SHA256': 'a' * 64 + '\nX=1'},
                       {'EXTRA': 'ignored'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                iso.layout_text(dict(value, **change))

    def test_cpio_normalizes_owner_permissions_and_device_nodes_without_root(self):
        entries = {'init': (stat.S_IFREG | 0o755, b'#!/bin/sh\n', (0, 0)),
                   'bin/sh': (stat.S_IFLNK | 0o777, b'busybox', (0, 0)),
                   'etc/surface-iso-layout': (stat.S_IFREG | 0o400, b'metadata\n', (0, 0))}
        first, second = self.root / 'first.gz', self.root / 'second.gz'
        iso.write_initramfs(first, entries, 123)
        iso.write_initramfs(second, entries, 123)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        records = unpack_newc(first)
        for fields, content in records.values():
            self.assertEqual(fields[2:4], [0, 0])
            self.assertEqual(fields[5], 123)
        self.assertTrue(stat.S_ISDIR(records['etc'][0][1]))
        self.assertEqual(records['init'][0][1], stat.S_IFREG | 0o755)
        self.assertEqual(records['bin/sh'][1], b'busybox')
        self.assertEqual(records['dev/console'][0][9:11], [5, 1])
        self.assertEqual(records['dev/null'][0][9:11], [1, 3])
        self.assertEqual(records['etc/surface-iso-layout'][0][1] & 0o777, 0o400)

    def test_cpio_rejects_path_escape_before_writing(self):
        for name in ('/host', '../outside', 'bin/../../escape', 'nul\0byte'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                iso.write_initramfs(self.root / 'bad.gz', {name: (stat.S_IFREG, b'', (0, 0))}, 0)
        self.assertFalse((self.root / 'bad.gz').exists())

    def test_guest_absolute_loader_symlink_stays_inside_guest(self):
        (self.root / 'lib').mkdir()
        library = self.root / 'lib/loader'
        library.write_bytes(b'fixture')
        (self.root / 'lib64').symlink_to('/lib', target_is_directory=True)
        (self.root / 'lib/libc.so').symlink_to('loader')
        self.assertEqual(iso.guest_file(self.root, '/lib64/libc.so'), library)

    def test_guest_symlink_escape_and_cycle_are_rejected(self):
        (self.root / 'escape').symlink_to('../host')
        (self.root / 'loop').symlink_to('loop')
        for path in ('/escape', '/loop', '/../host', 'relative'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                iso.guest_file(self.root, path)

    def test_runtime_keeps_lib64_loader_search_aliases(self):
        for directory in ('bin', 'sbin', 'lib', 'usr/lib', 'etc'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in ('bin/busybox', 'sbin/blkid', 'sbin/mke2fs',
                     'lib/ld-linux-x86-64.so.2', 'lib/libc.so.6', 'etc/mke2fs.conf'):
            (self.root / name).write_bytes(name.encode())
        (self.root / 'lib64').symlink_to('lib')
        (self.root / 'usr/lib64').symlink_to('lib')
        def inspect(image, offset, request):
            return (self.root / request.removeprefix('cat /')).read_bytes()
        def dependencies(path):
            return (['/lib64/ld-linux-x86-64.so.2'], ['libc.so.6']) if path.name=='busybox' else ([], [])
        with patch.object(iso, 'inspect_fs', side_effect=inspect), \
                patch.object(iso, 'elf_dependencies', side_effect=dependencies):
            entries = iso.runtime_entries(self.root / 'base', 1024, self.root)
        self.assertEqual(entries['lib64'][1], b'lib')
        self.assertTrue(stat.S_ISLNK(entries['lib64'][0]))
        self.assertIn('lib/ld-linux-x86-64.so.2', entries)
        self.assertIn('lib/libc.so.6', entries)
        self.assertFalse(any(name.startswith('lib64/') for name in entries))

    def test_stage_contains_only_pristine_base_and_checksum_without_chmod(self):
        base = self.root / 'surface-go-base.img'
        base.write_bytes(b'fixture'); base.chmod(0o640)
        before = base.stat()
        stage = self.root / 'stage'
        iso.stage_payload(base, stage, 'a' * 64)
        embedded = stage / iso.PAYLOAD
        self.assertEqual(embedded.stat().st_ino, before.st_ino)
        self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o640)
        self.assertEqual(sorted(str(path.relative_to(stage)) for path in stage.rglob('*')),
                         ['SurfaceGoGamingOS', iso.PAYLOAD, iso.PAYLOAD + '.sha256'])
        self.assertEqual((stage / (iso.PAYLOAD + '.sha256')).read_text(), 'a' * 64 + '  surface-go.img\n')
        with self.assertRaises(FileExistsError):
            iso.stage_payload(base, stage, 'a' * 64)
        link = self.root / 'alias'; link.symlink_to(base)
        with self.assertRaises(ValueError):
            iso.stage_payload(link, self.root / 'other', 'a' * 64)

    def test_runtime_collects_real_elf_dependencies_without_execution(self):
        source = Path('/bin/true').resolve()
        if not source.exists():
            self.skipTest('Host /bin/true unavailable')
        interpreters, needed = iso.elf_dependencies(source)
        self.assertEqual(len(interpreters), 1)
        self.assertIn('libc.so.6', needed)
        self.assertTrue(interpreters[0].startswith('/'))
        bad = self.root / 'not-elf'; bad.write_bytes(b'\x7fELF\x02\x01')
        with self.assertRaisesRegex(ValueError, 'x86-64 ELF'):
            iso.elf_dependencies(bad)

    def test_missing_debugfs_path_is_a_hard_failure_despite_zero_tool_exit(self):
        response = subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'File not found by ext2_lookup')
        with patch.object(iso, 'command', return_value=response), self.assertRaises(ValueError):
            iso.inspect_fs(self.root / 'image', 4096, 'cat /required')

    def test_public_state_gate_refuses_profiles_roms_pairings_and_passwords(self):
        def directories(image, offset, path):
            if offset == 2000:
                return {'.', '..', 'lost+found'}
            return {'.', '..', 'windows'} if path == '/data' else {'.', '..'}
        def content(image, offset, request):
            return b'root::0:0:99999:7:::\n' if request.endswith('/etc/shadow') else b'ctrl_interface=/run/wpa_supplicant\n'
        with patch.object(iso, 'names', side_effect=directories), patch.object(iso, 'inspect_fs', side_effect=content):
            iso.public_filesystem(self.root / 'base', 1000, 2000)
        for path, offset, entry in [('/', 2000, 'roms'), ('/data', 1000, 'profiles'),
                                     ('/root', 1000, '.ssh'), ('/var/lib/bluetooth', 1000, 'paired-device')]:
            def changed(image, actual_offset, actual_path):
                values = directories(image, actual_offset, actual_path)
                return values | {entry} if (actual_offset, actual_path) == (offset, path) else values
            with self.subTest(path=path), patch.object(iso, 'names', side_effect=changed), \
                 patch.object(iso, 'inspect_fs', side_effect=content), self.assertRaises(ValueError):
                iso.public_filesystem(self.root / 'base', 1000, 2000)
        for secret in (b'root:$6$synthetic-hash:0:0:99999:7:::\n', b'network={\npsk="fixture"\n}\n'):
            def altered(image, offset, request):
                if (secret.startswith(b'root:') and request.endswith('/etc/shadow') or
                    secret.startswith(b'network=') and request.endswith('/etc/wpa_supplicant.conf')):
                    return secret
                return content(image, offset, request)
            with patch.object(iso, 'names', side_effect=directories), \
                 patch.object(iso, 'inspect_fs', side_effect=altered), self.assertRaises(ValueError):
                iso.public_filesystem(self.root / 'base', 1000, 2000)

    def test_build_refuses_uncapped_host_before_reading_source(self):
        with patch.dict(os.environ, {'SURFACE_RESOURCE_SCOPE': ''}), \
             patch.object(iso, 'validate_base') as reading, self.assertRaisesRegex(ValueError, 'capped'):
            iso.build()
        reading.assert_not_called()


if __name__ == '__main__':
    unittest.main()
