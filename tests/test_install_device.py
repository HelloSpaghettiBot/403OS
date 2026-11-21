import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib
from test_grow_data import fixture as growth_fixture, DATA_START, DATA_END, grow

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_device', ROOT / 'services/install-device.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
ARTIFACTS = ROOT / 'artifacts/tests/install-device'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def fixture_image(path):
    growth_fixture(path)
    with path.open('r+b') as stream:
        stream.truncate((DATA_END + 41) * 512)
    # Metadata/plan fixtures deliberately have no claimed full-image checksum
    # verification. Execution tests mock hashing/copying and never write blocks.
    digest = 'a' * 64
    Path(str(path) + '.sha256').write_text(digest + '  surface-go.img\n')
    return digest


def flip_byte(path, offset):
    with path.open('r+b') as stream:
        stream.seek(offset);value = stream.read(1);stream.seek(offset);stream.write(bytes([value[0] ^ 1]))


def modify_table(path, offset, value):
    """Regular artifact fixture only; repair both CRC copies after a mutation."""
    with path.open('r+b') as stream:
        stream.seek(512);primary = bytearray(stream.read(512));backup_lba = struct.unpack_from('<Q', primary, 32)[0]
        stream.seek(1024);table = bytearray(stream.read(128 * 128));table[offset:offset + len(value)] = value
        crc = zlib.crc32(table) & 0xffffffff
        stream.seek(backup_lba * 512);backup = bytearray(stream.read(512))
        for header in (primary, backup):
            struct.pack_into('<I', header, 88, crc);struct.pack_into('<I', header, 16, 0)
            struct.pack_into('<I', header, 16, zlib.crc32(header[:92]) & 0xffffffff)
        for at, data in ((512, primary), (1024, table), ((backup_lba - 32) * 512, table), (backup_lba * 512, backup)):
            stream.seek(at);stream.write(data)


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.base = Path(self.temp.name)
        self.sys = self.base / 'sys/class/block'
        self.dev = self.base / 'dev'
        self.sys.mkdir(parents=True)
        self.dev.mkdir()
        self.image = self.base / 'surface-go.img'
        self.digest = fixture_image(self.image)
        helper_patch = patch.object(installer, 'growth_helper', return_value=vars(grow))
        helper_patch.start();self.addCleanup(helper_patch.stop)
        self.seed_plan=dict(entries=[],bytes=64,files=2,rom_files=1,root_auth_preserved=True,manifest_sha256='a'*64)
        self.seed_helper=dict(library_lock=contextlib.nullcontext,mounted_data=lambda:None,
                             source_plan=lambda capacity:dict(self.seed_plan),
                             apply_seed=lambda target,identity,uuid,size,plan:dict(event='seed-complete',target=target,target_id=identity,verified=True,
                                 **{key:plan[key] for key in ('bytes','files','rom_files','root_auth_preserved')}))
        seed_patch=patch.object(installer,'seeding_helper',return_value=self.seed_helper)
        seed_patch.start();self.addCleanup(seed_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def disk(self, name='nvme0n1', usb=False, removable=False, size=64 * 1024 ** 3):
        physical = self.base / 'devices' / ('usb1' if usb else 'pci0') / name
        physical.mkdir(parents=True)
        (self.sys / name).symlink_to(physical, target_is_directory=True)
        for relative, value in [('device/serial', 'FIXTURE-ONLY-001'), ('device/model', 'Fixture SSD'),
                                ('size', str(size // 512)), ('queue/logical_block_size', '512'),
                                ('dev', '259:0'), ('ro', '0'), ('removable', '1' if removable else '0')]:
            path = physical / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        (physical / 'holders').mkdir()
        # Regular fixtures can be planned from synthetic sysfs metadata, but
        # validate_target_node must always refuse them for real execution.
        (self.dev / name).write_bytes(b'fixture target remains untouched')
        return physical

    def info(self, name='nvme0n1', mounts='', swaps=''):
        return installer.disk_info(name, mounts, swaps, self.sys, self.dev)

    def test_plan_is_read_only_and_binds_id_hash_and_confirmation(self):
        self.disk()
        before = (self.dev / 'nvme0n1').read_bytes()
        source = installer.source_info(self.image)
        target = self.info()
        plan = installer.plan_install(target, source, 'FIXTURE-ONLY-001', self.digest)
        self.assertEqual(plan['confirmation'], 'ERASE FIXTURE-ONLY-001')
        self.assertEqual(plan['target_bytes'], 64 * 1024 ** 3)
        self.assertFalse(plan['automatic_restart'])
        self.assertEqual((self.dev / 'nvme0n1').read_bytes(), before)
        self.assertFalse(source['checksum_verified'])
        self.assertEqual(plan['data_original_bytes'], 4 * 1024 ** 3)
        self.assertGreater(plan['data_proposed_bytes'], 50 * 1024 ** 3)
        self.assertEqual(plan['data_proposed_bytes'] % 4096, 0)
        self.assertIn('before first mount', plan['data_expansion'])
        with self.assertRaisesRegex(installer.Refused, 'identity changed'):
            installer.plan_install(target, source, 'DIFFERENT-DISK', self.digest)
        with self.assertRaisesRegex(installer.Refused, 'SHA256 differs'):
            installer.plan_install(target, source, target['stable_id'], '0' * 64)

    def test_self_contained_payload_on_mounted_data_is_discovered_without_other_mounts(self):
        data=self.base/'mounted-data';payload=data/'SurfaceGoGamingOS';payload.mkdir(parents=True)
        candidate=payload/'surface-go.img';os.link(self.image,candidate)
        os.link(Path(str(self.image)+'.sha256'),Path(str(candidate)+'.sha256'))
        before=candidate.stat().st_mtime_ns
        mounts=f'42 23 8:3 / {data} rw,nosuid,nodev - ext4 /dev/fixture3 rw\n'
        original_read=Path.read_text
        def read_text(path,*arguments,**keywords):
            return mounts if str(path)=='/proc/self/mountinfo' else original_read(path,*arguments,**keywords)
        with patch.object(installer,'require_guest'),patch.object(Path,'read_text',read_text), \
             patch.object(installer,'command',side_effect=AssertionError('Mounted DATA needs no mount/probe command')):
            sources,errors=installer.discover_sources([])
        self.assertEqual(errors,[]);self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]['path'],str(candidate));self.assertEqual(sources[0]['sha256'],self.digest)
        self.assertFalse(sources[0]['checksum_verified'])  # Full hashing still happens before target writes.
        self.assertEqual(candidate.stat().st_mtime_ns,before)

    def test_usb_fixed_disks_removable_and_small_targets_rejected(self):
        for name, options in [('sda', {'usb': True}), ('sdb', {'removable': True}),
                              ('sdc', {'size': installer.MINIMUM_TARGET - 512})]:
            self.disk(name, **options)
            with self.subTest(name=name), self.assertRaises(installer.Refused):
                installer.plan_install(self.info(name), installer.source_info(self.image))
        self.disk('mmcblk0', size=64_000_000_000)
        self.assertTrue(self.info('mmcblk0')['eligible'])

    def test_mounted_partition_and_mapped_descendant_exclude_whole_disk(self):
        disk = self.disk()
        part = disk / 'nvme0n1p1'
        part.mkdir()
        (part / 'partition').write_text('1')
        (part / 'dev').write_text('259:1')
        (part / 'holders').mkdir()
        (self.sys / 'nvme0n1p1').symlink_to(part, target_is_directory=True)
        mount = '21 1 259:1 / / rw - ext4 /dev/nvme0n1p1 rw\n'
        info = self.info(mounts=mount)
        self.assertFalse(info['eligible'])
        self.assertIn('running boot filesystem', info['reason'])
        mapper = self.base / 'devices/dm-0'
        mapper.mkdir()
        (mapper / 'dev').write_text('253:0')
        (mapper / 'holders').mkdir()
        (self.sys / 'dm-0').symlink_to(mapper, target_is_directory=True)
        (part / 'holders/dm-0').symlink_to(mapper, target_is_directory=True)
        self.assertFalse(self.info(mounts='22 1 253:0 / /home rw - ext4 /dev/dm-0 rw')['eligible'])
        self.assertFalse(self.info(swaps='Filename Type Size Used Priority\n/dev/nvme0n1p1 partition 1 0 -2')['eligible'])

    def test_source_overlap_missing_identity_and_4kn_refused(self):
        disk = self.disk()
        source = installer.source_info(self.image)
        target = self.info()
        source['filesystem_devnum'] = '259:0'
        with self.assertRaisesRegex(installer.Refused, 'stored on the target'):
            installer.plan_install(target, source)
        (disk / 'device/serial').write_text('')
        self.assertFalse(self.info()['eligible'])
        (disk / 'device/serial').write_text('FIXTURE')
        (disk / 'queue/logical_block_size').write_text('4096')
        self.assertFalse(self.info()['eligible'])

    def test_regular_virtual_target_and_all_symlink_paths_refused(self):
        self.disk()
        with self.assertRaisesRegex(installer.Refused, 'real block device'):
            installer.validate_target_node(self.dev / 'nvme0n1', dev_root=self.dev, sys_block=self.sys)
        link = self.dev / 'alias'
        link.symlink_to(self.dev / 'nvme0n1')
        with self.assertRaisesRegex(installer.Refused, 'Symlink'):
            installer.validate_target_node(link, dev_root=self.dev, sys_block=self.sys)
        folder = self.base / 'source-link'
        folder.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(installer.Refused, 'Symlink'):
            installer.source_info(folder / 'surface-go.img')

    def test_bad_gpt_or_checksum_sidecar_cannot_be_planned(self):
        with self.image.open('r+b') as stream:
            stream.seek(512)
            stream.write(b'BAD PART')
        with self.assertRaisesRegex(installer.Refused, 'primary GPT'):
            installer.source_info(self.image)
        fixture_image(self.image)
        Path(str(self.image) + '.sha256').write_text(self.digest + '  wrong.img\n')
        with self.assertRaisesRegex(installer.Refused, 'does not identify'):
            installer.source_info(self.image)

    def test_gpt_requires_backup_checksums_and_protected_metadata_ranges(self):
        # Fixture files only: no block device is opened or modified by this test.
        flip_byte(self.image, self.image.stat().st_size - 512 + 16)
        with self.assertRaisesRegex(installer.Refused, 'Backup GPT header checksum'):
            installer.source_info(self.image)
        fixture_image(self.image)
        flip_byte(self.image, (DATA_END + 8) * 512)
        with self.assertRaisesRegex(installer.Refused, 'partition tables disagree'):
            installer.source_info(self.image)
        fixture_image(self.image)
        with self.image.open('r+b') as stream:
            stream.seek(512);data = bytearray(stream.read(512))
            struct.pack_into('<Q', data, 40, 2);struct.pack_into('<I', data, 16, 0)
            struct.pack_into('<I', data, 16, zlib.crc32(data[:92]) & 0xffffffff)
            stream.seek(512);stream.write(data)
        with self.assertRaisesRegex(installer.Refused, 'overlap partition-table metadata'):
            installer.source_info(self.image)

    def test_host_refusal_precedes_every_command_and_writable_open(self):
        absent_marker = self.base / 'no-image-marker'
        with patch.object(installer, 'MARKER', absent_marker), \
             patch.object(installer.os, 'open', side_effect=AssertionError('No device/file open is allowed before host refusal')), \
             patch.object(installer, 'command', side_effect=AssertionError('No command is allowed before host refusal')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            for mode in ['--list', '--plan', '--execute']:
                self.assertEqual(installer.main([mode, '--target', '/dev/nvme0n1', '--source', str(self.image),
                    '--expected-target-id', 'FIXTURE', '--expected-source-sha256', self.digest,
                    '--confirm', 'ERASE FIXTURE']), 2)
        self.assertIn('Refusing host execution', output.getvalue())

    def test_progress_reader_hashes_exact_source_without_target_writes(self):
        payload = self.base / 'small-payload.bin';payload.write_bytes(bytes(range(256)) * 400)
        descriptor = payload.open('rb')
        try:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                actual = installer.progress_reader(descriptor.fileno(), payload.stat().st_size, 'verify-source')
        finally:
            descriptor.close()
        self.assertEqual(actual, hashlib.sha256(payload.read_bytes()).hexdigest())
        self.assertIn('"percent": 100', output.getvalue())

    def test_plan_cli_opens_only_readonly_source_and_never_runs_block_utilities(self):
        self.disk();target = self.info();before = (self.dev / 'nvme0n1').read_bytes()
        actual_open = os.open;flags_seen = []
        def readonly_open(path, flags, *arguments, **keywords):
            flags_seen.append(flags)
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            return actual_open(path, flags, *arguments, **keywords)
        with patch.object(installer, 'require_guest'), patch.object(installer, 'boot_partition_devnums', return_value=set()), \
             patch.object(installer, 'validate_target_node'), patch.object(installer, 'disk_info', return_value=target), \
             patch.object(installer, 'current_target', return_value=target), patch.object(installer.shutil, 'which', return_value='/fixture/available'), \
             patch.object(installer.os, 'open', side_effect=readonly_open), \
             patch.object(installer, 'command', side_effect=AssertionError('Planning must not invoke block utilities')), \
             patch.object(installer, 'open_clone_target', side_effect=AssertionError('Planning must not open a writable target')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = installer.main(['--plan', '--target', target['device'], '--source', str(self.image),
                                     '--expected-target-id', target['stable_id'], '--expected-source-sha256', self.digest])
        self.assertEqual(status, 0, output.getvalue())
        plan = json.loads(output.getvalue())
        self.assertGreater(plan['data_proposed_bytes'], 50 * 1024 ** 3)
        self.assertEqual(plan['confirmation'], 'ERASE ' + target['stable_id'])
        self.assertTrue(flags_seen)
        self.assertEqual((self.dev / 'nvme0n1').read_bytes(), before)

    def test_unknown_transport_virtual_disks_and_unmounted_holders_are_ineligible(self):
        physical = self.disk()
        virtual_parent = physical.parent / 'virtio7';virtual_parent.mkdir()
        virtual = virtual_parent / physical.name;physical.rename(virtual)
        link = self.sys / physical.name;link.unlink();link.symlink_to(virtual, target_is_directory=True)
        self.assertFalse(self.info()['eligible'])
        self.assertIn('physical internal', self.info()['reason'])
        unknown_parent = self.base / 'devices/platform';unknown_parent.mkdir()
        unknown = unknown_parent / physical.name;virtual.rename(unknown)
        link.unlink();link.symlink_to(unknown, target_is_directory=True)
        self.assertFalse(self.info()['eligible'])
        self.assertIn('Unknown internal', self.info()['reason'])
        unknown.rename(physical);link.unlink();link.symlink_to(physical, target_is_directory=True)
        holder = self.base / 'devices/dm-1';holder.mkdir()
        (holder / 'dev').write_text('253:1');(holder / 'holders').mkdir()
        (self.sys / 'dm-1').symlink_to(holder, target_is_directory=True)
        (physical / 'holders/dm-1').symlink_to(holder, target_is_directory=True)
        self.assertFalse(self.info()['eligible'])
        self.assertIn('holders', self.info()['reason'])

    def prepared_execution(self):
        self.disk();target = self.info();source = installer.source_info(self.image)
        plan = installer.plan_install(target, source, target['stable_id'], self.digest)
        with self.image.open('rb') as stream:
            gpt = grow.read_gpt(stream.fileno(), source['bytes'])
            filesystem = grow.read_ext4(stream.fileno(), gpt['parts'][2], pristine=True)
        strict_target = dict(target=target['device'], stable_id=target['stable_id'], model=target['model'],
                             bytes=target['bytes'], devnum=target['devnum'], profile='physical-internal')
        proposal = grow.plan_grow(strict_target, gpt, filesystem)
        receipt = dict(event='grow-complete', target=target['device'], target_id=target['stable_id'],
                       data_partuuid=installer.DATA_UUID, filesystem_uuid=source['data_layout']['filesystem_uuid'],
                       old_bytes=plan['data_original_bytes'], new_bytes=plan['data_proposed_bytes'],
                       data_start_lba=plan['data_start_lba'], data_end_lba=plan['data_end_lba'],
                       efi_root_payload_preserved=True, old_data_payload_preserved_before_resize=True, mounted=False)
        arguments = argparse.Namespace(target=target['device'], source=str(self.image),
                                       expected_target_id=target['stable_id'], expected_source_sha256=self.digest,
                                       confirm='ERASE ' + target['stable_id'])
        return target, source, plan, proposal, receipt, arguments

    def execute_fixture(self, prepared, scenario='success'):
        """No writable descriptor or block utility is used: copy/hash are mocked."""
        target, source, plan, proposal, receipt, arguments = prepared
        events = [];output = io.StringIO();failure = None
        def progress(descriptor, size, phase, write_descriptor=None):
            events.append(phase)
            # The fake clone descriptor is deliberately O_RDONLY, making an
            # accidental real write impossible even inside this fixture.
            if write_descriptor is not None:
                self.assertEqual(size, source['bytes'])
            if scenario == 'bad-source-hash' and phase == 'verify-source': return '0' * 64
            if scenario == 'bad-readback' and phase == 'verify-target': return '0' * 64
            return self.digest
        def open_clone(device, current):
            self.assertEqual(device, target['device']);self.assertEqual(current['stable_id'], target['stable_id'])
            events.append('open-clone')
            return os.open(self.image, os.O_RDONLY | os.O_CLOEXEC)
        def command(argv, seconds=20):
            self.assertEqual(argv[0], str(installer.GROW_HELPER))
            self.assertEqual(argv[2:8], ['--target', target['device'], '--profile', 'physical-internal', '--expected-target-id', target['stable_id']])
            if argv[1] == '--plan':
                events.append('grow-plan');self.assertEqual(seconds, 60)
                changed = dict(proposal)
                if scenario == 'wrong-growth-target': changed['target_id'] = 'OTHER'
                if scenario == 'wrong-source-layout': changed['gpt_sha256'] = '0' * 64
                if scenario == 'no-token': changed.pop('review_token')
                return json.dumps(changed)
            events.append('grow-execute');self.assertEqual(seconds, 900)
            self.assertEqual(argv[8:], ['--review-token', proposal['review_token']])
            if scenario == 'growth-refused': raise installer.Refused('Fixture growth utility refused')
            if scenario == 'no-completion': return json.dumps(dict(event='utility-complete'))
            completed = dict(receipt)
            if scenario == 'unexpanded': completed['new_bytes'] = 4 * 1024 ** 3
            return json.dumps(completed) + '\n'
        def final(*unused):
            events.append('final-metadata-verification')
            if scenario == 'final-mismatch': raise installer.Refused('Fixture final filesystem size mismatch')
            return dict(uuid=plan['data_filesystem_uuid'], bytes=plan['data_proposed_bytes'])
        def seed_plan(capacity):
            events.append('migration-preflight')
            if scenario=='migration-space':raise ValueError('fixture live data does not fit')
            return dict(self.seed_plan)
        def seed_data(device,identity,uuid,size,reviewed):
            events.append('migration-copy-and-verify')
            self.assertEqual(size,plan['data_proposed_bytes'])
            if scenario=='migration-failed':raise ValueError('fixture migration readback failed')
            return dict(event='seed-complete',target=device,target_id=identity,verified=scenario!='migration-unverified',
                        **{key:reviewed[key] for key in ('files','bytes','rom_files','root_auth_preserved')})
        helper=dict(self.seed_helper,source_plan=seed_plan,apply_seed=seed_data)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(installer,'seeding_helper',return_value=helper))
            stack.enter_context(patch.object(installer, 'require_guest'))
            stack.enter_context(patch.object(installer, 'boot_partition_devnums', return_value=set()))
            stack.enter_context(patch.object(installer, 'current_target', return_value=target))
            stack.enter_context(patch.object(installer, 'preflight_utilities', side_effect=lambda: events.append('runtime-preflight')))
            stack.enter_context(patch.object(installer, 'progress_reader', side_effect=progress))
            stack.enter_context(patch.object(installer, 'open_clone_target', side_effect=open_clone))
            stack.enter_context(patch.object(installer, 'command', side_effect=command))
            stack.enter_context(patch.object(installer, 'verify_final_growth', side_effect=final))
            stack.enter_context(patch.object(installer.os, 'sync'))
            stack.enter_context(contextlib.redirect_stdout(output))
            try: installer.execute_install(arguments)
            except installer.Refused as error: failure = str(error)
        return events, output.getvalue(), failure

    def test_execute_grows_only_after_verified_clone_and_checks_completion_against_review(self):
        prepared = self.prepared_execution()
        events, output, failure = self.execute_fixture(prepared)
        self.assertIsNone(failure, output)
        self.assertLess(events.index('runtime-preflight'), events.index('open-clone'))
        self.assertLess(events.index('migration-preflight'), events.index('open-clone'))
        self.assertLess(events.index('verify-source'), events.index('open-clone'))
        self.assertLess(events.index('verify-target'), events.index('grow-plan'))
        self.assertLess(events.index('grow-plan'), events.index('grow-execute'))
        self.assertLess(events.index('grow-execute'), events.index('final-metadata-verification'))
        self.assertLess(events.index('final-metadata-verification'),events.index('migration-copy-and-verify'))
        completed = json.loads(output.splitlines()[-1])
        self.assertEqual(completed['event'], 'complete')
        self.assertTrue(completed['data_expansion_verified'])
        self.assertTrue(completed['user_data_verified']);self.assertTrue(completed['root_auth_preserved'])
        self.assertEqual(completed['rom_files'],1)
        self.assertEqual(completed['data_actual_bytes'], prepared[2]['data_proposed_bytes'])
        (ARTIFACTS / 'mocked-sequence.json').write_text(json.dumps(dict(
            scope='Isolated physical installer sequence with mocked hashing/copy/utilities and read-only fixture descriptors; no physical install or filesystem resize',
            events=events, complete=completed), indent=2) + '\n')

    def test_hash_failures_prevent_growth_and_source_failure_prevents_writable_open(self):
        prepared = self.prepared_execution()
        for scenario in ('bad-source-hash', 'bad-readback'):
            with self.subTest(scenario=scenario):
                events, output, failure = self.execute_fixture(prepared, scenario)
                self.assertIsNotNone(failure)
                self.assertNotIn('grow-plan', events)
                if scenario == 'bad-source-hash': self.assertNotIn('open-clone', events)
                self.assertNotIn('"event": "complete"', output)

    def test_migration_preflight_failure_prevents_erase_and_copy_failures_never_complete(self):
        prepared=self.prepared_execution()
        for scenario in ('migration-space','migration-failed','migration-unverified'):
            with self.subTest(scenario=scenario):
                events,output,failure=self.execute_fixture(prepared,scenario)
                self.assertIsNotNone(failure);self.assertNotIn('"event": "complete"',output)
                if scenario=='migration-space':self.assertNotIn('open-clone',events)

    def test_mismatched_growth_plan_or_missing_token_never_executes_growth(self):
        prepared = self.prepared_execution()
        for scenario in ('wrong-growth-target', 'wrong-source-layout', 'no-token'):
            with self.subTest(scenario=scenario):
                events, output, failure = self.execute_fixture(prepared, scenario)
                self.assertIsNotNone(failure)
                self.assertNotIn('grow-execute', events)
                self.assertNotIn('"event": "complete"', output)

    def test_growth_failures_missing_receipt_unexpanded_or_final_mismatch_never_complete(self):
        prepared = self.prepared_execution()
        for scenario in ('growth-refused', 'no-completion', 'unexpanded', 'final-mismatch'):
            with self.subTest(scenario=scenario):
                events, output, failure = self.execute_fixture(prepared, scenario)
                self.assertIsNotNone(failure)
                self.assertNotIn('"event": "complete"', output)

    def test_exact_source_layout_and_pristine_data_are_checked_before_writable_open(self):
        prepared = self.prepared_execution();target, source, plan, proposal, receipt, arguments = prepared
        for mutation in ('wrong-name', 'mounted-data'):
            with self.subTest(mutation=mutation):
                fixture_image(self.image)
                if mutation == 'wrong-name': modify_table(self.image, 2 * 128 + 56, b'x\0')
                else:
                    with self.image.open('r+b') as stream:
                        stream.seek(DATA_START * 512 + 1024 + 0x2c);stream.write(struct.pack('<I', 100))
                with patch.object(installer, 'require_guest'), patch.object(installer, 'boot_partition_devnums', return_value=set()), \
                     patch.object(installer, 'current_target', return_value=target), \
                     patch.object(installer, 'open_clone_target', side_effect=AssertionError('Bad layout must fail before writable open')), \
                     patch.object(installer, 'progress_reader', side_effect=AssertionError('Bad source metadata must fail before hashing')):
                    with self.assertRaises(installer.Refused): installer.execute_install(arguments)

    def test_missing_resize_utility_and_changed_capacity_fail_before_writable_open(self):
        target, source, plan, proposal, receipt, arguments = self.prepared_execution()
        with patch.object(installer, 'require_guest'), patch.object(installer, 'boot_partition_devnums', return_value=set()), \
             patch.object(installer, 'current_target', return_value=target), \
             patch.object(installer.shutil, 'which', side_effect=lambda name: None if name == 'resize2fs' else '/fixture/utility'), \
             patch.object(installer, 'open_clone_target', side_effect=AssertionError('Missing tool must fail before writable open')):
            with self.assertRaisesRegex(installer.Refused, 'resize2fs'): installer.execute_install(arguments)
        changed = dict(target);changed['bytes'] -= 512
        with patch.object(installer, 'require_guest'), patch.object(installer, 'boot_partition_devnums', return_value=set()), \
             patch.object(installer, 'current_target', side_effect=[target, changed]), patch.object(installer, 'preflight_utilities'), \
             patch.object(installer, 'progress_reader', return_value=self.digest), \
             patch.object(installer, 'open_clone_target', side_effect=AssertionError('Changed target must fail before writable open')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(installer.Refused, 'capacity'): installer.execute_install(arguments)

    def test_final_metadata_reread_checks_expanded_size_uuid_and_all_preserved_entries(self):
        target, source, plan, proposal, receipt, arguments = self.prepared_execution()
        final = self.base / 'expanded-metadata-fixture.img';growth_fixture(final, relocated=True)
        modify_table(final, 2 * 128 + 40, struct.pack('<Q', plan['data_end_lba']))
        # The whole-disk alias deliberately retains the original superblock;
        # only the partition alias reflects resize2fs's metadata writes.
        partition_file = self.base / 'expanded-data-partition.img'
        with final.open('rb') as stream:
            superblock = bytearray(os.pread(stream.fileno(), 1024, DATA_START * 512 + 1024))
        struct.pack_into('<I', superblock, 4, plan['data_proposed_bytes'] // 4096)
        partition_file.write_bytes(bytes(1024) + superblock)
        helper = dict(vars(grow))
        helper['current_target'] = lambda *unused: dict(target=target['device'])
        helper['open_readonly_target'] = lambda unused: os.open(final, os.O_RDONLY | os.O_CLOEXEC)
        helper['data_device'] = lambda *unused: '/dev/fixture-data3'
        def partition_filesystem(reviewed, partition, device):
            self.assertEqual(device, '/dev/fixture-data3')
            with partition_file.open('rb') as stream:
                return grow.read_ext4(stream.fileno(), dict(partition, first=0))
        helper['read_data_filesystem'] = partition_filesystem
        with self.image.open('rb') as original, patch.object(installer, 'growth_helper', return_value=helper):
            verified = installer.verify_final_growth(original.fileno(), target, source, plan)
            self.assertEqual(verified['bytes'], plan['data_proposed_bytes'])
            # The sparse fixture's superblock is a metadata test, not a real
            # resized ext4 filesystem; no e2fsck or resize2fs is run here.
            with partition_file.open('r+b') as stream:
                stream.seek(1024 + 0x68);stream.write(uuid.uuid4().bytes)
            with self.assertRaisesRegex(installer.Refused, 'identity or capacity'):
                installer.verify_final_growth(original.fileno(), target, source, plan)
            with partition_file.open('r+b') as stream:
                stream.seek(1024 + 0x68);stream.write(uuid.UUID(plan['data_filesystem_uuid']).bytes)
            modify_table(final, 16, uuid.uuid4().bytes_le)
            with self.assertRaisesRegex(installer.Refused, 'preserved'):
                installer.verify_final_growth(original.fileno(), target, source, plan)


if __name__ == '__main__':
    unittest.main()
