"""Synthetic IIO/socket/service fixtures; no host sensor or display is touched."""
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import signal
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rotation_display_fixture',ROOT/'services/display.py')
display=importlib.util.module_from_spec(spec);spec.loader.exec_module(display)
rotation=display.orientation


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.iio=self.base/'iio';self.iio.mkdir();self.udev=self.base/'udev';self.udev.mkdir()
        self.outputs=[dict(name='eDP-1',modes=['preferred','1800x1200','1280x800'],current='1800x1200')]
        self.settings=display.Settings(self.base/'config/display.json',self.base/'run/display')
    def sensor(self,index=0,label='',matrix=None):
        node=self.iio/f'iio:device{index}';node.mkdir()
        for axis,value in zip('xyz',('0','-981','0')):(node/f'in_accel_{axis}_raw').write_text(value)
        (node/'in_accel_scale').write_text('0.01');(node/'name').write_text('Synthetic accelerometer')
        if label:(node/'label').write_text(label)
        if matrix:(node/'in_accel_mount_matrix').write_text(matrix)
        return node
    def test_axis_scale_offset_and_matrix_are_applied_in_that_order(self):
        node=self.sensor(matrix='0, 1, 0; -1, 0, 0; 0, 0, 1')
        (node/'in_accel_x_raw').write_text('100');(node/'in_accel_x_scale').write_text('0.02')
        (node/'in_accel_offset').write_text('1');(node/'in_accel_x_offset').write_text('-100')
        value=rotation.sample(self.iio,self.udev)
        self.assertEqual(value['matrix_source'],'in_accel_mount_matrix')
        self.assertEqual(value['vector'],[-9.8,0.,.01]);self.assertTrue(value['available'])
    def test_udev_override_and_display_location_take_precedence(self):
        base=self.sensor(0,label='accel-base');self.sensor(1,label='accel-display',matrix='1,0,0;0,1,0;0,0,1')
        node=self.iio/'iio:device1';(node/'dev').write_text('243:1')
        (self.udev/'c243:1').write_text('E:ACCEL_MOUNT_MATRIX=-1,0,0;0,-1,0;0,0,1\nE:ACCEL_LOCATION=display\n')
        value=rotation.sample(self.iio,self.udev)
        self.assertEqual(value['sensor'],'iio:device1');self.assertEqual(value['vector'],[0.,9.81,0.])
        self.assertEqual(value['matrix_source'],'udev ACCEL_MOUNT_MATRIX')
    def test_missing_or_ambiguous_sensor_never_fabricates_orientation(self):
        with self.assertRaisesRegex(ValueError,'No supported'):rotation.sample(self.iio,self.udev)
        self.sensor(0);self.sensor(1)
        with self.assertRaisesRegex(ValueError,'Multiple'):rotation.sample(self.iio,self.udev)
    def test_missing_firmware_matrix_reports_standard_identity_explicitly(self):
        self.sensor();value=rotation.sample(self.iio,self.udev)
        self.assertEqual(value['vector'],[0.,-9.81,0.]);self.assertIn('no firmware',value['matrix_source'])
    def test_bad_matrix_scale_nan_and_oversized_attributes_are_rejected(self):
        for text in ['1,0;0,1;0,0','1,0,0;1,0,0;0,0,1','nan,0,0;0,1,0;0,0,1','2,0,0;0,1,0;0,0,1']:
            with self.subTest(text=text),self.assertRaises(ValueError):rotation.parse_matrix(text)
        node=self.sensor()
        for value in ['0','-1','nan','9'*600]:
            (node/'in_accel_scale').write_text(value)
            with self.subTest(scale=value[:8]),self.assertRaises(ValueError):rotation.sample(self.iio,self.udev)
    def test_debounce_rejects_motion_diagonals_flat_and_requires_three_stable_samples(self):
        for vector in [[0.,0.,9.81],[7.,7.,0.],[0.,-20.,0.],[0.,0.,0.]]:
            filter=rotation.Debounce()
            for now in (0.,.6,1.3,2.):self.assertIsNone(filter.update(vector,'normal',now))
        for vector,target in [([9.81,0.,0.],'90'),([-9.81,0.,0.],'270'),([0.,9.81,0.],'180'),([0.,-9.81,0.],'normal')]:
            filter=rotation.Debounce();current='180' if target=='normal' else 'normal'
            self.assertIsNone(filter.update(vector,current,0.));self.assertIsNone(filter.update(vector,current,.6))
            self.assertEqual(filter.update(vector,current,1.3),target)
            self.assertIsNone(filter.update(vector,target,1.5))
    def test_preference_persists_separately_without_clobbering_resolution_trial(self):
        display.write_json(self.settings.config,dict(output='eDP-1',mode='1800x1200'))
        self.settings.set('eDP-1','1280x800',self.outputs);pending=self.settings.pending.read_bytes()
        display.write_json(self.settings.rotation_state,dict(transform='90'))
        self.settings.set_rotation(False)
        self.assertFalse(self.settings.rotation_preference()['enabled'])
        self.assertEqual(self.settings.rotation_preference()['locked_transform'],'90')
        self.assertEqual(self.settings.pending.read_bytes(),pending)
        self.assertEqual(self.settings.saved(self.outputs)['mode'],'1800x1200')
        self.assertEqual(stat.S_IMODE(self.settings.rotation_config.stat().st_mode),0o600)
    def test_live_rotation_and_trial_freeze_do_not_spawn_or_restart_applications(self):
        worker=Mock();worker.poll.return_value=dict(available=True,vector=[9.81,0.,0.],sensor='fixture',matrix_source='fixture')
        with patch.object(rotation,'SensorWorker',return_value=worker),patch.object(rotation.MODULE.__class__,'is_file',return_value=True), \
             patch.object(rotation,'apply_transform') as apply, \
             patch.object(display.subprocess,'Popen',side_effect=AssertionError('Rotation must preserve running processes')):
            controller=display.Rotation(self.settings)
            for now in (0.,.6,1.3):controller.update(self.outputs,dict(deadline=45),now)
            apply.assert_not_called();self.assertTrue(self.settings.rotation_status()['rotation_suspended'])
            for now in (2.,2.6,3.3):controller.update(self.outputs,None,now)
            apply.assert_called_once_with('eDP-1','90')
            self.assertEqual(self.settings.rotation_status()['rotation_transform'],'90')
            for now in (3.8,4.4,5.):controller.update(self.outputs,None,now)
            self.assertEqual(apply.call_count,1)
    def test_module_failure_cooldown_and_missing_sensor_do_not_restart_compositor(self):
        worker=Mock();worker.poll.return_value=dict(available=True,vector=[9.81,0.,0.])
        with patch.object(rotation,'SensorWorker',return_value=worker),patch.object(Path,'is_file',return_value=True), \
             patch.object(rotation,'apply_transform',side_effect=OSError('offline')) as apply:
            controller=display.Rotation(self.settings)
            for now in (0.,.6,1.3,2.,2.6,3.3,4.,4.6,5.3):controller.update(self.outputs,None,now)
            self.assertEqual(apply.call_count,1);self.assertEqual(controller.current,'normal')
            worker.poll.return_value=dict(available=False,message='Sensor unplugged')
            controller.update(self.outputs,None,6.)
            self.assertFalse(self.settings.rotation_status()['rotation_available'])
            self.assertIn('unplugged',self.settings.rotation_status()['rotation_message'])
    def test_live_socket_protocol_uses_actual_bounded_literal_request_and_ack(self):
        path=self.base/'rotation.sock';server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.addCleanup(server.close);server.bind(str(path));path.chmod(0o600);server.listen(1);seen=[]
        def receive():
            connection,_=server.accept()
            with connection:seen.append(connection.recv(256));connection.send(b'OK')
        thread=threading.Thread(target=receive);thread.start()
        rotation.apply_transform('eDP-1','270',path);thread.join(timeout=2)
        self.assertFalse(thread.is_alive());self.assertEqual(seen,[b'eDP-1 270'])
        path.chmod(0o666)
        with self.assertRaisesRegex(ValueError,'private'):rotation.apply_transform('eDP-1','90',path)
        for name,transform in [('HDMI-A-1','90'),('eDP-1\nmode=off','90'),('eDP-1','360')]:
            with self.assertRaises(ValueError):rotation.apply_transform(name,transform,path)
    def test_stalled_sensor_worker_is_killed_without_blocking_or_forking_more_workers(self):
        read,write=os.pipe();self.addCleanup(os.close,write)
        process=Mock(pid=12345,stdout=os.fdopen(read,'rb',buffering=0));process.poll.return_value=None
        with patch.object(rotation.subprocess,'Popen',return_value=process) as spawn,patch.object(rotation.os,'killpg') as kill:
            worker=rotation.SensorWorker();self.addCleanup(worker.stop)
            self.assertIsNone(worker.poll(0.))
            os.write(write,json.dumps(dict(available=True,vector=[0.,-9.81,0.])).encode()+b'\n')
            self.assertTrue(worker.poll(.5)['available'])
            value=worker.poll(8.);self.assertFalse(value['available']);self.assertIn('timed out',value['message'])
            for now in (9.,20.,100.):self.assertIsNone(worker.poll(now))
            self.assertEqual(spawn.call_count,1);kill.assert_called_once()
            process.poll.return_value=-9;worker.poll(101.)
    def test_supervisor_keeps_same_weston_and_home_processes_during_live_rotation(self):
        self.settings.runtime.parent.mkdir(mode=0o700)
        serial=self.base/'serial';serial.touch();dmi=self.base/'dmi';dmi.write_text('physical fixture')
        clock=[100.];starts=[];signals={};applied=[]
        worker=Mock();worker.poll.return_value=dict(available=True,vector=[9.81,0.,0.],sensor='fixture')
        class Process:
            def __init__(self,argv,**kwargs):self.argv=argv;self.pid=5000+len(starts);self.returncode=None;starts.append(self)
            def poll(self):return self.returncode
        def sleep(seconds):
            clock[0]+=seconds
            if applied:signals[signal.SIGTERM](signal.SIGTERM,None)
            if clock[0]>110:raise AssertionError('Rotation did not complete')
        def stop(process):
            if process:process.returncode=0
        with patch.object(display,'SERIAL',serial),patch.object(display,'VM_DMI',dmi), \
             patch.object(rotation,'SensorWorker',return_value=worker),patch.object(Path,'is_file',return_value=True), \
             patch.object(rotation,'apply_transform',side_effect=lambda *args:applied.append(args)), \
             patch.object(display,'inventory',return_value=self.outputs),patch.object(display.subprocess,'Popen',side_effect=Process), \
             patch.object(display.subprocess,'run',side_effect=AssertionError('No hardware acceptance commands')), \
             patch.object(display,'terminate',side_effect=stop),patch.object(Path,'is_socket',return_value=True), \
             patch.object(display.time,'monotonic',side_effect=lambda:clock[0]),patch.object(display.time,'sleep',side_effect=sleep), \
             patch.object(display.signal,'signal',side_effect=lambda number,handler:signals.__setitem__(number,handler)):
            display.session(self.settings,'gl')
        self.assertEqual(applied,[('eDP-1','90')])
        self.assertEqual(len(starts),2)
        self.assertEqual(starts[0].argv[0],'weston');self.assertEqual(starts[1].argv,['/usr/bin/surface-shell'])
        self.assertIn('--modules='+str(rotation.MODULE),starts[0].argv)
        self.assertFalse(self.settings.config.exists());self.assertFalse(self.settings.pending.exists())
    def test_stale_reading_is_reported_unavailable_without_changing_orientation(self):
        worker=Mock();worker.poll.side_effect=[dict(available=True,vector=[9.81,0.,0.]),None,None]
        with patch.object(rotation,'SensorWorker',return_value=worker),patch.object(Path,'is_file',return_value=True), \
             patch.object(rotation,'apply_transform') as apply:
            controller=display.Rotation(self.settings)
            controller.update(self.outputs,None,0.);controller.update(self.outputs,None,1.);controller.update(self.outputs,None,3.)
            apply.assert_not_called();self.assertFalse(self.settings.rotation_status()['rotation_available'])
            self.assertIn('fresh',self.settings.rotation_status()['rotation_message'])
    def test_all_rotation_cli_commands_remain_guest_guarded(self):
        with patch.object(display,'guard',side_effect=ValueError('guest required')),patch.object(rotation,'monitor') as monitor, \
             patch.object(display,'write_json') as write,patch('sys.stdout'):
            for command in [['--autorotate','on'],['--autorotate','off'],['--rotation-monitor']]:self.assertEqual(display.main(command),2)
            write.assert_not_called();monitor.assert_not_called()


if __name__=='__main__':unittest.main()
