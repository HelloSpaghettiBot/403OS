"""Optional Proton plumbing fixtures. Never runs a VM, network or Windows code."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]

def module(name,path):
    loader=importlib.machinery.SourceFileLoader(name,str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    value=importlib.util.module_from_spec(spec);loader.exec_module(value)
    return value

vm=module('surface_vm_proton_fixture',ROOT/'scripts/vm.py')
check=module('surface_app_proton_fixture',ROOT/'external/board/surface-go1/overlay/usr/sbin/surface-app-check')
LIBRARY=ROOT/'external/board/surface-go1/overlay/etc/init.d/S21library'


class OptionalProtonHost(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.artifacts=self.base/'vm-tests';self.artifacts.mkdir()
        self.run=self.artifacts/'run';self.run.mkdir()
        self.disk=self.artifacts/'installed.qcow2';self.disk.write_bytes(b'EXISTING DISK SENTINEL')
        self.patcher=patch.object(vm,'ARTIFACTS',self.artifacts);self.patcher.start();self.addCleanup(self.patcher.stop)

    def args(self,extra=()):
        return vm.parse_arguments(['test','--disk',str(self.disk),*extra])

    def test_flag_requires_explicit_installed_mode_and_disk(self):
        for argv in (['live','--test-proton'],['install','--disk',str(self.disk),'--test-proton'],['test','--test-proton']):
            with self.subTest(argv=argv),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):vm.parse_arguments(argv)
        self.assertTrue(self.args(['--test-proton']).test_proton)
        self.assertFalse(self.args().test_proton)

    def test_no_request_file_or_share_without_explicit_flag(self):
        self.assertIsNone(vm.prepare_test_options(self.run,self.args(),self.disk))
        self.assertFalse((self.run/'test-options').exists())
        summary={'checks':{}};vm.record_proton_result(summary,'SURFACE_PROTON_REQUEST requested=False\n',False)
        self.assertEqual(summary['proton_test']['status'],'NOT_REQUESTED');self.assertFalse(summary['checks'])

    def test_request_is_fixed_private_json_and_existing_disk_is_unchanged(self):
        options=vm.prepare_test_options(self.run,self.args(['--test-proton']),self.disk)
        self.assertEqual(json.loads((options/'request.json').read_text()),{'schema':'surface-vm-test-options-v1','test_proton':True})
        self.assertEqual(stat.S_IMODE((options/'request.json').stat().st_mode),0o400)
        self.assertEqual(self.disk.read_bytes(),b'EXISTING DISK SENTINEL')
        for kwargs in ({'install':True},{'media':True}):
            with self.assertRaises(ValueError):vm.prepare_test_options(self.run,self.args(['--test-proton']),self.disk,**kwargs)
        self.disk.unlink()
        with self.assertRaises(ValueError):vm.prepare_test_options(self.run,self.args(['--test-proton']),self.disk)

    def test_qemu_share_is_read_only_and_absent_from_default_boot(self):
        firmware=self.base/'OVMF_CODE.fd';firmware.write_bytes(b'fixture')
        firmware.with_name('OVMF_VARS.fd').write_bytes(b'fixture-vars')
        for requested in (False,True):
            run=self.artifacts/str(requested);run.mkdir()
            args=self.args(['--ovmf',str(firmware)]+(['--test-proton'] if requested else []))
            process=Mock();process.poll.return_value=0
            with patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.base)}),patch.object(vm.subprocess,'Popen',return_value=process):
                instance=vm.VM(run,args,self.disk)
                try:
                    command=json.loads((run/'command.json').read_text())
                    shares=[command[index+1] for index,arg in enumerate(command[:-1]) if arg=='-virtfs']
                    self.assertEqual(len(shares),int(requested))
                    if requested:
                        self.assertIn('mount_tag=surface_test_options',shares[0]);self.assertIn('readonly=on',shares[0])
                    self.assertEqual(instance.metadata['proton_requested'],requested)
                finally:instance.close()

    def test_summary_requires_guest_request_and_actual_pass_without_failure(self):
        for requested,log,status in (
            (True,'SURFACE_PROTON_REQUEST requested=True\nSURFACE_PROTON_PASS scope=Windows_command\n','PASS'),
            (True,'SURFACE_PROTON_PASS scope=Windows_command\n','FAIL'),
            (True,'SURFACE_PROTON_REQUEST requested=True\nSURFACE_PROTON_FAIL reason=probe\n','FAIL'),
            (True,'SURFACE_PROTON_REQUEST requested=True\nSURFACE_PROTON_PASS\nSURFACE_PROTON_FAIL\n','FAIL'),
            (False,'SURFACE_PROTON_PASS\n','FAIL')):
            with self.subTest(requested=requested,log=log):
                summary={'checks':{}};vm.record_proton_result(summary,log,requested)
                self.assertEqual(summary['proton_test']['status'],status)
                self.assertIs(summary['proton_test']['gameplay_verified'],False)


class OptionalProtonGuest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.options=self.base/'options';self.options.mkdir()
        self.dmi=self.base/'dmi';self.dmi.write_text(check.VM_NAME)
        self.mounts=self.base/'mountinfo'
        self.mounts.write_text(f'42 23 0:51 / {self.options} ro,nosuid,nodev,noexec - 9p surface_test_options ro,trans=virtio\n')
        self.request=self.options/'request.json';self.request.write_text(json.dumps({'schema':'surface-vm-test-options-v1','test_proton':True}))
        for name,value in (('VM_DMI',self.dmi),('TEST_OPTIONS',self.options),('MOUNT_INFO',self.mounts)):
            patcher=patch.object(check,name,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(check.os,'fstatvfs',return_value=SimpleNamespace(f_flag=os.ST_RDONLY));patcher.start();self.addCleanup(patcher.stop)

    def test_hardware_and_missing_share_never_read_the_request(self):
        self.dmi.write_text('Surface Go')
        class Forbidden:
            def read_text(self):raise AssertionError('Hardware must never inspect VM test shares')
        with patch.object(check,'MOUNT_INFO',Forbidden()):self.assertFalse(check.proton_requested())
        self.dmi.write_text(check.VM_NAME);self.mounts.write_text('')
        self.assertFalse(check.proton_requested())

    def test_request_requires_read_only_noexec_exact_mount_and_actual_ro_filesystem(self):
        original=self.mounts.read_text()
        for changed in (original.replace('ro,nosuid','rw,nosuid'),original.replace(',noexec',''),
                        original.replace('surface_test_options','surface_other'),original.replace(' - 9p ',' - ext4 ')):
            with self.subTest(changed=changed):
                self.mounts.write_text(changed)
                with self.assertRaises(RuntimeError):check.proton_requested()
        self.mounts.write_text(original)
        with patch.object(check.os,'fstatvfs',return_value=SimpleNamespace(f_flag=0)),self.assertRaises(RuntimeError):check.proton_requested()
        self.assertTrue(check.proton_requested())

    def test_request_is_strict_boolean_bounded_regular_and_has_no_command_fields(self):
        for value in (1,'true',None,[],{}):
            with self.subTest(value=value):
                self.request.write_text(json.dumps({'schema':'surface-vm-test-options-v1','test_proton':value}))
                with self.assertRaises(RuntimeError):check.proton_requested()
        for value in ({'schema':'other','test_proton':True},
                      {'schema':'surface-vm-test-options-v1','test_proton':True,'command':['anything']}):
            self.request.write_text(json.dumps(value))
            with self.assertRaises(RuntimeError):check.proton_requested()
        self.request.write_text('{"schema":"surface-vm-test-options-v1","test_proton":false,"test_proton":true}')
        with self.assertRaises(RuntimeError):check.proton_requested()
        self.request.write_text(' '*4097)
        with self.assertRaises(RuntimeError):check.proton_requested()
        self.request.unlink();self.request.symlink_to(self.dmi)
        with self.assertRaises(OSError):check.proton_requested()

    def test_false_flag_and_direct_component_cannot_authorize_setup(self):
        self.request.write_text(json.dumps({'schema':'surface-vm-test-options-v1','test_proton':False}))
        self.assertFalse(check.proton_requested())
        with patch.object(check,'guard'),patch.object(check,'command',side_effect=AssertionError('No Windows or network process permitted')):
            with self.assertRaisesRegex(RuntimeError,'explicit'):check.proton_check(self.base)
        with patch.object(check,'guard',side_effect=RuntimeError('host refused')),patch.object(check,'proton_requested',side_effect=AssertionError('guard must run first')):
            with self.assertRaisesRegex(RuntimeError,'host refused'):check.proton_check(self.base)

    def test_requested_component_follows_browser_and_defaults_never_include_it(self):
        defaults='tailscale,moonlight,audio,wine,browser'
        self.assertNotIn('proton',check.selected_components(defaults,False))
        self.assertEqual(check.selected_components(defaults,True)[-2:],['browser','proton'])
        self.assertEqual(check.selected_components('proton,wine',True),['wine','browser','proton'])

    def probe(self,replies,capacity=None):
        fields={'proton_path':'/data/windows/.local/share/Steam/compatibilitytools.d/UMU-Proton-11',
                'runtime':'steamrt4','manifest_sha256':'a'*64,'version':'UMU-Proton-11'}
        complete=json.dumps(dict(event='complete',**fields))
        status=json.dumps(dict(event='windows-status',proton_ready=True,proton=fields,gameplay_verified=False))
        calls=[]
        def fixture(argv,log,timeout=10,env=None,tail=False):
            calls.append((argv,timeout,tail))
            self.assertEqual(argv,['/usr/bin/surface-windows-runner','--setup-proton' if len(calls)==1 else '--status'])
            return replies(complete,status,len(calls))
        with patch.object(check,'guard'),patch.object(check,'proton_requested',return_value=True), \
             patch.object(check.shutil,'disk_usage',return_value=capacity or SimpleNamespace(total=60*1024**3,free=20*1024**3)), \
             patch.object(check,'command',side_effect=fixture):
            return check.proton_check(self.base),calls

    def test_real_runner_success_requires_both_final_completion_and_read_only_ready_status(self):
        result,calls=self.probe(lambda complete,status,index:(0,complete if index==1 else status))
        self.assertEqual(result['status'],'PASS');self.assertIs(result['gameplay_verified'],False)
        self.assertEqual(calls[0][1:],(1500,True));self.assertEqual(calls[1][1:],(15,False))

    def test_setup_or_status_failure_and_metadata_disagreement_never_pass(self):
        scenarios=(
            lambda complete,status,index:(1,complete),
            lambda complete,status,index:(0,'{"event":"progress"}'),
            lambda complete,status,index:(0,complete+'\n{"event":"error"}'),
            lambda complete,status,index:(0,complete) if index==1 else (1,status),
            lambda complete,status,index:(0,complete if index==1 else status.replace('"proton_ready": true','"proton_ready": false')),
            lambda complete,status,index:(0,complete if index==1 else status.replace('"gameplay_verified": false','"gameplay_verified": true')),
            lambda complete,status,index:(0,complete if index==1 else status.replace('"runtime": "steamrt4"','"runtime": "steamrt3"')))
        for scenario in scenarios:
            with self.subTest(scenario=scenario),self.assertRaises(RuntimeError):self.probe(scenario)

    def test_small_or_full_data_refuses_before_any_process(self):
        for capacity in (SimpleNamespace(total=4*1024**3,free=4*1024**3),SimpleNamespace(total=60*1024**3,free=5*1024**3)):
            with self.subTest(capacity=capacity),self.assertRaisesRegex(RuntimeError,'no download started'):
                self.probe(lambda *_:self.fail('No process may run'),capacity)

    def test_command_tail_keeps_real_final_event_after_large_progress_without_process_execution(self):
        final=json.dumps({'event':'complete'});payload=b'x'*300000+b'\n'+final.encode()+b'\n'
        def fixture(_argv,**kwargs):
            kwargs['stdout'].write(payload);kwargs['stdout'].flush()
            process=Mock();process.wait.return_value=0;return process
        with patch.object(check.subprocess,'Popen',side_effect=fixture):
            code,output=check.command(['fixture-only'],self.base/'tail.log',tail=True)
        self.assertEqual(code,0);self.assertLessEqual(len(output),256*1024)
        self.assertEqual(check.last_event(output),{'event':'complete'})

    def test_s21_exact_dmi_precedes_test_directory_and_only_mounts_read_only(self):
        # Execute only a disposable copy of the options block with literal paths
        # redirected to fixtures and a fake mount. Never run the host init script.
        block='        # Optional acceptance'+LIBRARY.read_text().split('        # Optional acceptance',1)[1].split('        mkdir -p /library/roms',1)[0]
        tags=self.base/'virtio0';tags.mkdir();(tags/'mount_tag').write_text('surface_test_options\n')
        destination=self.base/'mounted-options';serial=self.base/'serial';calls=self.base/'mount-calls.json'
        script=self.base/'options-fixture.sh'
        block=block.replace('/sys/class/dmi/id/product_name',str(self.dmi)).replace('/sys/bus/virtio/drivers/9pnet_virtio/virtio*/mount_tag',str(tags/'mount_tag'))
        block=block.replace('/run/surface-test-options',str(destination)).replace('/dev/ttyS0',str(serial))
        script.write_text('#!/bin/sh\n'+block)
        binary=self.base/'bin';binary.mkdir();fake=binary/'mount'
        fake.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(calls))+').write_text(json.dumps(sys.argv[1:]))\n');fake.chmod(0o700)
        environment=dict(os.environ,PATH=str(binary)+':'+os.environ.get('PATH','/usr/bin:/bin'))
        self.dmi.write_text('Surface Go');subprocess.run(['/bin/sh',str(script)],env=environment,check=True,timeout=3)
        self.assertFalse(destination.exists());self.assertFalse(calls.exists())
        self.dmi.write_text(check.VM_NAME);subprocess.run(['/bin/sh',str(script)],env=environment,check=True,timeout=3)
        argv=json.loads(calls.read_text());self.assertEqual(argv[:2],['-t','9p'])
        self.assertTrue({'ro','noexec','nosuid','nodev'}<=set(argv[3].split(',')))
        self.assertEqual(argv[-2:],['surface_test_options',str(destination)])


if __name__=='__main__':unittest.main()
