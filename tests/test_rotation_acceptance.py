"""Probe evidence fixtures only; real module/browser acceptance runs in QEMU."""
import collections
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('rotation_app_fixture',str(ROOT/'external/board/surface-go1/overlay/usr/sbin/surface-app-check'))
spec=importlib.util.spec_from_loader(loader.name,loader);check=importlib.util.module_from_spec(spec);loader.exec_module(check)


class RotationAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.dmi=self.base/'dmi';self.dmi.write_text(check.VM_NAME)
        self.module=self.base/'module.so';self.module.write_bytes(b'SYNTHETIC MODULE PRESENCE FIXTURE')
        self.process=SimpleNamespace(pid=200,poll=lambda:None)
        self.browser=dict(pid=200,start_ticks=123,executable='cog')
        self.compositor=dict(pid=100,start_ticks=99,executable='weston')
        self.base_geometry=dict(width=1280,height=800,screen_width=1280,screen_height=800)
        self.portrait=dict(width=800,height=1280,screen_width=800,screen_height=1280)
    def context(self):
        from contextlib import ExitStack
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(check,'guard'))
        stack.enter_context(patch.object(check,'VM_DMI',self.dmi));stack.enter_context(patch.object(check,'ROTATION_MODULE',self.module))
        stack.enter_context(patch.object(check,'vm_rotation_output',return_value='Virtual-1'))
        snapshot=stack.enter_context(patch.object(check,'display_state_snapshot',return_value={'fixture':'unchanged'}))
        stack.enter_context(patch.object(check,'browser_process',return_value=200))
        identities=stack.enter_context(patch.object(check,'process_identity',side_effect=lambda pid,_:self.browser if pid==200 else self.compositor))
        command=stack.enter_context(patch.object(check,'rotation_command',return_value=self.compositor))
        geometry=stack.enter_context(patch.object(check,'wait_browser_geometry',side_effect=[self.base_geometry,self.portrait,self.base_geometry]))
        emitted=stack.enter_context(patch.object(check,'emit'))
        return snapshot,identities,command,geometry,emitted
    def test_browser_identity_follows_only_its_own_launcher_process_group(self):
        proc=self.base/'proc';proc.mkdir()
        cog=self.base/'cog';cog.write_bytes(b'fixture executable')
        other=self.base/'dbus-run-session';other.write_bytes(b'fixture executable')
        for pid,pgrp,exe in [(200,200,other),(201,200,cog),(202,999,cog)]:
            node=proc/str(pid);node.mkdir()
            (node/'stat').write_text(str(pid)+' (fixture) S 1 '+str(pgrp)+' '+str(pgrp)+' '+'0 '*15+'123 0')
            (node/'exe').symlink_to(exe)
        self.assertEqual(check.browser_process(self.process,proc),201)
        (proc/'201'/'stat').write_text('201 (fixture) Z 1 200 200 '+'0 '*15+'123 0')
        with self.assertRaisesRegex(RuntimeError,'one live Cog'):check.browser_process(self.process,proc)

    def test_pass_requires_geometry_swap_restore_same_processes_and_unchanged_state(self):
        snapshot,identities,command,geometry,emitted=self.context()
        result=check.rotation_runtime_check(object(),self.process,self.base)
        self.assertEqual(result['status'],'PASS');self.assertFalse(result['compositor_restarted']);self.assertFalse(result['browser_restarted'])
        self.assertEqual([call.args for call in command.call_args_list],[('Virtual-1','90'),('Virtual-1','normal')])
        self.assertEqual(geometry.call_args_list[1].args[3],(800,1280));self.assertEqual(geometry.call_args_list[2].args[3],(1280,800))
        self.assertEqual(emitted.call_args.args[0],'SURFACE_ROTATION_RUNTIME_PASS');self.assertEqual(snapshot.call_count,2)
        self.assertEqual(json.loads((self.base/'rotation.json').read_text())['compositor']['pid'],100)
    def test_rotation_failure_still_attempts_restore_and_cannot_emit_pass(self):
        _,_,command,geometry,emitted=self.context()
        geometry.side_effect=[self.base_geometry,RuntimeError('no actual logical resize'),self.base_geometry]
        with self.assertRaisesRegex(RuntimeError,'logical resize'):check.rotation_runtime_check(object(),self.process,self.base)
        self.assertEqual(command.call_args_list[-1].args,('Virtual-1','normal'));emitted.assert_not_called()
        self.assertFalse((self.base/'rotation.json').exists())
    def test_restarted_process_or_changed_resolution_state_fails(self):
        for changed in ('browser','compositor','state'):
            with self.subTest(changed=changed):
                snapshot,identities,command,geometry,emitted=self.context()
                if changed=='state':snapshot.side_effect=[{'saved':'before'},{'saved':'after'}]
                elif changed=='browser':
                    seen=[0]
                    def identity(pid,_):
                        if pid!=200:return self.compositor
                        seen[0]+=1;return self.browser if seen[0]==1 else dict(self.browser,start_ticks=456)
                    identities.side_effect=identity
                else:command.side_effect=[self.compositor,dict(self.compositor,start_ticks=999)]
                with self.assertRaises(RuntimeError):check.rotation_runtime_check(object(),self.process,self.base)
                emitted.assert_not_called()
    def test_physical_dmi_and_missing_module_cannot_run_or_pass(self):
        _,_,command,_,emitted=self.context()
        self.dmi.write_text('Surface Go')
        self.assertEqual(check.rotation_runtime_check(object(),self.process,self.base)['status'],'NOT_RUN')
        command.assert_not_called();emitted.assert_not_called()
        self.dmi.write_text(check.VM_NAME);self.module.unlink()
        with self.assertRaisesRegex(RuntimeError,'module is missing'):check.rotation_runtime_check(object(),self.process,self.base)
        command.assert_not_called();emitted.assert_not_called()
    def test_geometry_endpoint_authenticates_bounds_and_records_real_measurements(self):
        server=check.PageServer('fixture');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join,2);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        for query,status in [('token=wrong&width=1280&height=800&screen_width=1280&screen_height=800',403),
                             ('token=fixture&width=0&height=800&screen_width=1280&screen_height=800',400),
                             ('token=fixture&width=1280&height=800&screen_width=1280&screen_height=800&extra=1',400)]:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/geometry?'+query,timeout=2)
            self.assertEqual(error.exception.code,status);error.exception.close()
        self.assertFalse(server.geometries)
        with urllib.request.urlopen(base+'/geometry?token=fixture&width=1280&height=800&screen_width=1280&screen_height=800',timeout=2) as response:
            self.assertEqual(response.status,204)
        self.assertEqual(server.geometries[-1]['width'],1280);self.assertEqual(server.geometries[-1]['height'],800)
        self.assertEqual(server.geometries.maxlen,64)
    def test_wait_requires_two_fresh_consistent_client_reports_and_live_process(self):
        server=SimpleNamespace(geometry_condition=threading.Condition(),geometries=collections.deque([
            dict(self.base_geometry,at=9.),dict(self.portrait,at=10.1),dict(self.portrait,at=10.5)]))
        with patch.object(check.time,'monotonic',return_value=11.):
            self.assertEqual(check.wait_browser_geometry(server,self.process,10.,(800,1280))['width'],800)
        exited=SimpleNamespace(poll=lambda:1)
        with self.assertRaisesRegex(RuntimeError,'browser exited'):check.wait_browser_geometry(server,exited,10.)
        server.geometries=collections.deque([dict(self.portrait,at=8.),dict(self.portrait,at=9.)])
        with self.assertRaisesRegex(RuntimeError,'expected stable'):
            check.wait_browser_geometry(server,self.process,10.,(800,1280),timeout=.001)
    def test_saved_configuration_snapshot_rejects_links_and_detects_content_changes(self):
        path=self.base/'display.json';path.write_text('{"mode":"preferred"}')
        with patch.object(check,'DISPLAY_STATE',(path,self.base/'missing')):
            first=check.display_state_snapshot();self.assertIsNone(first[str(self.base/'missing')])
            path.write_text('{"mode":"1280x800"}')
            self.assertNotEqual(check.display_state_snapshot(),first)
            path.unlink();path.symlink_to(self.dmi)
            with self.assertRaises(OSError):check.display_state_snapshot()


if __name__=='__main__':unittest.main()
