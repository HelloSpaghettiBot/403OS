import importlib.util
import contextlib
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
from unittest import mock
import urllib.request
import zipfile

spec = importlib.util.spec_from_file_location("pokemmo_setup", Path(__file__).with_name("pokemmo-setup.py"))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def client_zip(self, entries):
        archive = self.directory / "client.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name, contents in entries:
                output.writestr(name, contents)
        return archive

    def test_expected_launcher_and_files_extract(self):
        archive = self.client_zip([("PokeMMO/PokeMMO.sh", "#!/bin/sh\n"), ("PokeMMO/test.txt", "unchanged")])
        client = setup.extract_client(archive, self.directory / "unpacked")
        self.assertEqual((client / "test.txt").read_text(), "unchanged")

    def test_zip_traversal_rejected_before_any_extraction(self):
        archive = self.client_zip([("PokeMMO.sh", "ok"), ("../escaped", "bad")])
        with self.assertRaises(setup.SetupError):
            setup.extract_client(archive, self.directory / "unpacked")
        self.assertFalse((self.directory / "escaped").exists())
        self.assertFalse((self.directory / "unpacked").exists())

    def test_zip_symlink_rejected(self):
        link = zipfile.ZipInfo("PokeMMO.sh")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.client_zip([(link, "../../outside")])
        with self.assertRaises(setup.SetupError):
            setup.extract_client(archive, self.directory / "unpacked")

    def test_zip_duplicate_paths_rejected(self):
        archive = self.client_zip([("PokeMMO.sh", "ok"), ("./PokeMMO.sh", "bad")])
        with self.assertRaises(setup.SetupError):
            setup.extract_client(archive, self.directory / "unpacked")

    def test_untrusted_https_and_http_rejected(self):
        for url in ["http://dl.pokemmo.com/client.zip", "https://evil.example/client.zip",
                    "https://dl.pokemmo.com.evil.example/client.zip", "https://user@dl.pokemmo.com/client.zip"]:
            with self.subTest(url=url), self.assertRaises(setup.SetupError):
                setup.trusted_url(url, setup.CLIENT_HOSTS)
        setup.trusted_url(setup.CLIENT_URL, setup.CLIENT_HOSTS)

    def test_redirect_host_checked_before_request_is_created(self):
        handler = setup.TrustedRedirects(setup.CLIENT_HOSTS)
        with self.assertRaises(setup.SetupError):
            handler.redirect_request(urllib.request.Request(setup.CLIENT_URL), None, 302,
                                     "redirect", {}, "https://evil.example/client.zip")

    def test_tar_link_cannot_escape_destination(self):
        archive = self.directory / "jre.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            link = tarfile.TarInfo("jdk/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            output.addfile(link)
        with self.assertRaises(tarfile.TarError):
            setup.extract_runtime(archive, self.directory / "runtime")

    def test_existing_install_preserved_without_download(self):
        destination = self.directory / "pokemmo"
        destination.mkdir()
        owned = destination / "owned-save"
        owned.write_text("keep")
        with mock.patch.object(setup, "guest_guard"), mock.patch.object(setup, "APPS", self.directory), \
                mock.patch.object(setup, "DESTINATION", destination), mock.patch.object(setup, "download") as download:
            with self.assertRaises(setup.SetupError):
                setup.install()
            download.assert_not_called()
        self.assertEqual(owned.read_text(), "keep")

    def test_failed_download_removes_staging_and_leaves_no_install(self):
        destination = self.directory / "pokemmo"
        with mock.patch.object(setup, "guest_guard"), mock.patch.object(setup, "APPS", self.directory), \
                mock.patch.object(setup, "DESTINATION", destination), \
                mock.patch.object(setup.shutil, "disk_usage", return_value=mock.Mock(free=3 * setup.GIB)), \
                mock.patch.object(setup, "download", side_effect=setup.SetupError("test network failure")):
            with self.assertRaises(setup.SetupError):
                setup.install()
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".pokemmo-install-*")), [])


    def test_completed_staging_becomes_one_install_directory(self):
        destination = self.directory / "pokemmo"

        def fake_download(url, path, hosts, limit, expected_hash=None):
            if url == setup.CLIENT_URL:
                with zipfile.ZipFile(path, "w") as output:
                    output.writestr("PokeMMO.sh", "#!/bin/sh\nexit 0\n")
            else:
                with tarfile.open(path, "w:gz") as output:
                    java = tarfile.TarInfo("jdk/bin/java")
                    java.mode = 0o755
                    java.size = 4
                    output.addfile(java, io.BytesIO(b"test"))
            return "0" * 64

        with mock.patch.object(setup, "guest_guard"), mock.patch.object(setup, "APPS", self.directory), \
                mock.patch.object(setup, "DESTINATION", destination), \
                mock.patch.object(setup.shutil, "disk_usage", return_value=mock.Mock(free=3 * setup.GIB)), \
                mock.patch.object(setup, "download", side_effect=fake_download):
            setup.install()
        self.assertTrue((destination / "client/PokeMMO.sh").is_file())
        self.assertTrue((destination / "jre/bin/java").is_file())
        self.assertIn(setup.JRE_SHA256, (destination / "install.json").read_text())
        self.assertEqual(list(self.directory.glob(".pokemmo-install-*")), [])


class RomImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'owned-files'
        self.source.mkdir()
        self.destination = self.base / 'pokemmo'
        (self.destination / 'client/roms').mkdir(parents=True)
        (self.destination / 'client/PokeMMO.sh').write_text('synthetic fixture only')
        self.patches = [mock.patch.object(setup, 'ROM_ROOTS', (self.source,)),
                        mock.patch.object(setup, 'DESTINATION', self.destination)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def gba(self, name='misleading-name.gba', code=b'BPRE', title=b'POKEMON FIRE'):
        # No game data or Nintendo logo is embedded in these sparse fixtures.
        path = self.source / name
        header = bytearray(512)
        header[0xa0:0xac] = title.ljust(12, b'\0')
        header[0xac:0xb0] = code
        header[0xb0:0xb2] = b'01'
        header[0xb2] = 0x96
        header[0xbd] = (-sum(header[0xa0:0xbd]) - 0x19) & 255
        with path.open('wb') as output:
            output.write(header)
            output.truncate(16 * 1024 ** 2)
        return path

    def nds(self, name='also-misleading.nds', code=b'IRBO', title=b'POKEMON B'):
        path = self.source / name
        header = bytearray(512)
        header[:12] = title.ljust(12, b'\0')
        header[12:16] = code
        header[16:18] = b'01'
        header[18] = 2
        struct.pack_into('<II', header, 0x80, 65536, 16384)
        for offset in (0x20, 0x30):
            struct.pack_into('<I', header, offset, 16384)
            struct.pack_into('<I', header, offset + 12, 4)
        struct.pack_into('<H', header, 0x15e, setup.crc16(header[:0x15e]))
        with path.open('wb') as output:
            output.write(header)
            output.truncate(64 * 1024 ** 2)
        return path

    def import_fixture(self, path):
        with mock.patch.object(setup, 'guest_guard'), contextlib.redirect_stdout(io.StringIO()):
            setup.import_rom(path)

    def test_header_identity_overrides_filename_and_rejects_other_games(self):
        firered = self.gba('Pokemon Black.nds')
        black = self.nds('Pokemon FireRed.gba')
        self.assertEqual(setup.inspect_rom(firered)['game'], 'FireRed')
        self.assertTrue(setup.inspect_rom(black)['required'])
        self.gba('not-approved.gba', b'AXVE', b'POKEMON RUBY')
        self.nds('not-approved.nds', b'IREO', b'POKEMON B2')
        result = setup.scan_roms(self.source)
        self.assertEqual({item['game'] for item in result['roms']}, {'Black', 'FireRed'})
        self.assertEqual(len(result['warnings']), 2)

    def test_bad_header_checksum_and_truncated_geometry_rejected(self):
        gba = self.gba()
        with gba.open('r+b') as output:
            output.seek(0xbd)
            output.write(b'\0')
        with self.assertRaisesRegex(setup.SetupError, 'checksum'):
            setup.inspect_rom(gba)
        nds = self.nds()
        with nds.open('r+b') as output:
            output.truncate(1024)
        with self.assertRaisesRegex(setup.SetupError, 'size'):
            setup.inspect_rom(nds)

    def test_atomic_private_copy_preserves_source_and_exact_duplicate(self):
        path = self.gba()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.import_fixture(path)
        target = self.destination / 'client/roms/FireRed-BPRE-rev0.gba'
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        original_inode = target.stat().st_ino
        self.import_fixture(path)
        self.assertEqual(target.stat().st_ino, original_inode)
        self.assertFalse(setup.rom_status()['black_white_present'])
        self.assertEqual(list(target.parent.glob('.rom-import-*')), [])

    def test_different_existing_rom_is_always_preserved(self):
        path = self.gba()
        self.import_fixture(path)
        target = self.destination / 'client/roms/FireRed-BPRE-rev0.gba'
        previous = target.read_bytes()
        with path.open('r+b') as output:
            output.seek(1024)
            output.write(b'different owner file')
        with self.assertRaisesRegex(setup.SetupError, 'Existing files were preserved'):
            self.import_fixture(path)
        self.assertEqual(target.read_bytes(), previous)

    def test_symlinks_outside_roots_and_pipes_are_rejected(self):
        original = self.gba()
        link = self.source / 'alias.gba'
        link.symlink_to(original)
        with self.assertRaisesRegex(setup.SetupError, 'symbolic links'):
            setup.inspect_rom(link)
        outside = self.base / 'outside.gba'
        outside.write_bytes(b'fixture')
        with self.assertRaisesRegex(setup.SetupError, 'ROM sources must be below'):
            setup.inspect_rom(outside)
        pipe = self.source / 'pipe.gba'
        os.mkfifo(pipe)
        with self.assertRaisesRegex(setup.SetupError, 'regular file'):
            setup.inspect_rom(pipe)
        with self.assertRaisesRegex(setup.SetupError, 'parent traversal'):
            setup.inspect_rom(self.source / '../outside.gba')

    def test_changed_source_cleans_staging_without_publishing(self):
        path = self.gba()
        real_hash = setup.hash_descriptor
        def changed(descriptor, size, phase, output=None):
            result = real_hash(descriptor, size, phase, output)
            if phase == 'Copying selected ROM':
                with path.open('r+b') as source:
                    source.seek(1024)
                    source.write(b'changed during copy')
            return result
        with mock.patch.object(setup, 'hash_descriptor', side_effect=changed):
            with self.assertRaisesRegex(setup.SetupError, 'source changed'):
                self.import_fixture(path)
        self.assertEqual(list((self.destination / 'client/roms').iterdir()), [])

    def test_host_guard_precedes_any_import_writable_open(self):
        path = self.gba()
        with mock.patch.object(setup, 'IMAGE_MARKER', self.base / 'absent'), \
             mock.patch.object(setup.os, 'open', side_effect=AssertionError('No file open before guard')):
            with self.assertRaisesRegex(setup.SetupError, 'only inside'):
                setup.import_rom(path)



if __name__ == "__main__":
    unittest.main()
