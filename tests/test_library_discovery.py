import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


discovery=module('discovery',ROOT/'services/library-discovery.py')
scanner=module('discovery_scanner',ROOT/'scripts/library-scan.py')


class DiscoveryTests(unittest.TestCase):
    def test_multiple_local_volumes_populate_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);first=base/'first';second=base/'second'
            first.mkdir();second.mkdir()
            (first/'A.gb').write_bytes(b'game')
            (second/'A.gb').write_bytes(b'game')
            (second/'B.gba').write_bytes(b'other')
            output=base/'output'
            result=discovery.refresh(scanner,[first,second],output,base/'cache')
            self.assertEqual(result['games'],2)
            self.assertEqual(len((output/'catalog.tsv').read_text().splitlines()),2)
            before=(output/'catalog.tsv').stat().st_mtime_ns
            discovery.refresh(scanner,[first,second],output,base/'cache')
            self.assertEqual(before,(output/'catalog.tsv').stat().st_mtime_ns)

    def test_disconnected_or_failed_volume_keeps_existing_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);output=base/'output';output.mkdir()
            catalog=output/'catalog.tsv';catalog.write_text('existing game\n')
            result=discovery.refresh(scanner,[],output,base/'cache')
            self.assertIsNone(result['games'])
            self.assertEqual(catalog.read_text(),'existing game\n')
            with patch.object(scanner,'scan',return_value=([],['read failed'])):
                with self.assertRaisesRegex(ValueError,'previous library was kept'):
                    discovery.refresh(scanner,[base],output,base/'cache')
            self.assertEqual(catalog.read_text(),'existing game\n')

    def test_host_guard_refuses_before_mounting_or_loading_scanner(self):
        if Path('/etc/surface-image-id').exists():self.skipTest('inside guest')
        with patch.object(discovery.subprocess,'run') as run:
            with self.assertRaises((OSError,ValueError)):
                discovery.once()
        run.assert_not_called()

    def test_failed_or_invalid_mount_inventory_preserves_catalog_before_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);output=base/'output';output.mkdir()
            catalog=output/'catalog.tsv';catalog.write_text('existing game\n')
            real_fstat=os.fstat
            def owned_fixture(descriptor):
                info=real_fstat(descriptor)
                return SimpleNamespace(st_mode=info.st_mode,st_uid=0)
            scenarios=[(1,dict(status='PARTIAL',errors=['fixture mount failed'])),
                       (0,dict(status='OK',errors=['fixture probe failed'])),
                       (0,dict(status='ERROR',errors=[])),(0,[]),(0,'malformed JSON')]
            for code,record in scenarios:
                with self.subTest(code=code,record=record), \
                     patch.object(discovery,'require_guest'),patch.object(discovery,'safe_directory'), \
                     patch.object(discovery,'OUTPUT',output),patch.object(discovery,'LOCK',base/'lock'), \
                     patch.object(discovery.os,'fstat',side_effect=owned_fixture), \
                     patch.object(discovery.subprocess,'run',return_value=SimpleNamespace(
                         returncode=code,stdout=record.encode() if isinstance(record,str) else json.dumps(record).encode())), \
                     patch.object(discovery.importlib.machinery,'SourceFileLoader') as loading:
                    with self.assertRaisesRegex(ValueError,'previous library was kept'):
                        discovery.once()
                    loading.assert_not_called()
                    self.assertEqual(catalog.read_text(),'existing game\n')

    def test_only_readonly_uuid_volume_mounts_are_discovered(self):
        mounted={
            '/media/library/0123-abcd':dict(options={'ro','nosuid','nodev','noexec'}),
            '/media/library/abcd-9999':dict(options={'rw','nosuid','nodev','noexec'}),
            '/media/library/../../host':dict(options={'ro','nosuid','nodev','noexec'}),
            '/run/media/host':dict(options={'ro','nosuid','nodev','noexec'}),
        }
        with patch.object(Path,'is_dir',return_value=True),patch.object(Path,'is_symlink',return_value=False), \
             patch.object(discovery,'safe_directory'):
            result=discovery.discovery_roots(mounted)
        self.assertEqual(result,[Path('/data/roms'),Path('/media/library/0123-abcd')])
