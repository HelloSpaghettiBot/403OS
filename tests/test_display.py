"""Display fixtures: no host DRM, compositor, window, signal or service commands."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('surface_display_fixture',ROOT/'services/display.py')
display=importlib.util.module_from_spec(spec);spec.loader.exec_module(display)


class DisplaySettings(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.settings=display.Settings(self.base/'config/display.json',self.base/'run/display')
        self.outputs=[dict(name='eDP-1',card='card0',modes=['preferred','1800x1200','1280x800'],current='1800x1200')]
        self.clock=[100.0]
        timer=patch.object(display.time,'monotonic',side_effect=lambda:self.clock[0]);timer.start();self.addCleanup(timer.stop)

    def test_trial_changes_active_mode_but_only_confirmed_mode_persists(self):
        result=self.settings.set('eDP-1','1280x800',self.outputs)
        self.assertEqual(result['event'],'display-trial');self.assertFalse(self.settings.config.exists())
        status=self.settings.status(self.outputs)
        self.assertTrue(status['trial_active']);self.assertEqual(status['remaining_seconds'],45)
        self.assertEqual(status['trial_mode'],'1280x800');self.assertEqual(status['trial_output'],'eDP-1')
        self.assertEqual(self.settings.active(self.outputs)['mode'],'1280x800')
        self.outputs[0]['current']='1280x800'
        self.settings.confirm(self.outputs)
        self.assertEqual(self.settings.saved(self.outputs),dict(output='eDP-1',mode='1280x800'))
        self.assertEqual(stat.S_IMODE(self.settings.config.stat().st_mode),0o600)
        self.assertFalse(self.settings.status(self.outputs)['trial_active'])

    def test_timeout_revert_and_power_loss_keep_previous_confirmed_choice(self):
        display.write_json(self.settings.config,dict(output='eDP-1',mode='1800x1200'))
        self.settings.set('eDP-1','1280x800',self.outputs)
        self.clock[0]+=46
        self.assertEqual(self.settings.active(self.outputs)['mode'],'1800x1200')
        with self.assertRaisesRegex(ValueError,'unexpired'):self.settings.confirm(self.outputs)
        self.settings.revert();self.assertFalse(self.settings.pending.exists())
        # /run is lost on reboot; DATA retains only a confirmed selection.
        rebooted=display.Settings(self.settings.config,self.base/'new-run/display')
        self.assertEqual(rebooted.active(self.outputs)['mode'],'1800x1200')

    def test_unknown_mode_disconnected_output_and_configuration_injection_are_refused(self):
        for output,mode in (('eDP-1','9999x9999'),('HDMI-A-1','1280x800'),('eDP-1\nmode=off','preferred'),('eDP-1','1280x800\nmode=off')):
            with self.subTest(output=output,mode=mode),self.assertRaises(ValueError):self.settings.set(output,mode,self.outputs)
        self.assertFalse(self.settings.config.exists());self.assertFalse(self.settings.pending.exists())
        display.write_json(self.settings.config,dict(output='HDMI-A-1',mode='1280x800'))
        self.assertIsNone(self.settings.active(self.outputs))
        self.assertNotIn('mode=off',display.weston_config(self.outputs,None))

    def test_confirm_requires_actual_drm_crtc_resolution_not_just_request(self):
        self.settings.set('eDP-1','1280x800',self.outputs)
        for current in ('','1800x1200'):
            with self.subTest(current=current):
                self.outputs[0]['current']=current
                with self.assertRaisesRegex(ValueError,'DRM'):self.settings.confirm(self.outputs)
                self.assertFalse(self.settings.config.exists())
        self.settings.set('eDP-1','preferred',self.outputs);self.outputs[0]['current']='1800x1200'
        self.settings.confirm(self.outputs);self.assertEqual(self.settings.saved(self.outputs)['mode'],'preferred')

    def test_corrupt_or_symlinked_saved_state_falls_back_without_following_links(self):
        self.settings.config.parent.mkdir(mode=0o700)
        self.settings.config.write_text('corrupt');self.settings.config.chmod(0o600)
        self.assertIsNone(self.settings.active(self.outputs))
        self.settings.config.unlink();elsewhere=self.base/'untouched';elsewhere.write_text('OWNER SENTINEL')
        self.settings.config.symlink_to(elsewhere)
        self.assertIsNone(self.settings.active(self.outputs));self.assertEqual(elsewhere.read_text(),'OWNER SENTINEL')

    def test_weston_configuration_uses_selected_connector_and_native_other_outputs(self):
        outputs=self.outputs+[dict(name='HDMI-A-1',modes=['preferred','1920x1080'],current='1920x1080')]
        config=display.weston_config(outputs,dict(output='eDP-1',mode='1280x800'))
        self.assertIn('name=eDP-1\nmode=1280x800',config)
        self.assertIn('name=HDMI-A-1\nmode=preferred',config)
        self.assertIn('shell=kiosk-shell.so',config);self.assertIn('xwayland=true',config)

    def test_drm_inventory_uses_only_connected_advertised_modes_and_actual_crtc(self):
        root=self.base/'drm';root.mkdir()
        for name,connected,modes in [('card0-eDP-1','connected','1800x1200\n1280x800\n1280x800\n'),
                                     ('card0-HDMI-A-1','disconnected','1920x1080\n')]:
            node=root/name;node.mkdir()
            (node/'status').write_text(connected);(node/'modes').write_text(modes);(node/'connector_id').write_text('42')
        library=object()
        with patch.object(display,'drm_library',return_value=library),patch.object(display,'current_mode',return_value='1280x800') as current:
            outputs=display.inventory(root,self.base/'dri')
        self.assertEqual(len(outputs),1);self.assertEqual(outputs[0]['modes'],['preferred','1800x1200','1280x800'])
        self.assertEqual(outputs[0]['current'],'1280x800');current.assert_called_once_with(library,self.base/'dri/card0',42)

    def test_current_mode_reads_connector_encoder_crtc_and_frees_each_resource(self):
        library=SimpleNamespace(drmModeGetConnectorCurrent=Mock(return_value=ctypes.pointer(display.Connector(42,7))),
            drmModeGetEncoder=Mock(return_value=ctypes.pointer(display.Encoder(7,1,9))),
            drmModeGetCrtc=Mock(return_value=ctypes.pointer(display.Crtc(9,2,0,0,1280,800,1))),
            drmModeFreeConnector=Mock(),drmModeFreeEncoder=Mock(),drmModeFreeCrtc=Mock())
        with patch.object(display.os,'open',return_value=123),patch.object(display.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
             patch.object(display.os,'close') as close:
            self.assertEqual(display.current_mode(library,Path('/dev/fixture'),42),'1280x800')
        library.drmModeGetEncoder.assert_called_once_with(123,7);library.drmModeGetCrtc.assert_called_once_with(123,9)
        self.assertEqual(library.drmModeFreeConnector.call_count,1);self.assertEqual(library.drmModeFreeEncoder.call_count,1)
        self.assertEqual(library.drmModeFreeCrtc.call_count,1);close.assert_called_once_with(123)

    def test_host_cli_guard_precedes_library_loading_or_mutation(self):
        with patch.object(display,'guard',side_effect=ValueError('Guest image required')), \
             patch.object(display,'inventory',side_effect=AssertionError('No host DRM query')), \
             patch.object(display,'write_json',side_effect=AssertionError('No host setting writes')),patch('sys.stdout'):
            for arguments in (['--status'],['--set','eDP-1','1280x800'],['--confirm'],['--revert'],['--session']):
                self.assertEqual(display.main(arguments),2)

    def test_supervisor_restarts_real_command_paths_for_trial_then_rolls_back_on_timeout(self):
        self.settings.runtime.parent.mkdir(mode=0o700)
        serial=self.base/'serial';serial.touch();dmi=self.base/'dmi';dmi.write_text('physical fixture')
        starts=[];stopped=[];signals={};phase=[0]
        class Process:
            def __init__(self,argv,**keywords):
                self.argv=argv;self.pid=1000+len(starts);self.returncode=None;starts.append((argv,keywords))
            def poll(self):return self.returncode
        def stop(process):
            if process:process.returncode=0;stopped.append(process.argv)
        def sleep(seconds):
            self.clock[0]+=seconds
            if not any(argv[0]=='/usr/bin/surface-shell' for argv,_ in starts):return
            if phase[0]==0:
                self.settings.set('eDP-1','1280x800',self.outputs);phase[0]=1
            elif phase[0]==1 and len([argv for argv,_ in starts if argv[0]=='weston'])>=2:
                self.clock[0]+=46;phase[0]=2
            elif phase[0]==2 and len([argv for argv,_ in starts if argv[0]=='weston'])>=3:
                signals[signal.SIGTERM](signal.SIGTERM,None)
        with patch.object(display,'SERIAL',serial),patch.object(display,'VM_DMI',dmi), \
             patch.object(display,'inventory',side_effect=lambda:self.outputs),patch.object(display.subprocess,'Popen',side_effect=Process), \
             patch.object(display.subprocess,'run',side_effect=AssertionError('Hardware session must not run VM acceptance')), \
             patch.object(display,'terminate',side_effect=stop),patch.object(Path,'is_socket',return_value=True), \
             patch.object(display.time,'sleep',side_effect=sleep), \
             patch.object(display.signal,'signal',side_effect=lambda number,handler:signals.__setitem__(number,handler)), \
             patch.object(display,'Rotation'):
            display.session(self.settings,'pixman')
        weston=[argv for argv,_ in starts if argv[0]=='weston'];self.assertEqual(len(weston),3)
        shells=[kwargs for argv,kwargs in starts if argv[0]=='/usr/bin/surface-shell'];self.assertEqual(len(shells),3)
        self.assertTrue(all(kwargs['stdout'].name==str(serial) for kwargs in shells))
        self.assertIsNone(self.settings.saved(self.outputs));self.assertFalse(self.settings.pending.exists())
        self.assertIn('automatically',self.settings.status(self.outputs)['message'])
        self.assertIn('mode=preferred',(self.settings.runtime.parent/'weston-display.ini').read_text())
        self.assertEqual(len(stopped),6)


if __name__=='__main__':unittest.main()
