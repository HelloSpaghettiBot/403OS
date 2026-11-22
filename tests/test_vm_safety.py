import importlib.util
from pathlib import Path
import unittest
import subprocess
from contextlib import redirect_stderr
import io
from unittest.mock import Mock,patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vm", ROOT / "scripts/vm.py")
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


class DiskSafetyTests(unittest.TestCase):
    def test_companion_disk_is_always_an_ordinary_disposable_overlay(self):
        for source in (Path('/dev/sda'),Path('/tmp/other.qcow2')):
            with self.assertRaises(ValueError):
                vm.companion_overlay(vm.ARTIFACTS/'unit-test',source)
        directory=vm.ARTIFACTS/'unit-test';directory.mkdir(exist_ok=True)
        source=directory/'companion-source.qcow2';source.write_bytes(b'preserved')
        target=directory/'companion.qcow2'
        try:
            with patch.object(vm.subprocess,'run') as command:
                self.assertEqual(vm.companion_overlay(directory,source),target)
                self.assertIn('-b',command.call_args.args[0])
                self.assertEqual(command.call_args.args[0][-2:], [str(source),str(target)])
            self.assertEqual(source.read_bytes(),b'preserved')
        finally:source.unlink()

    def test_dual_disk_bus_options_reject_direct_kernel_install(self):
        for arguments in (['install','--usb-storage'],['install','--companion-disk','/dev/sda'],['live','--sata','--usb-storage']):
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):vm.parse_arguments(arguments)

    def test_update_acceptance_requires_a_disposable_explicit_companion(self):
        for arguments in (['live','--test-update-ui'],['test','--test-update-ui'],
                          ['live','--test-update-ui','--companion-disk','test.qcow2'],
                          ['live','--test-update-ui','--usb-storage','--companion-disk','test.qcow2','--test-install-ui']):
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):vm.parse_arguments(arguments)
        self.assertTrue(vm.parse_arguments(['live','--test-update-ui','--usb-storage','--companion-disk',str(vm.ARTIFACTS/'fixture.qcow2')]).test_update_ui)

    def test_existing_password_check_requires_an_installed_disk(self):
        for arguments in (['live','--expect-existing-profile'],['test','--expect-existing-profile']):
            with self.subTest(arguments=arguments),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                vm.parse_arguments(arguments)
        self.assertTrue(vm.parse_arguments(['test','--disk',str(vm.ARTIFACTS/'installed.qcow2'),'--expect-existing-profile']).expect_existing_profile)

    def test_profile_acceptance_requires_the_real_graphical_protocol(self):
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            vm.parse_arguments(['install','--test-profiles'])
        self.assertTrue(vm.parse_arguments(['live','--test-profiles']).test_profiles)
        guest=Mock();guest.log.read_text.return_value='SURFACE_READY graphics=wayland\n'
        summary={'checks':{}}
        with self.assertRaisesRegex(RuntimeError,'profile UI'):
            vm.authenticate_profile(guest,summary,required=True)
        self.assertEqual(summary['checks'],{})

    def test_serial_json_waits_for_complete_record(self):
        guest=Mock();guest.process.poll.return_value=None
        guest.log.read_text.side_effect=['SURFACE_DISPLAY_VIEW {"trial_act','SURFACE_DISPLAY_VIEW {"trial_active":true}\n']
        with patch.object(vm.time,'sleep'):
            self.assertTrue(vm.display_view(guest,trial=True)['trial_active'])

    def test_display_trial_test_requires_explicit_installed_disk(self):
        for arguments in (['live','--test-display'],['install','--test-display'],['test','--test-display']):
            with self.subTest(arguments=arguments),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                vm.parse_arguments(arguments)
        args=vm.parse_arguments(['test','--disk',str(vm.ARTIFACTS/'installed.qcow2'),'--test-display'])
        self.assertTrue(args.test_display)

    def test_click_installer_runs_only_against_new_disposable_live_target(self):
        for arguments in (['test','--test-install-ui'],['install','--test-install-ui'],['live','--test-install-ui','--visible','--keep-open']):
            with self.subTest(arguments=arguments),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                vm.parse_arguments(arguments)
        self.assertTrue(vm.parse_arguments(['live','--test-install-ui']).test_install_ui)

    def test_devices_and_outside_files_rejected(self):
        for path in ["/dev/sda", "/dev/null", "/tmp/example.qcow2", vm.ARTIFACTS / "../escape.qcow2",
                     vm.ARTIFACTS / "disk,file=other.qcow2"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                vm.safe_file(path)

    def test_project_regular_path_allowed(self):
        path = vm.ARTIFACTS / "unit-test/installed.qcow2"
        self.assertEqual(vm.safe_file(path), path)

    def test_installer_refuses_host_execution(self):
        script = str(ROOT / "installer/install-vm.sh")
        without_confirmation = subprocess.run([script], capture_output=True, text=True)
        self.assertEqual(without_confirmation.returncode, 2)
        self.assertIn("Explicit VM confirmation required", without_confirmation.stdout)
        on_host = subprocess.run([script, "--confirm-disposable-vm"], capture_output=True, text=True)
        self.assertEqual(on_host.returncode, 2)
        self.assertIn("Missing VM boot guard", on_host.stdout)

    def test_symlink_escape_rejected(self):
        directory = vm.ARTIFACTS / "unit-test"
        directory.mkdir(parents=True, exist_ok=True)
        link = directory / "unsafe.qcow2"
        link.symlink_to("/dev/null")
        try:
            with self.assertRaises(ValueError):
                vm.safe_file(link)
        finally:
            link.unlink()

    def test_existing_virtual_disks_are_preserved(self):
        directory = vm.ARTIFACTS / "unit-test"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "preserve.qcow2"
        path.write_bytes(b"existing virtual disk sentinel")
        try:
            with self.assertRaises(FileExistsError):
                vm.create_disk(path)
            with self.assertRaises(FileExistsError):
                vm.create_live_disk(path, ROOT / "output/images/surface-go.img")
            self.assertEqual(path.read_bytes(), b"existing virtual disk sentinel")
        finally:
            path.unlink()
