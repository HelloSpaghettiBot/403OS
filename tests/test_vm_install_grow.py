"""VM installer control-flow fixtures. No block device, loop, mount or shutdown.

Only a disposable COPY of the shell script is executed with literal paths
redirected to artifact files and strictly checked fixture commands. Production
has no fixture/host override. Real GPT/resize behavior belongs to the guest test.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'installer/install-vm.sh'
ARTIFACTS = ROOT / 'artifacts/tests/vm-install-grow'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
TOKEN = 'a' * 64


class VmGrowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.base = Path(self.temp.name)
        self.log = self.base / 'calls.jsonl'
        self.config = self.base / 'config.json'
        self.media = self.base / 'dev/vda'
        self.media_data = self.base / 'dev/vda3'
        self.mountpoint = self.base / 'media-mount'
        self.source = self.mountpoint / 'SurfaceGoGamingOS/surface-go.img'
        self.target = self.base / 'dev/vdb'
        self.media.parent.mkdir();self.source.parent.mkdir(parents=True)
        self.media.write_bytes(b'OUTER MEDIA MUST NEVER BE CLONED\0'*1024)
        self.media_data.write_bytes(b'fixture DATA partition')
        self.source.write_bytes(bytes(range(256)) * 64)
        self.sidecar=Path(str(self.source)+'.sha256')
        self.sidecar.write_text(hashlib.sha256(self.source.read_bytes()).hexdigest()+'  surface-go.img\n')
        self.target.write_bytes(b'TARGET SENTINEL\0' * 2048)
        self.mountinfo=self.base/'mountinfo';self.mountinfo.write_text('')
        self.sys = self.base / 'sys/class'
        for relative, value in {
            'dmi/id/product_name': 'Surface Go OS Disposable Test VM',
            'block/vda/serial': 'SURFACE_MEDIA', 'block/vda/size': str(self.media.stat().st_size // 512),
            'block/vda/queue/logical_block_size': '512', 'block/vda/dev': '0:0','block/vda/ro':'1',
            'block/vda/vda3/partition':'3','block/vda/vda3/dev':'0:0','block/vda/vda3/start':'100',
            'block/vda/vda3/size':'8192',
            'block/vdb/serial': 'SURFACE_TEST_TARGET', 'block/vdb/size': '134217728',
        }.items():
            path = self.sys / relative;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(value)
        (self.sys/'block/vda3').symlink_to(self.sys/'block/vda/vda3',target_is_directory=True)
        self.cmdline = self.base / 'cmdline';self.cmdline.write_text('root=/dev/vda2 surface.vm-install=1 ro')
        self.bin = self.base / 'bin';self.bin.mkdir()
        self.helper = self.base / 'surface-grow-data-fixture'
        self.write_config('success')
        common = f'''import json,os,stat,sys
from pathlib import Path
CONFIG=Path({str(self.config)!r})
def config(): return json.loads(CONFIG.read_text())
def record(event,**values):
    with Path(config()['log']).open('a') as stream: stream.write(json.dumps(dict(event=event,**values))+'\\n')
'''
        self.helper.write_text('#!/usr/bin/env python3\n' + common + textwrap.dedent('''
            import argparse
            def require_guest():
                record('guest-guard')
                if config()['scenario']=='guest-refused': raise ValueError('Guest marker refused')
            def current_target(path,profile,expected):
                record('target-guard',target=path,profile=profile,identity=expected)
                cfg=config()
                assert path==cfg['target'] and profile=='vm-install' and expected=='SURFACE_TEST_TARGET'
                assert stat.S_ISREG(Path(path).lstat().st_mode)
                if cfg['scenario']=='mounted-alias': raise ValueError('Target is mounted through a UUID alias')
                return dict(target=path,name='vdb',stable_id=expected,bytes=64*1024**3,devnum='252:16',profile=profile)
            def no_symlinks(path):
                assert path.is_relative_to(CONFIG.parent)
                current=path
                while current!=CONFIG.parent:
                    if stat.S_ISLNK(current.lstat().st_mode):raise ValueError('Symlink fixture refused')
                    current=current.parent
                return path
            def open_readonly_target(target):
                record('readback-open')
                assert target['target'] in (config()['target'],config()['media'])
                return os.open(target['target'],os.O_RDONLY|os.O_NOFOLLOW)
            def read_gpt(descriptor,size):
                record('gpt-check',bytes=size)
                assert stat.S_ISREG(os.fstat(descriptor).st_mode)
                assert os.readlink('/proc/self/fd/'+str(descriptor))==config()['source']
                if config()['scenario']=='bad-payload-gpt':
                    raise ValueError('Invalid packaged GPT fixture')
                return dict(parts=[{},{},dict(first=100,bytes=4194304)])
            def read_media_gpt(descriptor,size):
                record('live-media-gpt-check',bytes=size)
                assert stat.S_ISREG(os.fstat(descriptor).st_mode)
                assert os.readlink('/proc/self/fd/'+str(descriptor))==config()['media']
                if config()['scenario']=='bad-live-media-gpt':
                    raise ValueError('Invalid live media GPT fixture')
                return dict(parts=[{},dict(uuid='55555555-5555-4555-8555-555555555551'),
                    dict(first=100,bytes=4194304,uuid='55555555-5555-4555-8555-555555555552')])
            def read_ext4(descriptor,partition,pristine=False):
                record('pristine-check',pristine=pristine)
                assert pristine is True
                if config()['scenario']=='dirty-payload':raise ValueError('Payload DATA is not pristine')
                return dict(uuid='fixture-uuid',bytes=4*1024**3)
            if __name__=='__main__':
                parser=argparse.ArgumentParser()
                mode=parser.add_mutually_exclusive_group(required=True)
                mode.add_argument('--plan',action='store_true');mode.add_argument('--execute',action='store_true')
                parser.add_argument('--target',required=True);parser.add_argument('--profile',required=True)
                parser.add_argument('--expected-target-id',required=True);parser.add_argument('--review-token')
                args=parser.parse_args();cfg=config()
                assert args.target==cfg['target'] and args.profile=='vm-install' and args.expected_target_id=='SURFACE_TEST_TARGET'
                record('plan' if args.plan else 'execute',argv=sys.argv[1:])
                if args.plan:
                    if cfg['scenario']=='plan-failed': print('fixture plan refusal');sys.exit(3)
                    plan=cfg['plan']
                    if cfg['scenario']=='plan-wrong-target': plan['target']='/dev/WRONG-NOT-ACCESSED'
                    if cfg['scenario']=='plan-no-token': plan.pop('review_token')
                    print(json.dumps(plan))
                else:
                    assert args.review_token==cfg['plan']['review_token']
                    if cfg['scenario']=='execute-no-receipt': print(json.dumps({'event':'utility-complete'}));sys.exit(0)
                    complete=cfg['complete']
                    if cfg['scenario']=='execute-wrong-size': complete['new_bytes']=4*1024**3
                    print(json.dumps(complete))
                    if cfg['scenario']=='execute-failed': sys.exit(7)
            '''))
        self.helper.chmod(0o700)
        dd = self.bin / 'dd'
        dd.write_text('#!/usr/bin/env python3\n' + common + textwrap.dedent('''
            cfg=config();record('dd',argv=sys.argv[1:])
            assert sys.argv[1].startswith('if=/proc/self/fd/')
            assert sys.argv[2:]==['of='+cfg['target'],'bs=4M','conv=fsync']
            source,target=Path(sys.argv[1][3:]),Path(cfg['target'])
            assert source.resolve()==Path(cfg['source']) and target.parent.parent==CONFIG.parent
            source=source.resolve()
            assert stat.S_ISREG(source.lstat().st_mode) and stat.S_ISREG(target.lstat().st_mode)
            if cfg['scenario']=='dd-failed': sys.exit(5)
            with target.open('r+b') as stream:
                stream.write(source.read_bytes())
                if cfg['scenario']=='bad-readback': stream.seek(500);stream.write(b'CORRUPT')
            if cfg['scenario']=='source-changed': Path(cfg['source_serial']).write_text('CHANGED')
            '''))
        dd.chmod(0o700)
        mount=self.bin/'mount'
        mount.write_text('#!/usr/bin/env python3\n'+common+textwrap.dedent('''
            cfg=config();record('mount',argv=sys.argv[1:])
            assert sys.argv[1:]==['-t','ext4','-o','ro,noload,nosuid,nodev,noexec',cfg['media_data'],cfg['mountpoint']]
            options='rw,nosuid,nodev,noexec' if cfg['scenario']=='writable-source-mount' else 'ro,nosuid,nodev,noexec'
            Path(cfg['mountinfo']).write_text('42 23 0:0 / '+cfg['mountpoint']+' '+options+' - ext4 '+cfg['media_data']+' ro\\n')
            '''));mount.chmod(0o700)
        umount=self.bin/'umount'
        umount.write_text('#!/usr/bin/env python3\n'+common+textwrap.dedent('''
            cfg=config();record('umount',argv=sys.argv[1:]);assert sys.argv[1:]==[cfg['mountpoint']]
            Path(cfg['mountinfo']).write_text('')
            '''));umount.chmod(0o700)
        for name in ('sync', 'poweroff'):
            command = self.bin / name
            command.write_text('#!/usr/bin/env python3\n' + common + f"record({name!r})\n")
            command.chmod(0o700)
        original = SCRIPT.read_text()
        self.assertEqual(original.count('stat.S_ISBLK(info.st_mode)'), 2)
        transformed = original.replace('/usr/sbin/surface-grow-data', str(self.helper))
        transformed = transformed.replace('/proc/cmdline', str(self.cmdline))
        transformed = transformed.replace('/proc/self/mountinfo', str(self.mountinfo))
        transformed = transformed.replace('/run/surface-install-media',str(self.mountpoint))
        transformed = transformed.replace('/sys/class', str(self.sys))
        transformed = transformed.replace('/dev/vda', str(self.media)).replace('/dev/vdb', str(self.target))
        # This rewrite exists only in the disposable artifact copy. The real
        # installer continues to require real block nodes and the image guard.
        transformed = transformed.replace('stat.S_ISBLK(info.st_mode)', 'stat.S_ISREG(info.st_mode)')
        transformed = transformed.replace('info.st_uid!=0','info.st_uid!=os.getuid()')
        transformed = transformed.replace('initial.st_dev!=data_rdev','False')
        transformed = transformed.replace('not os.fstatvfs(source_fd).f_flag&os.ST_RDONLY','False')
        # Fixture payload directory already contains our files, whereas the
        # production mountpoint is empty before the real readonly mount.
        transformed = transformed.replace('or any(MOUNTPOINT.iterdir())','')
        self.script = self.base / 'install-vm-fixture.sh';self.script.write_text(transformed);self.script.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, scenario):
        new_data = (134217694 - 4327424 + 1) * 512
        plan = dict(event='plan', profile='vm-install', target=str(self.target), target_id='SURFACE_TEST_TARGET',
                    target_bytes=64 * 1024 ** 3, data_partition=3,
                    data_partuuid='8b4a530e-d6d4-4e69-9307-310000000002', filesystem_uuid='fixture-uuid',
                    old_data_bytes=4 * 1024 ** 3, new_data_bytes=new_data, data_start_lba=4327424,
                    new_data_end_lba=134217694, partition_starts_and_uuids_preserved=True,
                    unused_space_only=True, requires_unmounted=True, automatic_on_boot=False, review_token=TOKEN)
        complete = dict(event='grow-complete', target=str(self.target), target_id='SURFACE_TEST_TARGET',
                        data_partuuid=plan['data_partuuid'], filesystem_uuid=plan['filesystem_uuid'],
                        old_bytes=plan['old_data_bytes'], new_bytes=new_data // 4096 * 4096,
                        data_start_lba=4327424, data_end_lba=134217694,
                        efi_root_payload_preserved=True, old_data_payload_preserved_before_resize=True, mounted=False)
        self.config.write_text(json.dumps(dict(scenario=scenario, plan=plan, complete=complete, log=str(self.log),
                                               source=str(self.source), target=str(self.target),
                                               media=str(self.media),media_data=str(self.media_data),mountpoint=str(self.mountpoint),
                                               mountinfo=str(self.mountinfo),
                                               source_serial=str(self.sys / 'block/vda/serial'))))

    def run_case(self, scenario='success'):
        self.write_config(scenario)
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.defpath)
        value = subprocess.run([str(self.script), '--confirm-disposable-vm'], env=env,
                               capture_output=True, text=True, timeout=15, check=False)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return value, calls

    def test_verified_clone_plan_then_exact_token_execute_before_success_and_shutdown(self):
        value, calls = self.run_case()
        self.assertEqual(value.returncode, 0, value.stdout + value.stderr)
        events = [call['event'] for call in calls]
        self.assertLess(events.index('guest-guard'), events.index('dd'))
        self.assertLess(events.index('mount'),events.index('pristine-check'))
        self.assertLess(events.index('pristine-check'),events.index('dd'))
        self.assertLess(events.index('dd'), events.index('umount'))
        self.assertLess(events.index('umount'), events.index('plan'))
        self.assertLess(events.index('plan'), events.index('execute'))
        self.assertLess(events.index('execute'), events.index('poweroff'))
        executed = next(call for call in calls if call['event'] == 'execute')
        self.assertEqual(executed['argv'][-2:], ['--review-token', TOKEN])
        self.assertIn('SURFACE_VM_CLONE_VERIFIED', value.stdout)
        self.assertIn('SURFACE_VM_EMBEDDED_PAYLOAD_VERIFIED sha256='+hashlib.sha256(self.source.read_bytes()).hexdigest(),value.stdout)
        self.assertIn('SURFACE_VM_DATA_GROW_PASS bytes=', value.stdout)
        self.assertIn('SURFACE_VM_INSTALL_PASS', value.stdout)
        self.assertEqual(self.target.read_bytes()[:self.source.stat().st_size], self.source.read_bytes())
        (ARTIFACTS / 'control-flow-fixture-success.log').write_text(value.stdout + value.stderr)

    def test_missing_corrupt_dirty_or_writable_payload_never_clones_outer_media(self):
        for scenario in ('missing-payload','wrong-sha','bad-payload-gpt','dirty-payload','writable-source-mount'):
            with self.subTest(scenario=scenario):
                if self.log.exists():self.log.unlink()
                content=self.source.read_bytes();manifest=self.sidecar.read_text();sentinel=self.target.read_bytes()
                if scenario=='missing-payload':self.source.unlink()
                if scenario=='wrong-sha':self.sidecar.write_text('0'*64+'  surface-go.img\n')
                value,calls=self.run_case(scenario)
                self.assertEqual(value.returncode,2,value.stdout+value.stderr)
                self.assertNotIn('dd',[call['event'] for call in calls]);self.assertEqual(self.target.read_bytes(),sentinel)
                self.assertNotIn('SURFACE_VM_INSTALL_PASS',value.stdout)
                self.assertFalse(self.mountinfo.read_text())
                self.source.write_bytes(content);self.sidecar.write_text(manifest)

    def test_live_source_uses_separate_geometry_check_and_invalid_gpt_never_clones(self):
        value, calls = self.run_case()
        self.assertEqual(value.returncode, 0, value.stdout + value.stderr)
        events = [call['event'] for call in calls]
        self.assertEqual(events.count('live-media-gpt-check'), 1)
        self.assertEqual(events.count('gpt-check'), 1)
        self.assertLess(events.index('live-media-gpt-check'), events.index('gpt-check'))
        self.log.unlink()
        sentinel = self.target.read_bytes()
        value, calls = self.run_case('bad-live-media-gpt')
        self.assertEqual(value.returncode, 2, value.stdout + value.stderr)
        self.assertNotIn('dd', [call['event'] for call in calls])
        self.assertEqual(self.target.read_bytes(), sentinel)

    def test_equal_logical_media_and_embedded_image_sizes_are_valid(self):
        # Sparse inner/outer images have identical logical geometry even though
        # the outer DATA filesystem contains the physically sparse base file.
        with self.media.open('r+b') as stream:stream.truncate(self.source.stat().st_size)
        (self.sys/'block/vda/size').write_text(str(self.source.stat().st_size//512))
        value,calls=self.run_case()
        self.assertEqual(value.returncode,0,value.stdout+value.stderr)
        self.assertIn('SURFACE_VM_INSTALL_PASS',value.stdout)
        self.assertEqual(len([call for call in calls if call['event']=='dd']),1)

    def test_symlink_shared_write_and_bad_sidecar_refuse_before_target_write(self):
        content=self.source.read_bytes();manifest=self.sidecar.read_text()
        for scenario in ('symlink-source','shared-write-source','shared-write-sidecar','bad-sidecar','large-sidecar'):
            with self.subTest(scenario=scenario):
                if self.log.exists():self.log.unlink()
                sentinel=self.target.read_bytes()
                if scenario=='symlink-source':
                    alias=self.base/'payload-alias';alias.write_bytes(content)
                    self.source.unlink();self.source.symlink_to(alias)
                elif scenario=='shared-write-source':self.source.chmod(0o666)
                elif scenario=='shared-write-sidecar':self.sidecar.chmod(0o666)
                elif scenario=='bad-sidecar':self.sidecar.write_text(manifest.replace('surface-go.img','other.img'))
                else:self.sidecar.write_text('x'*4097)
                value,calls=self.run_case()
                self.assertEqual(value.returncode,2,value.stdout+value.stderr)
                self.assertNotIn('dd',[call['event'] for call in calls]);self.assertEqual(self.target.read_bytes(),sentinel)
                self.assertFalse(self.mountinfo.read_text())
                if self.source.is_symlink():self.source.unlink();self.source.write_bytes(content)
                self.source.chmod(0o644);self.sidecar.chmod(0o644);self.sidecar.write_text(manifest)

    def test_guest_and_alias_mount_refusal_precede_clone_or_helper_execution(self):
        for scenario in ('guest-refused', 'mounted-alias'):
            with self.subTest(scenario=scenario):
                if self.log.exists(): self.log.unlink()
                sentinel = self.target.read_bytes()
                value, calls = self.run_case(scenario)
                self.assertEqual(value.returncode, 2)
                self.assertNotIn('dd', [call['event'] for call in calls])
                self.assertEqual(self.target.read_bytes(), sentinel)
                self.assertNotIn('SURFACE_VM_INSTALL_PASS', value.stdout)

    def test_copy_failure_or_readback_mismatch_never_runs_growth_or_reports_pass(self):
        for scenario in ('dd-failed', 'bad-readback', 'source-changed'):
            with self.subTest(scenario=scenario):
                if self.log.exists(): self.log.unlink()
                (self.sys / 'block/vda/serial').write_text('SURFACE_MEDIA')
                value, calls = self.run_case(scenario)
                self.assertEqual(value.returncode, 2, value.stdout + value.stderr)
                self.assertNotIn('plan', [call['event'] for call in calls])
                self.assertNotIn('execute', [call['event'] for call in calls])
                self.assertNotIn('SURFACE_VM_INSTALL_PASS', value.stdout)
                self.assertNotIn('poweroff', [call['event'] for call in calls])

    def test_plan_failure_wrong_target_or_missing_token_never_execute(self):
        for scenario in ('plan-failed', 'plan-wrong-target', 'plan-no-token'):
            with self.subTest(scenario=scenario):
                if self.log.exists(): self.log.unlink()
                value, calls = self.run_case(scenario)
                self.assertEqual(value.returncode, 2, value.stdout + value.stderr)
                self.assertNotIn('execute', [call['event'] for call in calls])
                self.assertNotIn('SURFACE_VM_DATA_GROW_PASS', value.stdout)
                self.assertNotIn('SURFACE_VM_INSTALL_PASS', value.stdout)

    def test_failure_missing_receipt_or_unexpanded_data_never_reports_success(self):
        for scenario in ('execute-failed', 'execute-no-receipt', 'execute-wrong-size'):
            with self.subTest(scenario=scenario):
                if self.log.exists(): self.log.unlink()
                value, calls = self.run_case(scenario)
                self.assertEqual(value.returncode, 2, value.stdout + value.stderr)
                self.assertIn('execute', [call['event'] for call in calls])
                self.assertNotIn('SURFACE_VM_DATA_GROW_PASS', value.stdout)
                self.assertNotIn('SURFACE_VM_INSTALL_PASS', value.stdout)
                self.assertNotIn('poweroff', [call['event'] for call in calls])

    def test_boot_flag_substring_and_wrong_serial_or_capacity_refuse_before_clone(self):
        for path, bad, good in ((self.cmdline, 'surface.vm-install=10', 'surface.vm-install=1'),
                                (self.sys / 'block/vdb/serial', 'WRONG', 'SURFACE_TEST_TARGET'),
                                (self.sys / 'block/vdb/size', '1', '134217728')):
            with self.subTest(path=path):
                if self.log.exists(): self.log.unlink()
                path.write_text(bad);sentinel = self.target.read_bytes()
                value, calls = self.run_case()
                self.assertEqual(value.returncode, 2)
                self.assertFalse(calls)
                self.assertEqual(self.target.read_bytes(), sentinel)
                path.write_text(good)


if __name__ == '__main__':
    unittest.main()
