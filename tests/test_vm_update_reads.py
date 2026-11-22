"""Actual read-only QCOW2 byte reads; regular fixtures and Unix sockets only."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('vm_update_reads', ROOT/'scripts/vm.py')
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)
TOOLS = ('qemu-img', 'qemu-io', 'qemu-nbd')


@unittest.skipUnless(all(shutil.which(name) for name in TOOLS) and os.environ.get('XDG_RUNTIME_DIR'),
                     'QEMU tools and a private runtime directory are required')
class ReadOnlyVirtualDiskTests(unittest.TestCase):
    def setUp(self):
        vm.ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='readonly-byte-fixture-', dir=vm.ARTIFACTS)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.base = self.directory/'base.raw'
        self.overlay = self.directory/'overlay.qcow2'
        self.size = 8*1024**2
        with self.base.open('wb') as output:
            output.truncate(self.size)
            output.seek(1024**2);output.write(b'B'*4096)
            output.seek(2*1024**2);output.write(b'Z'*4096)
            output.seek(self.size-512);output.write(b'T'*512)
        self.run_tool('qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(self.base), str(self.overlay))
        self.run_tool('qemu-io', '-f', 'qcow2', '-c', 'write -P 0xa5 3145728 4096',
                      '-c', 'write -z 2097152 4096', str(self.overlay))

    @staticmethod
    def run_tool(*arguments):
        return subprocess.run(arguments, check=True, capture_output=True, timeout=20)

    def hashes(self):
        return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.base, self.overlay))

    def test_resolves_backing_overlay_and_explicit_zero_without_changing_either_image(self):
        before = self.hashes()
        reader = vm.ReadOnlyVirtualDisk(self.overlay, 'qcow2', self.directory, 'fixture')
        with reader as disk:
            self.assertEqual(disk.size, self.size)
            self.assertTrue(disk.address.is_socket())
            self.assertEqual(disk.read(1024**2, 4096), b'B'*4096)  # Backing data.
            self.assertEqual(disk.read(2*1024**2, 4096), bytes(4096))  # Overlay zero masks backing Z.
            self.assertEqual(disk.read(3*1024**2, 4096), b'\xa5'*4096)  # Overlay data.
            self.assertEqual(disk.read(self.size-512, 512), b'T'*512)
            expected = bytearray(self.base.read_bytes())
            expected[2*1024**2:2*1024**2+4096] = bytes(4096)
            expected[3*1024**2:3*1024**2+4096] = b'\xa5'*4096
            self.assertEqual(disk.read(0, self.size), bytes(expected))
        self.assertIsNotNone(reader.process.poll())
        self.assertFalse(reader.address.exists())
        self.assertEqual(self.hashes(), before)

    def test_bounds_refuse_before_request_and_exception_closes_server_and_socket(self):
        before = self.hashes()
        reader = vm.ReadOnlyVirtualDisk(self.overlay, 'qcow2', self.directory, 'fixture')
        with self.assertRaisesRegex(RuntimeError, 'intentional fixture exception'):
            with reader as disk:
                for offset, count in ((-1, 512), (0, 0), (self.size, 1), (self.size-1, 2),
                                      (0, 8*1024**2+1)):
                    with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                        disk.read(offset, count)
                self.assertEqual(disk.read(3*1024**2, 512), b'\xa5'*512)
                raise RuntimeError('intentional fixture exception')
        self.assertIsNotNone(reader.process.poll())
        self.assertFalse(reader.address.exists())
        self.assertEqual(self.hashes(), before)


if __name__ == '__main__':
    unittest.main()
