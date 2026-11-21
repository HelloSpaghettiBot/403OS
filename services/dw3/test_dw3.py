#!/usr/bin/env python3
"""Exercise imports using synthetic PE headers and opaque test assets only."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("dw3_setup", Path(__file__).with_name("dw3-setup.py"))
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def pe(machine=0x8664):
    header = bytearray(134)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3c, 128)
    header[128:132] = b"PE\0\0"
    struct.pack_into("<H", header, 132, machine)
    return header


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.media = self.base/"media"
        self.source = self.media/"DW3 Online"
        for directory in ["app", "runtime/bin", "data"]:
            (self.source/directory).mkdir(parents=True)
        for name in ["DW3 Online.exe", "app/dwcofre.dll", "runtime/bin/java.dll"]:
            (self.source/name).write_bytes(pe())
        (self.source/"app/client.jar").write_bytes(b"opaque jar fixture")
        (self.source/"data/data.pak").write_bytes(b"opaque data fixture")
        (self.source/"personal-account-export.txt").write_bytes(b"must remain on import media")
        self.apps = self.base/"apps"
        self.destination = self.apps/"dw3"
        for name, value in [("APPS", self.apps), ("DESTINATION", self.destination),
                            ("IMPORT_ROOTS", [self.media])]:
            context = patch.object(setup, name, value)
            context.start()
            self.addCleanup(context.stop)

    def do_import(self):
        with patch.object(setup, "guest_guard"), \
             patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=4*1024**3)), \
             contextlib.redirect_stdout(io.StringIO()):
            setup.import_client(self.source)

    def test_atomic_import_preserves_asset_bytes_and_excludes_root_personal_files(self):
        self.do_import()
        for name in setup.REQUIRED_FILES:
            self.assertEqual((self.source/name).read_bytes(), (self.destination/"client"/name).read_bytes())
        self.assertFalse((self.destination/"client/personal-account-export.txt").exists())
        self.assertEqual((self.destination/"client").stat().st_mode & 0o777, 0o755)
        for name in setup.REQUIRED_FILES:
            info = (self.destination/"client"/name).stat()
            self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), (1000, 1000, 0o644))
        receipt = json.loads((self.destination/"install.json").read_text())
        self.assertEqual(receipt["compatibility"], "UNTESTED")
        self.assertEqual(receipt["launcher_sha256"], hashlib.sha256(pe()).hexdigest())
        self.assertFalse(list(self.apps.glob(".dw3-import-*/")))

    def test_existing_application_is_preserved(self):
        self.destination.mkdir(parents=True)
        marker = self.destination/"existing"
        marker.write_bytes(b"owner data")
        with patch.object(setup, "copy_file", side_effect=AssertionError("must not copy")), \
             patch.object(setup, "assign_new_installation") as assigning:
            with self.assertRaisesRegex(setup.ImportError, "preserved"):
                self.do_import()
        assigning.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"owner data")

    def test_copy_failure_removes_staging_without_publishing(self):
        with patch.object(setup, "copy_file", side_effect=OSError("fixture copy failure")):
            with self.assertRaisesRegex(OSError, "fixture copy failure"):
                self.do_import()
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.apps.glob(".dw3-import-*/")))

    def test_32_bit_native_library_is_rejected(self):
        (self.source/"app/dwcofre.dll").write_bytes(pe(0x14c))
        with self.assertRaisesRegex(setup.ImportError, "x86-64"):
            setup.inventory(self.source)

    def test_linked_installation_root_is_rejected(self):
        real_app = self.media/"real-app"
        (self.source/"app").rename(real_app)
        (self.source/"app").symlink_to(real_app, target_is_directory=True)
        with self.assertRaisesRegex(setup.ImportError, "ordinary directories"):
            setup.inventory(self.source)

    def test_nested_linked_directory_is_rejected(self):
        (self.source/"app/linked").symlink_to(self.media, target_is_directory=True)
        with self.assertRaisesRegex(setup.ImportError, "Linked directories"):
            setup.inventory(self.source)

    def test_linked_file_is_rejected(self):
        (self.source/"data/linked").symlink_to(self.source/"data/data.pak")
        with self.assertRaisesRegex(setup.ImportError, "ordinary files"):
            setup.inventory(self.source)

    def test_copy_rejects_parent_replaced_with_link_after_inventory(self):
        setup.inventory(self.source)
        real_bin = self.media/"renamed-bin"
        (self.source/"runtime/bin").rename(real_bin)
        (self.source/"runtime/bin").symlink_to(real_bin, target_is_directory=True)
        with self.assertRaises(OSError):
            setup.copy_file(self.source, Path("runtime/bin/java.dll"), self.base/"copied.dll", len(pe()))
        self.assertFalse((self.base/"copied.dll").exists())

    def test_import_outside_allowed_media_is_rejected(self):
        outside = self.base/"outside"
        shutil.copytree(self.source, outside)
        with self.assertRaisesRegex(setup.ImportError, "mounted import media"):
            setup.inventory(outside)

    def test_low_space_rejects_import_without_publishing(self):
        with patch.object(setup, "guest_guard"), \
             patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(setup.ImportError, "512 MiB"):
                setup.import_client(self.source)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
