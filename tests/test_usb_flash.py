"""Disposable files and mocked metadata only: never access a host block device."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flash = load('usb_flash', ROOT/'installer/flash-usb.py')
fixture = load('flash_gpt_fixture', ROOT/'tests/test_usb_plan.py')


class USBFlashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.plan = dict(schema='surface-full-usb-flash-v1', status='READY_FOR_EXECUTION',
            device='/dev/disk/by-id/usb-EXAMPLE_0123456789-0:0',
            image=str(flash.FILES/'Projects/example/surface-go.img'), image_sha256='a'*64,
            expected=dict(model='EXAMPLE USB', serial='0123456789', bytes=256*1024**3))
        self.disk = dict(path='/dev/sdz', type='disk', tran='usb', ro=False, size=256*1024**3,
            model='EXAMPLE USB', serial='0123456789', **{'log-sec':512, 'maj:min':'8:240'},
            mountpoints=[None], children=[dict(path='/dev/sdz1', type='part', **{'maj:min':'8:241'}, mountpoints=[None])])

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_usb_identity_required_even_with_same_size(self):
        flash.validate_disk(self.disk, self.plan['expected'], Path('/dev/sdz'))
        for key, wrong in [('tran','nvme'), ('model','OTHER'), ('serial','OTHER'), ('size',123),
                           ('type','part'), ('log-sec',4096), ('ro',True)]:
            disk = copy.deepcopy(self.disk)
            disk[key] = wrong
            with self.subTest(key=key), self.assertRaises(flash.FlashError):
                flash.validate_disk(disk, self.plan['expected'], Path('/dev/sdz'))

    def test_protected_mounts_and_mapped_children_rejected(self):
        for where in ('/', '/boot/efi', str(flash.FILES)):
            disk = copy.deepcopy(self.disk)
            disk['children'][0]['mountpoints'] = [where]
            with self.assertRaises(flash.FlashError):
                flash.validate_disk(disk, self.plan['expected'], Path('/dev/sdz'))
        self.disk['children'][0]['type'] = 'crypt'
        with self.assertRaises(flash.FlashError):
            flash.validate_disk(self.disk, self.plan['expected'], Path('/dev/sdz'))

    def test_plan_requires_usb_whole_by_id_and_exact_hash(self):
        flash.validate_plan(self.plan)
        for key, value in [('device','/dev/sdz'), ('device','/dev/disk/by-id/usb-EXAMPLE-part1'),
                           ('image_sha256','latest'), ('image','/tmp/image')]:
            bad = copy.deepcopy(self.plan)
            bad[key] = value
            with self.subTest(key=key,value=value), self.assertRaises(flash.FlashError):
                flash.validate_plan(bad)

    def test_confirmation_and_boot_evidence_checked_before_udisks(self):
        args = argparse.Namespace(expected_plan_sha256='b'*64, confirm='wrong', boot_result=None)
        with mock.patch.object(flash, 'UDisks') as backend:
            with self.assertRaises(flash.FlashError):
                flash.execute(self.plan, 'b'*64, args, -1, self.disk, None)
            args.confirm = 'ERASE ENTIRE USB '+self.plan['expected']['serial']
            with self.assertRaises(flash.FlashError):
                flash.execute(self.plan, 'b'*64, args, -1, self.disk, None)
        backend.assert_not_called()

    def test_wrong_source_sha_prevents_unmount_or_open(self):
        image = self.directory/'image'
        image.write_bytes(b'wrong image')
        args = argparse.Namespace(expected_plan_sha256='b'*64,
            confirm='ERASE ENTIRE USB '+self.plan['expected']['serial'], boot_result='mock')
        with image.open('rb') as source, mock.patch.object(flash,'check_boot', return_value='c'*64), \
             mock.patch.object(flash,'UDisks') as backend:
            with self.assertRaisesRegex(flash.FlashError, 'Compiled image SHA'):
                flash.execute(self.plan, 'b'*64, args, source.fileno(), self.disk, None)
        backend.assert_not_called()

    def test_mountinfo_can_block_even_when_lsblk_reports_unmounted(self):
        with mock.patch.object(Path,'read_text',return_value='22 1 8:241 / /media/old rw - ext4 /dev/sdz1 rw\n'):
            with self.assertRaisesRegex(flash.FlashError, 'mountinfo'):
                flash.require_unmounted(self.disk)

    def test_boot_evidence_requires_exact_image_and_clean_uefi(self):
        path = self.directory/'result.json'
        result = dict(image_sha256='a'*64, mode='live', direct_kernel_boot=False, status='PARTIAL',
            checks={name:'PASS' for name in flash.REQUIRED_BOOT_CHECKS})
        path.write_text(json.dumps(result))
        flash.check_boot(path, 'a'*64)
        for mutate in (lambda r:r.update(image_sha256='b'*64), lambda r:r.update(direct_kernel_boot=True),
                       lambda r:r['checks'].update(ui_clean_poweroff='FAIL')):
            bad = copy.deepcopy(result)
            mutate(bad)
            path.write_text(json.dumps(bad))
            with self.assertRaises(flash.FlashError):
                flash.check_boot(path, 'a'*64)

    def test_dense_copy_readback_valid_gpt_and_old_end_cleared(self):
        source, target = self.directory/'source.img', self.directory/'target.img'
        size, target_size = 8*1024**2, 16*1024**2
        fixture.create_disk(source, size, [(1, flash.gpt.ESP_TYPE,
            '11111111-2222-4333-8444-555555555555', 2048, 1024**2, 'fixture', 0)])
        with target.open('wb') as stream:
            stream.write(b'OLD!'*(target_size//4))
        with source.open('rb') as src, target.open('r+b') as dst:
            expected = flash.sha256(src.fileno(),size)
            actual = flash.copy_and_verify(src.fileno(),dst.fileno(),size,target_size,expected)
            self.assertEqual(actual,expected)
            self.assertEqual(os.pread(dst.fileno(),4096,2*1024**2),bytes(4096))
            self.assertEqual(os.pread(dst.fileno(),flash.TAIL,target_size-flash.TAIL),bytes(flash.TAIL))
            self.assertEqual(os.pread(dst.fileno(),4,size+4096),b'OLD!')
            flash.gpt.read_gpt(dst.fileno(),size)

    def test_readback_mismatch_and_non_block_descriptor_rejected(self):
        path = self.directory/'file'
        path.write_bytes(bytes(1024))
        with path.open('r+b') as stream:
            with self.assertRaisesRegex(flash.FlashError,'not the reviewed whole USB'):
                flash.validate_fd(stream.fileno(),self.disk)
        with mock.patch.object(flash,'pwrite_all'), mock.patch.object(os,'fsync'), \
             mock.patch.object(flash,'sha256',return_value='b'*64), \
             mock.patch.object(os,'pread',return_value=b'x'*1024), \
             mock.patch.object(os,'posix_fadvise'):
            with self.assertRaisesRegex(flash.FlashError,'readback SHA'):
                flash.copy_and_verify(4,5,1024,32*1024,'a'*64)

    def test_preview_never_instantiates_writable_backend(self):
        plan_path = self.directory/'plan.json'
        plan_path.write_text(json.dumps(self.plan))
        with mock.patch.object(os.path,'ismount',return_value=True), \
             mock.patch.object(flash,'load_json',return_value=(self.plan,'a'*64)), \
             mock.patch.object(flash,'inspect',return_value=(self.disk,None)), \
             mock.patch.object(flash,'regular',return_value=88), \
             mock.patch.object(os,'fstat',return_value=argparse.Namespace(st_size=1024)), \
             mock.patch.object(os,'close'), mock.patch.object(flash,'UDisks') as backend, \
             mock.patch('builtins.print'):
            self.assertEqual(flash.main(['--plan',str(plan_path)]),0)
        backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
