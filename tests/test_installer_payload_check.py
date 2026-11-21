"""Read-only payload evidence checks using sparse regular files; no guest services."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('payload_acceptance', str(ROOT / 'external/board/surface-go1/overlay/usr/sbin/surface-app-check'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
check = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(check)


class PayloadEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / 'surface-go.img'
        with self.payload.open('wb') as output:
            output.write(b'original sparse installation payload')
            output.truncate(64 * 1024**2)
        info = self.payload.stat()
        with self.payload.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.source = dict(path=str(self.payload), bytes=info.st_size, sha256=digest,
                           inode=info.st_ino, mtime_ns=info.st_mtime_ns,
                           filesystem_devnum=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}')
        self.listing = dict(event='list', sources=[self.source])

    def run_check(self, code=0):
        with patch.object(check, 'INSTALLER_PAYLOAD', self.payload), \
             patch.object(check, 'guard'), \
             patch.object(check, 'command', return_value=(code, json.dumps(self.listing))) as command, \
             contextlib.redirect_stdout(io.StringIO()):
            result = check.installer_check(self.root)
        return result, command

    def test_discovered_payload_requires_full_file_checksum_and_preserves_bytes(self):
        before = self.payload.stat()
        result, command = self.run_check()
        self.assertEqual(result['sha256'], self.source['sha256'])
        self.assertFalse(result['target_written'])
        command.assert_called_once_with(['/usr/sbin/surface-install-device', '--list'], self.root / 'installer-discovery.log', timeout=30)
        self.assertEqual(before.st_mtime_ns, self.payload.stat().st_mtime_ns)

    def test_changed_checksum_or_discovered_identity_fails(self):
        for key, replacement in [('sha256', 'a' * 64), ('inode', -1), ('mtime_ns', -1), ('filesystem_devnum', '0:0')]:
            with self.subTest(key=key), patch.dict(self.source, {key: replacement}):
                with self.assertRaises(RuntimeError):
                    self.run_check()

    def test_missing_discovery_and_failed_cli_never_pass(self):
        with self.assertRaisesRegex(RuntimeError, 'discover'):
            self.run_check(code=2)
        self.listing['sources'] = []
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            self.run_check()

    def test_guard_precedes_discovery_and_absent_payload_is_explicit(self):
        with patch.object(check, 'guard', side_effect=RuntimeError('host refused')), \
             patch.object(check, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'host refused'):
                check.installer_check(self.root)
            command.assert_not_called()
        self.payload.unlink()
        result, command = self.run_check()
        self.assertIn('without USB installer payload', result['mode'])
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
