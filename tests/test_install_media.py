"""Self-contained image packaging: ordinary sparse files, no mounts/devices."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'external/board/surface-go1/pack-install-media.py'
SPEC = importlib.util.spec_from_file_location('install_media', SCRIPT)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)
HOST = ROOT/'output/host'


class InstallerMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.base = self.directory/'surface-go-base.img'
        with self.base.open('wb') as stream:
            stream.write(b'PRISTINE-BASE')
            stream.seek(8*1024**2-1)
            stream.write(b'!')

    def test_stage_is_one_sparse_hardlink_and_exact_sidecar_not_outer_image(self):
        expected = media.digest(self.base)
        root = self.directory/'data-root'
        # Ownership is exercised with real fakeroot/mke2fs in the integration test.
        with mock.patch.object(media,'root_owned'):
            folder = media.stage_payload(self.base,root,expected)
        embedded = folder/'surface-go.img'
        self.assertEqual(embedded.stat().st_ino,self.base.stat().st_ino)
        self.assertEqual(embedded.stat().st_blocks,self.base.stat().st_blocks)
        self.assertLess(embedded.stat().st_blocks*512,64*1024)
        self.assertEqual((folder/'surface-go.img.sha256').read_text(),expected+'  surface-go.img\n')
        self.assertEqual(sorted(p.name for p in root.rglob('*')),
                         ['SurfaceGoGamingOS','surface-go.img','surface-go.img.sha256'])
        self.assertEqual(media.digest(embedded),expected)

    def test_collision_symlink_and_dense_payload_budget_refused(self):
        stage = self.directory/'existing'
        stage.mkdir()
        with self.assertRaisesRegex(RuntimeError,'new directory'):
            media.stage_payload(self.base,stage,'a'*64)
        alias = self.directory/'alias'
        alias.symlink_to(self.base)
        with self.assertRaisesRegex(RuntimeError,'ordinary files'):
            media.stage_payload(alias,self.directory/'not-created','a'*64)
        with mock.patch.object(media,'DATA_BYTES',media.RESERVE):
            with self.assertRaisesRegex(RuntimeError,'DATA budget'):
                media.stage_payload(self.base,self.directory/'too-full','a'*64)
        self.assertFalse((self.directory/'too-full').exists())

    def test_config_points_only_to_generated_data_and_base_config_is_empty(self):
        template = ROOT/'external/board/surface-go1/genimage-usb.cfg'
        rendered = self.directory/'usb.cfg'
        media.render_config(template,rendered,self.directory/'data-root')
        text=rendered.read_text()
        self.assertIn('srcpath = "'+str(self.directory/'data-root')+'"',text)
        self.assertNotIn('@INSTALLER_DATA_ROOT@',text)
        base = (ROOT/'external/board/surface-go1/genimage.cfg').read_text()
        self.assertIn('image surface-go-base.img {',base)
        self.assertIn('empty = true',base)
        self.assertNotIn('srcpath',base)
        bad = self.directory/'bad-template'
        bad.write_text('no data root')
        with self.assertRaisesRegex(RuntimeError,'exactly one'):
            media.render_config(bad,rendered,self.directory/'data-root')

    def test_pristine_guard_rejects_nested_payload_or_private_data(self):
        for name in ('SurfaceGoGamingOS','home','tailscale'):
            listing='/2/040755/0/0/.//\n/2/040755/0/0/..//\n/11/040700/0/0/lost+found//\n/12/040755/0/0/'+name+'//\n'
            with mock.patch.object(media,'regular'),mock.patch.object(media,'debugfs',return_value=listing):
                with self.assertRaisesRegex(RuntimeError,'pristine'):
                    media.verify_pristine_data(Path('/unused'),Path('/unused'))

    @unittest.skipUnless(all((HOST/path).is_file() for path in ('bin/fakeroot','sbin/mke2fs','sbin/debugfs')),
                         'Pinned Buildroot filesystem tools unavailable')
    def test_real_sparse_large_file_import_preserves_size_root_ownership_and_permissions(self):
        # A >4 GiB logical file in a 64 MiB filesystem proves sparse import rather
        # than an apparent-size capacity assumption; only two pages allocate.
        large = self.directory/'large-base.img'
        with large.open('wb') as stream:
            stream.write(b'LARGE-PRISTINE-BASE')
            stream.seek(5*1024**3)
            stream.write(b'END')
        test = self.directory/'verify.py'
        test.write_text('''import importlib.util,os,subprocess,sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("media",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\nroot=Path(sys.argv[2]);base=Path(sys.argv[3]);image=Path(sys.argv[4]);tools=Path(sys.argv[5])\nm.stage_payload(base,root,"a"*64)\nsubprocess.run([str(tools/"sbin/mke2fs"),"-q","-t","ext4","-F","-d",str(root),str(image),"65536"],check=True)\nfor name in ("/SurfaceGoGamingOS","/SurfaceGoGamingOS/surface-go.img","/SurfaceGoGamingOS/surface-go.img.sha256"):\n print(m.debugfs(tools/"sbin/debugfs",image,"stat "+name))\n''')
        image = self.directory/'data.ext4'
        result = subprocess.run([str(HOST/'bin/fakeroot'),'-l',str(HOST/'lib/libfakeroot.so'),
            '-f',str(HOST/'bin/faked'),'--','python3',str(test),str(SCRIPT),
            str(self.directory/'large-stage'),str(large),str(image),str(HOST)],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Size: '+str(large.stat().st_size),result.stdout)
        self.assertRegex(result.stdout,r'Type:\s*regular\s+Mode:\s*0444')
        self.assertEqual(len(__import__('re').findall(r'User:\s*0\s+Group:\s*0\b',result.stdout)),3)
        self.assertLess(image.stat().st_blocks*512,16*1024**2)
        self.assertLess(large.stat().st_blocks*512,64*1024)


if __name__ == '__main__':
    unittest.main()
