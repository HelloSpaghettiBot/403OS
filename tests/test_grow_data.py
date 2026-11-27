"""Metadata fixtures and sfdisk regular-file checks only. Never host block writes."""
import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('grow_data', ROOT / 'services/grow-data.py')
grow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grow)
ARTIFACTS = ROOT / 'artifacts/tests/grow-data'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
DISK_BYTES = 64 * 1024 ** 3
DISK_GUID = 'ee980000-273d-4660-b02d-0d778ea4b403'
EFI_UUID = 'ee980001-273d-4660-b02d-0d778ea4b403'
FS_UUID = 'ee980002-273d-4660-b02d-0d778ea4b403'
DATA_START = 4327424
DATA_END = 12716031


def header(current, backup, table_lba, last, checksum):
    value = bytearray(512)
    struct.pack_into('<8sIIIIQQQQ16sQIII', value, 0, b'EFI PART', 0x10000, 92, 0, 0,
                     current, backup, 34, last, uuid.UUID(DISK_GUID).bytes_le,
                     table_lba, 128, 128, checksum)
    struct.pack_into('<I', value, 16, zlib.crc32(value[:92]) & 0xffffffff)
    return value


def fixture(path, relocated=False, extra=False, root_uuid=None, data_uuid=None):
    """Sparse regular file: no loop devices, root commands, or mounted images."""
    end = DISK_BYTES // 512 - 1 if relocated else DATA_END + 40
    parts = [
        (grow.EFI_TYPE, EFI_UUID, 2048, 133119, 4, 'boot'),
        (grow.ROOT_TYPE, root_uuid or grow.ROOT_UUID, 133120, 4327423, 0, 'root'),
        (grow.DATA_TYPE, data_uuid or grow.DATA_UUID, DATA_START, DATA_END, 0, 'data')]
    if extra:
        parts.append((grow.DATA_TYPE, str(uuid.uuid4()), DATA_END + 1, DATA_END + 100, 0, 'unknown'))
        end = DATA_END + 140
    table = bytearray(128 * 128)
    for index, (kind, identity, first, last, attributes, name) in enumerate(parts):
        entry = bytearray(128)
        struct.pack_into('<16s16sQQQ', entry, 0, uuid.UUID(kind).bytes_le, uuid.UUID(identity).bytes_le, first, last, attributes)
        encoded = name.encode('utf-16le');entry[56:56 + len(encoded)] = encoded
        table[index * 128:(index + 1) * 128] = entry
    checksum = zlib.crc32(table) & 0xffffffff
    mbr = bytearray(512);mbr[450] = 0xEE
    struct.pack_into('<II', mbr, 454, 1, min(end, 0xffffffff));mbr[510:] = b'\x55\xaa'
    superblock = bytearray(1024)
    struct.pack_into('<I', superblock, 4, grow.ORIGINAL_DATA_BYTES // 4096)
    struct.pack_into('<I', superblock, 0x18, 2)
    struct.pack_into('<HH', superblock, 0x38, 0xEF53, 1)
    superblock[0x68:0x78] = uuid.UUID(FS_UUID).bytes
    superblock[0x78:0x84] = b'SURFACE_DATA'
    with path.open('w+b') as stream:
        stream.truncate(DISK_BYTES)
        for offset, value in [(0, mbr), (512, header(1, end, 2, end - 33, checksum)),
                              (1024, table), ((end - 32) * 512, table),
                              (end * 512, header(end, 1, end - 32, end - 33, checksum)),
                              (DATA_START * 512 + 1024, superblock)]:
            stream.seek(offset);stream.write(value)
        # Payload probes are safely inside the original three partitions.
        for offset in (2048 * 512 + 4096, 133120 * 512 + 4096,
                       DATA_START * 512 + 8192, DATA_END * 512 - 4096):
            stream.seek(offset);stream.write(b'ORIGINAL PAYLOAD / DO NOT MOVE OR WIPE\0' * 64)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise AssertionError('Fixture is not a regular file')


def inspect(path):
    with path.open('rb') as stream:
        gpt = grow.read_gpt(stream.fileno(), DISK_BYTES)
        filesystem = grow.read_ext4(stream.fileno(), gpt['parts'][2], pristine=True)
        return gpt, filesystem


def target():
    return dict(target='/dev/vdb', name='vdb', stable_id=grow.VM_TARGET_ID,
                model='QEMU virtio disposable installation target', bytes=DISK_BYTES,
                logical_sector=512, devnum='252:16', profile='vm-install')


class DataGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.base = Path(self.temp.name)
        self.image = self.base / 'fixture.img'
        fixture(self.image)

    def tearDown(self):
        self.temp.cleanup()

    def test_system_only_inspection_preserves_pending_internal_journal(self):
        with self.image.open('r+b') as stream:
            offset = DATA_START * 512 + 1024
            stream.seek(offset + 0x5c)
            stream.write(struct.pack('<II', 4, 4))  # Has journal; needs replay.
            stream.flush()
            gpt = grow.read_gpt(stream.fileno(), DISK_BYTES)
            before = os.pread(stream.fileno(), 1024, offset)
            with self.assertRaises(grow.Refused):
                grow.read_ext4(stream.fileno(), gpt['parts'][2])
            result = grow.read_ext4(stream.fileno(), gpt['parts'][2], allow_journal_recovery=True)
            self.assertTrue(result['journal_recovery_pending'])
            self.assertEqual(before, os.pread(stream.fileno(), 1024, offset))
            with self.assertRaises(grow.Refused):
                grow.read_ext4(stream.fileno(), gpt['parts'][2], pristine=True, allow_journal_recovery=True)
            for state, compatible, incompatible in ((2, 4, 4), (1, 0, 4), (1, 4, 12)):
                stream.seek(offset + 0x3a); stream.write(struct.pack('<H', state))
                stream.seek(offset + 0x5c); stream.write(struct.pack('<II', compatible, incompatible))
                stream.flush()
                with self.assertRaises(grow.Refused):
                    grow.read_ext4(stream.fileno(), gpt['parts'][2], allow_journal_recovery=True)

    def test_plan_is_read_only_reaches_last_usable_and_binds_target_and_gpt(self):
        before = self.image.stat().st_mtime_ns
        gpt, filesystem = inspect(self.image)
        plan = grow.plan_grow(target(), gpt, filesystem)
        self.assertEqual(self.image.stat().st_mtime_ns, before)
        self.assertEqual(plan['data_start_lba'], DATA_START)
        self.assertEqual(plan['old_data_bytes'], 4 * 1024 ** 3)
        self.assertEqual(plan['new_data_end_lba'], DISK_BYTES // 512 - 34)
        self.assertGreater(plan['new_data_bytes'], 50 * 1024 ** 3)
        self.assertEqual(plan['review_token'], grow.plan_grow(target(), gpt, filesystem)['review_token'])
        changed = target();changed['stable_id'] = 'CHANGED'
        self.assertNotEqual(plan['review_token'], grow.plan_grow(changed, gpt, filesystem)['review_token'])
        changed_gpt = copy.deepcopy(gpt);changed_gpt['fingerprint'] = '0' * 64
        self.assertNotEqual(plan['review_token'], grow.plan_grow(target(), changed_gpt, filesystem)['review_token'])
        self.assertNotIn('--force', grow.sfdisk_arguments('/dev/vdb'))
        self.assertNotIn('--no-reread', grow.sfdisk_arguments('/dev/vdb'))

    def test_both_crc_copies_and_all_partition_identities_are_required(self):
        for offset in (512 + 16, (DATA_END + 40) * 512 + 16, 1024 + 40, (DATA_END + 8) * 512 + 40):
            with self.subTest(offset=offset):
                fixture(self.image)
                with self.image.open('r+b') as stream:
                    stream.seek(offset);value = stream.read(1);stream.seek(offset);stream.write(bytes([value[0] ^ 1]))
                with self.assertRaises(grow.Refused):
                    inspect(self.image)
        fixture(self.image, extra=True)
        with self.assertRaisesRegex(grow.Refused, 'three-partition'):
            inspect(self.image)

    def test_live_media_identifiers_are_readable_but_never_a_growth_target(self):
        root_uuid, data_uuid = str(uuid.uuid4()), str(uuid.uuid4())
        fixture(self.image, root_uuid=root_uuid, data_uuid=data_uuid)
        before = self.image.stat().st_mtime_ns
        with self.image.open('rb') as stream:
            media = grow.read_media_gpt(stream.fileno(), DISK_BYTES)
            self.assertEqual(media['parts'][1]['uuid'], root_uuid)
            self.assertEqual(media['parts'][2]['uuid'], data_uuid)
            filesystem = grow.read_ext4(stream.fileno(), media['parts'][2], pristine=True)
            with self.assertRaisesRegex(grow.Refused, 'partition UUIDs'):
                grow.read_gpt(stream.fileno(), DISK_BYTES)
        with self.assertRaisesRegex(grow.Refused, 'installed root/DATA'):
            grow.plan_grow(target(), media, filesystem)
        self.assertEqual(self.image.stat().st_mtime_ns, before)
        # Relaxing identity for a read-only source must not relax GPT integrity.
        with self.image.open('r+b') as stream:
            stream.seek(1024 + 40);stream.write(b'CORRUPT')
        with self.image.open('rb') as stream:
            with self.assertRaisesRegex(grow.Refused, 'CRC'):
                grow.read_media_gpt(stream.fileno(), DISK_BYTES)

    def test_wrong_data_uuid_root_entry_and_moved_start_are_refused(self):
        gpt, _ = inspect(self.image)
        for relative, new in ((128 + 16, uuid.uuid4().bytes_le),
                              (2 * 128 + 16, uuid.uuid4().bytes_le),
                              (2 * 128 + 32, struct.pack('<Q', DATA_START + 1))):
            with self.subTest(relative=relative):
                fixture(self.image)
                table = bytearray(gpt['table']);table[relative:relative + len(new)] = new
                end = DATA_END + 40;checksum = zlib.crc32(table) & 0xffffffff
                with self.image.open('r+b') as stream:
                    for offset, value in [(1024, table), ((end - 32) * 512, table),
                                          (512, header(1, end, 2, end - 33, checksum)),
                                          (end * 512, header(end, 1, end - 32, end - 33, checksum))]:
                        stream.seek(offset);stream.write(value)
                with self.assertRaises(grow.Refused):
                    inspect(self.image)

    def test_already_mounted_dirty_wrong_label_and_non_original_data_refused(self):
        for relative, value in ((0x2c, struct.pack('<I', 10)), (0x34, struct.pack('<H', 1)),
                                (0x3a, struct.pack('<H', 2)), (0x60, struct.pack('<I', 4)),
                                (0x78, b'OTHER_VOLUME'), (4, struct.pack('<I', 900000))):
            with self.subTest(relative=relative):
                fixture(self.image)
                with self.image.open('r+b') as stream:
                    stream.seek(DATA_START * 512 + 1024 + relative);stream.write(value)
                with self.assertRaises(grow.Refused):
                    inspect(self.image)

    def sys_disk(self, name='vdb', transport='virtio0', serial=grow.VM_TARGET_ID, removable='0'):
        sys = self.base / 'sys'
        sys.mkdir(exist_ok=True)
        physical = self.base / 'devices/pci0000:00' / transport / name
        physical.mkdir(parents=True)
        (sys / name).symlink_to(physical, target_is_directory=True)
        for key, value in {'serial': serial, 'size': str(DISK_BYTES // 512), 'dev': '252:16',
                           'queue/logical_block_size': '512', 'removable': removable,
                           'ro': '0', 'device/model': 'Fixture SSD'}.items():
            file = physical / key;file.parent.mkdir(parents=True, exist_ok=True);file.write_text(value)
        (physical / 'holders').mkdir()
        return sys, physical

    def info(self, sys, profile='vm-install', mounts='', swaps='', dmi=grow.VM_DMI, cmdline='surface.vm-install=1', identity=grow.VM_TARGET_ID):
        return grow.inspect_target('vdb', profile, identity, mounts, swaps, dmi, cmdline, sys, self.base / 'dev')

    def test_exact_vm_guard_and_physical_usb_refusal(self):
        sys, physical = self.sys_disk()
        self.assertEqual(self.info(sys)['stable_id'], grow.VM_TARGET_ID)
        for options in ({'dmi': 'Other VM'}, {'cmdline': ''}, {'identity': 'OTHER'}, {'profile': 'physical-internal'}):
            with self.subTest(options=options), self.assertRaises(grow.Refused):
                self.info(sys, **options)
        (physical / 'removable').write_text('1')
        with self.assertRaisesRegex(grow.Refused, 'Removable'):
            self.info(sys)
        (physical / 'removable').write_text('0')
        (self.base / 'sys/vdb').unlink()
        usb = physical.parent.parent / 'usb1' / 'vdb';usb.parent.mkdir();physical.rename(usb)
        (sys / 'vdb').symlink_to(usb, target_is_directory=True)
        with self.assertRaisesRegex(grow.Refused, 'USB transport'):
            self.info(sys, profile='physical-internal')

    def test_mounted_descendant_swap_and_4kn_refused(self):
        sys, physical = self.sys_disk()
        partition = physical / 'vdb3';partition.mkdir();(partition / 'partition').write_text('3');(partition / 'dev').write_text('252:19');(partition / 'holders').mkdir()
        (sys / 'vdb3').symlink_to(partition, target_is_directory=True)
        with self.assertRaisesRegex(grow.Refused, 'mounted'):
            self.info(sys, mounts='22 1 252:19 / /data rw - ext4 /dev/vdb3 rw\n')
        with self.assertRaisesRegex(grow.Refused, 'swap'):
            self.info(sys, swaps='Filename Type Size Used Priority\n/dev/vdb3 partition 1 0 -2\n')
        (physical / 'queue/logical_block_size').write_text('4096')
        with self.assertRaisesRegex(grow.Refused, '512-byte'):
            self.info(sys)

    def test_preservation_comparison_rejects_every_non_end_entry_change(self):
        before, _ = inspect(self.image)
        after = copy.deepcopy(before);data = bytearray(after['table'])
        end = DISK_BYTES // 512 - 34;struct.pack_into('<Q', data, 2 * 128 + 40, end)
        after['table'] = bytes(data)
        grow.verify_preserved(before, after, end)
        for offset in (0, 16, 32, 128 + 16, 2 * 128 + 32, 2 * 128 + 48, 4 * 128):
            changed = copy.deepcopy(after);data = bytearray(changed['table']);data[offset] ^= 1;changed['table'] = bytes(data)
            with self.subTest(offset=offset), self.assertRaisesRegex(grow.Refused, 'preserved'):
                grow.verify_preserved(before, changed, end)

    def test_host_guard_precedes_every_open_command_and_discovery(self):
        arguments = ['--target', '/dev/vdb', '--profile', 'vm-install', '--expected-target-id', grow.VM_TARGET_ID]
        with patch.object(grow, 'MARKER', self.base / 'missing-marker'), \
             patch.object(grow.os, 'open', side_effect=AssertionError('No writable/read device open before refusal')), \
             patch.object(grow, 'inspect_plan', side_effect=AssertionError('No target discovery before refusal')), \
             patch.object(grow, 'run', side_effect=AssertionError('No utility before refusal')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(grow.main(['--plan', *arguments]), 2)
            self.assertEqual(grow.main(['--execute', *arguments, '--review-token', '0' * 64]), 2)
        self.assertIn('Refusing host execution', output.getvalue())

    def test_wrong_review_token_never_reaches_utilities_or_lock_open(self):
        gpt, filesystem = inspect(self.image);plan = grow.plan_grow(target(), gpt, filesystem)
        arguments = argparse.Namespace(target='/dev/vdb', profile='vm-install', expected_target_id=grow.VM_TARGET_ID, review_token='0' * 64)
        with patch.object(grow, 'require_guest'), patch.object(grow, 'inspect_plan', return_value=(target(), gpt, filesystem, plan)), \
             patch.object(grow.os, 'open', side_effect=AssertionError('No lock/device open before reviewed token')), \
             patch.object(grow, 'run', side_effect=AssertionError('No utility before reviewed token')):
            with self.assertRaisesRegex(grow.Refused, 'review token'):
                grow.execute(arguments)

    def test_final_filesystem_uses_validated_partition_alias_instead_of_stale_whole_disk(self):
        gpt, original = inspect(self.image)
        partition = dict(gpt['parts'][2], bytes=DISK_BYTES-DATA_START*512-33*512)
        expected = partition['bytes']//4096*4096
        with self.image.open('rb') as stream:
            superblock = bytearray(os.pread(stream.fileno(),1024,DATA_START*512+1024))
        struct.pack_into('<I',superblock,4,expected//4096)
        alias = self.base/'partition-alias.img';alias.write_bytes(bytes(1024)+superblock)
        opened=[]
        def open_alias(identity):
            self.assertEqual(identity,dict(target='/dev/vdb3',devnum='252:19',bytes=partition['bytes']))
            self.assertTrue(stat.S_ISREG(alias.stat().st_mode))
            descriptor=os.open(alias,os.O_RDONLY|os.O_CLOEXEC);opened.append(descriptor);return descriptor
        with patch.object(grow,'data_device',return_value='/dev/vdb3'), \
             patch.object(grow,'text',return_value='252:19'), \
             patch.object(grow,'open_readonly_target',side_effect=open_alias):
            final=grow.read_data_filesystem(target(),partition,'/dev/vdb3')
        self.assertEqual(final['bytes'],expected);self.assertEqual(final['uuid'],original['uuid'])
        self.assertEqual(inspect(self.image)[1]['bytes'],grow.ORIGINAL_DATA_BYTES)
        with self.assertRaises(OSError):os.fstat(opened[0])

    def test_partition_alias_identity_changes_refuse_before_read_and_close_descriptor(self):
        partition=inspect(self.image)[0]['parts'][2]
        with patch.object(grow,'data_device',return_value='/dev/changed'), \
             patch.object(grow,'open_readonly_target',side_effect=AssertionError('No open after changed partition')):
            with self.assertRaisesRegex(grow.Refused,'DATA device changed'):
                grow.read_data_filesystem(target(),partition,'/dev/vdb3')
        for devices,numbers in ((['/dev/vdb3','/dev/changed'],['252:19']),
                                 (['/dev/vdb3','/dev/vdb3'],['252:19','252:20'])):
            descriptor=os.open(self.image,os.O_RDONLY|os.O_CLOEXEC)
            with patch.object(grow,'data_device',side_effect=devices),patch.object(grow,'text',side_effect=numbers), \
                 patch.object(grow,'open_readonly_target',return_value=descriptor), \
                 patch.object(grow,'read_ext4',side_effect=AssertionError('No metadata read after identity changed')):
                with self.assertRaisesRegex(grow.Refused,'identity changed'):
                    grow.read_data_filesystem(target(),partition,'/dev/vdb3')
            with self.assertRaises(OSError):os.fstat(descriptor)

    def test_partition_descriptor_requires_exact_block_identity_capacity_and_readonly_open(self):
        identity=dict(target='/dev/vdb3',devnum='252:19',bytes=66503818752)
        for kind,number,size,valid in ((stat.S_IFBLK,os.makedev(252,19),identity['bytes'],True),
                                      (stat.S_IFREG,os.makedev(252,19),identity['bytes'],False),
                                      (stat.S_IFBLK,os.makedev(252,20),identity['bytes'],False),
                                      (stat.S_IFBLK,os.makedev(252,19),identity['bytes']-512,False)):
            with self.subTest(kind=kind,number=number,size=size):
                def geometry(descriptor,operation,buffer):
                    self.assertEqual((descriptor,operation),(123,grow.BLKGETSIZE64))
                    struct.pack_into('<Q',buffer,0,size)
                with patch.object(grow.os,'open',return_value=123) as opening, \
                     patch.object(grow.os,'fstat',return_value=SimpleNamespace(st_mode=kind,st_rdev=number)), \
                     patch.object(grow.fcntl,'ioctl',side_effect=geometry),patch.object(grow.os,'close') as closing:
                    if valid:
                        self.assertEqual(grow.open_readonly_target(identity),123);closing.assert_not_called()
                    else:
                        with self.assertRaises(grow.Refused):grow.open_readonly_target(identity)
                        closing.assert_called_once_with(123)
                    flags=opening.call_args.args[1]
                    self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY)
                    self.assertTrue(flags&os.O_NOFOLLOW);self.assertTrue(flags&os.O_CLOEXEC)

    def test_payload_hashes_cover_every_byte_in_the_requested_original_range(self):
        payload = self.base / 'payload.bin';payload.write_bytes(bytes(range(256)) * 400)
        parts = [dict(number=1, first=0, bytes=payload.stat().st_size)]
        with payload.open('rb') as stream, contextlib.redirect_stdout(io.StringIO()):
            original = grow.payload_hashes(stream.fileno(), parts)
        with payload.open('r+b') as stream:
            stream.seek(50001);stream.write(b'changed')
        with payload.open('rb') as stream, contextlib.redirect_stdout(io.StringIO()):
            changed = grow.payload_hashes(stream.fileno(), parts)
        self.assertNotEqual(original, changed)

    @unittest.skipUnless(shutil.which('sfdisk'), 'host sfdisk unavailable; guest integration remains required')
    def test_real_sfdisk_on_regular_fixture_preserves_guid_start_first_entries_and_payload_probes(self):
        self.assertTrue(stat.S_ISREG(self.image.lstat().st_mode))
        before, filesystem = inspect(self.image);plan = grow.plan_grow(target(), before, filesystem)
        offsets = [2048 * 512 + 4096, 133120 * 512 + 4096, DATA_START * 512 + 8192, DATA_END * 512 - 4096]
        with self.image.open('rb') as stream:
            original = [os.pread(stream.fileno(), 4096, offset) for offset in offsets]
        log = []
        for relocate, input_text in ((True, None), (False, plan['sfdisk_input'])):
            # Assert the target is our disposable regular file immediately before
            # each utility invocation. Never call this test on /dev or a loop.
            self.assertTrue(self.image.is_relative_to(ARTIFACTS))
            self.assertTrue(stat.S_ISREG(self.image.lstat().st_mode))
            value = subprocess.run(grow.sfdisk_arguments(str(self.image), relocate), input=input_text,
                                   capture_output=True, text=True, timeout=20, check=False)
            log.append(value.stdout + value.stderr);self.assertEqual(value.returncode, 0, log[-1])
            with self.image.open('rb') as stream:
                after = grow.read_gpt(stream.fileno(), DISK_BYTES)
            grow.verify_preserved(before, after, None if relocate else plan['new_data_end_lba'])
        self.assertEqual(after['parts'][2]['first'], DATA_START)
        self.assertEqual(after['parts'][2]['last'], plan['new_data_end_lba'])
        self.assertEqual(after['parts'][2]['uuid'], grow.DATA_UUID)
        self.assertEqual(after['table'][:256], before['table'][:256])
        self.assertEqual(after['primary']['backup'], DISK_BYTES // 512 - 1)
        with self.image.open('rb') as stream:
            self.assertEqual([os.pread(stream.fileno(), 4096, offset) for offset in offsets], original)
            after_filesystem = grow.read_ext4(stream.fileno(), after['parts'][2], pristine=True)
        self.assertEqual(after_filesystem, filesystem)
        (ARTIFACTS / 'sfdisk-regular-fixture.log').write_text('\n'.join(log))
        (ARTIFACTS / 'sfdisk-regular-fixture.json').write_text(json.dumps(dict(
            scope='Actual sfdisk on sparse regular fixture only; no host block writes or resize2fs execution',
            target_bytes=DISK_BYTES, data_start_lba=DATA_START, data_end_lba=after['parts'][2]['last'],
            data_partuuid=after['parts'][2]['uuid'], first_two_entries_identical=True,
            payload_probes_preserved=True, primary_and_backup_crc_valid=True), indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
