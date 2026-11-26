"""The optional controller integration probe must remain confined to the VM."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    loader=importlib.machinery.SourceFileLoader(name,str(path))
    spec=importlib.util.spec_from_loader(name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


vm=load('ipega_vm_fixture',ROOT/'scripts/vm.py')
guest=load('ipega_guest_fixture',ROOT/'external/board/surface-go1/overlay/usr/sbin/surface-app-check')


class IpegaVmTests(unittest.TestCase):
    def test_opt_in_live_request_does_not_authorize_proton_or_host_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);run=root/'run';run.mkdir()
            args=vm.parse_arguments(['live','--test-ipega'])
            with patch.object(vm,'ARTIFACTS',root):
                options=vm.prepare_test_options(run,args,run/'disposable.qcow2',media=True)
            self.assertEqual(json.loads((options/'request.json').read_text()),
                dict(schema='surface-vm-test-options-v1',test_proton=False,test_ipega=True))

    def test_probe_is_rejected_during_direct_kernel_install(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            vm.parse_arguments(['install','--test-ipega'])

    def test_physical_hardware_never_reads_vm_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity=Path(temporary)/'identity';identity.write_text('Surface Go')
            with patch.object(guest,'VM_DMI',identity),patch.object(guest,'MOUNT_INFO') as mounts:
                self.assertFalse(guest.ipega_requested())
                self.assertFalse(guest.proton_requested())
                mounts.read_text.assert_not_called()

    def test_unprivileged_probe_drops_primary_and_supplementary_groups(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            account=SimpleNamespace(pw_uid=1000,pw_gid=1000)
            with patch.object(guest.pwd,'getpwnam',return_value=account), \
                    patch.object(guest.os,'getgrouplist',return_value=[1000,1100]), \
                    patch.object(guest.subprocess,'Popen') as popen:
                popen.return_value.wait.return_value=0
                guest.command(['fixture'],Path(temporary)/'log',user='gaming')
                self.assertEqual(popen.call_args.kwargs['user'],1000)
                self.assertEqual(popen.call_args.kwargs['group'],1000)
                self.assertEqual(popen.call_args.kwargs['extra_groups'],[1000,1100])


if __name__=='__main__':unittest.main()
