import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('library_scan', ROOT / 'scripts/library-scan.py')
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.roms = self.base / 'roms'
        self.roms.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def rom(self, name, data=b'game data'):
        path = self.roms / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_recursive_dedupe_preserves_distinct_title_and_size(self):
        self.rom('Super Mario World (USA).sfc')
        self.rom('duplicate/Super Mario World (USA).sfc')
        self.rom('different-size/Super Mario World (USA).sfc', b'other data!')
        self.rom('Super Mario World 2 (USA).sfc')
        self.rom('firmware.23.1/Mario.gba')
        self.rom('ps2-bios-usa/fake.iso')
        self.rom('prod-keys/fake.gba')
        games, errors = scanner.scan(self.roms)
        self.assertEqual(len(games), 3)
        self.assertEqual(errors, [])
        self.assertEqual({game['system_id'] for game in games}, {'snes'})
        self.assertEqual(len({game['id'] for game in games}), 3)

    def test_iso_magic_and_unknown_container(self):
        self.rom('Cube.nkit.iso', b'\0' * 28 + bytes.fromhex('c2339f3d'))
        self.rom('Wii.iso', b'\0' * 24 + bytes.fromhex('5d1c9ea3') + b'\0' * 4)
        self.rom('Unknown.iso', b'unknown')
        self.rom('Unknown.chd', b'unknown')
        games, _ = scanner.scan(self.roms)
        systems = {game['title']: game['system_id'] for game in games}
        self.assertEqual(systems['Cube'], 'gamecube')
        self.assertEqual(systems['Wii'], 'wii')
        self.assertEqual(systems['Unknown'], 'unknown')

    def test_sidecar_conversion_and_no_network_by_default(self):
        from PIL import Image
        rom = self.rom('Sonic Advance 3 (Europe) (En,Ja,Fr,De,Es,It).gba')
        cover = rom.with_suffix('.png')
        Image.new('RGB', (20, 30), (30, 70, 110)).save(cover)
        original = rom.read_bytes()
        output = self.base / 'out'
        output.mkdir()
        games, errors = scanner.scan(self.roms)
        with patch.object(scanner, 'request_bytes', side_effect=AssertionError('network is opt-in')):
            errors += scanner.prepare_covers(games, output, self.base / 'cache', False)
        scanner.write_catalog(games, errors, self.roms, output)
        self.assertEqual(rom.read_bytes(), original)
        texture = Path(games[0]['cover_texture_path']).read_bytes()
        self.assertEqual(texture[:8], b'SGOTEX01')
        self.assertEqual(struct.unpack('<II', texture[8:16]), (400, 480))
        self.assertEqual(len(texture), 16 + 400 * 480 * 4)
        self.assertEqual(len((output / 'catalog.tsv').read_text().splitlines()), 1)
        self.assertEqual(len((output / 'catalog.tsv').read_text().rstrip('\n').split('\t')), 5)

    def test_title_matching_never_substitutes_sequel_or_hack(self):
        game = {'title': 'Sonic Advance 3', 'rom_path': '/games/Sonic Advance 3 (Europe).gba'}
        self.assertIsNone(scanner.select_cover(game, ['Named_Boxarts/Sonic Advance 2 (USA).png']))
        self.assertEqual(scanner.select_cover(game, ['Named_Boxarts/Sonic Advance 3 (USA).png', 'Named_Boxarts/Sonic Advance 3 (Europe) (En,Ja,Fr,De,Es,It).png']), 'Named_Boxarts/Sonic Advance 3 (Europe) (En,Ja,Fr,De,Es,It).png')
        self.assertEqual(scanner.display_title('Wolverine - Feral Instinct (SMW Hack)'), 'Wolverine - Feral Instinct (SMW Hack)')

    def test_existing_cover_is_preserved_during_offline_refresh(self):
        self.rom('A.gba')
        games,_=scanner.scan(self.roms)
        output=self.base/'output';(output/'covers').mkdir(parents=True)
        texture=output/'covers'/(games[0]['id']+'.sgo')
        content=b'SGOTEX01'+struct.pack('<II',400,480)+bytes(400*480*4)
        texture.write_bytes(content)
        with patch.object(scanner,'convert_cover',side_effect=AssertionError('cached cover must survive')), \
             patch.object(scanner,'request_bytes',side_effect=AssertionError('offline')):
            self.assertEqual(scanner.prepare_covers(games,output,self.base/'cache',False),[])
        self.assertEqual(games[0]['cover_texture_path'],str(texture))
        self.assertEqual(texture.read_bytes(),content)

    def test_atomic_catalog_keeps_mtime_when_unchanged_and_refuses_symlink(self):
        self.rom('A.gb');games,errors=scanner.scan(self.roms)
        output=self.base/'output';output.mkdir()
        scanner.write_catalog(games,errors,self.roms,output)
        before=(output/'catalog.tsv').stat()
        scanner.write_catalog(games,errors,self.roms,output)
        after=(output/'catalog.tsv').stat()
        self.assertEqual((before.st_ino,before.st_mtime_ns),(after.st_ino,after.st_mtime_ns))
        (output/'catalog.tsv').unlink();(output/'catalog.tsv').symlink_to(self.roms/'A.gb')
        with self.assertRaisesRegex(ValueError,'symlink'):
            scanner.write_catalog(games,errors,self.roms,output)

    def test_scan_limit_preserves_existing_catalog(self):
        self.rom('A.gb');self.rom('B.gb')
        with self.assertRaisesRegex(ValueError,'scan limit'):
            scanner.scan(self.roms,maximum_entries=1)

    def test_flat_directory_deadline_is_checked_between_files(self):
        self.rom('A.gba');self.rom('B.gba')
        with patch.object(scanner.time,'monotonic',side_effect=[0,0,10]), \
             patch.object(scanner,'identify_system',return_value='gba') as identify:
            with self.assertRaisesRegex(ValueError,'previous library was kept'):
                scanner.scan(self.roms,deadline=5)
        self.assertEqual(identify.call_count,1)

    def test_unreadable_file_reports_error_and_keeps_other_games(self):
        self.rom('Denied.iso')
        self.rom('Working.gba')
        original = scanner.identify_system
        def identify(path):
            if path.name == 'Denied.iso':
                raise PermissionError('fixture read denied')
            return original(path)
        with patch.object(scanner, 'identify_system', side_effect=identify):
            games, errors = scanner.scan(self.roms)
        self.assertEqual([game['title'] for game in games], ['Working'])
        self.assertEqual(len(errors), 1)
        self.assertIn('fixture read denied', errors[0])

    def test_missing_root_output_in_library_symlinks_and_bad_paths(self):
        self.assertEqual(scanner.main(['--root', str(self.base / 'absent')]), 2)
        self.assertEqual(scanner.main(['--root', str(self.roms), '--output', str(self.roms / 'out')]), 2)
        self.assertEqual(scanner.main(['--root', str(self.roms), '--cache', str(self.roms / 'cache'), '--fetch-covers']), 2)
        target = self.rom('Real.gba')
        (self.roms / 'Link.gba').symlink_to(target)
        self.rom('Tab\tName.gba')
        self.rom('empty.gba', b'')
        games, errors = scanner.scan(self.roms)
        self.assertEqual(len(games), 1)
        self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
