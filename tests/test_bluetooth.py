"""Bluetooth service fixtures: no host radio, D-Bus, USB or daemon operations."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('surface_bluetooth', ROOT / 'services/bluetooth.py')
bluetooth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bluetooth)


class BluetoothRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.rfkill = self.base / 'rfkill'
        self.rfkill.mkdir()
        self.devices = self.base / 'bluetooth'
        self.devices.mkdir()
        self.usb = self.base / 'usb'
        self.usb.mkdir()
        self.logs = self.base / 'logs'
        self.logs.mkdir()
        self.firmware = self.base / 'firmware'
        self.firmware.mkdir()
        self.messages = self.base / 'messages'
        self.adapter = dict(address='00:11:22:33:44:55', hci='hci0')
        for name, value in (('RFKILL', self.rfkill), ('BLUETOOTH', self.devices),
                            ('USB', self.usb), ('LOGS', self.logs), ('FIRMWARE', self.firmware), ('MESSAGES', self.messages)):
            patcher = patch.object(bluetooth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def radio(self, number, kind='bluetooth', soft='1', hard='0'):
        path = self.rfkill / ('rfkill' + str(number))
        path.mkdir()
        for name, value in (('type', kind), ('name', 'hci' + str(number)), ('soft', soft), ('hard', hard)):
            (path / name).write_text(value + '\n')
        return path

    def test_explicit_enable_only_unblocks_bluetooth_software(self):
        radio = self.radio(0)
        wifi = self.radio(1, 'wlan')
        wwan = self.radio(2, 'wwan')
        bluetooth.clear_software_blocks(self.adapter)
        self.assertEqual((radio / 'soft').read_text(), '0\n')
        self.assertEqual((radio / 'hard').read_text(), '0\n')
        self.assertEqual((wifi / 'soft').read_text(), '1\n')
        self.assertEqual((wwan / 'soft').read_text(), '1\n')

    def test_hardware_block_refuses_before_any_software_write(self):
        first = self.radio(0, hard='1')
        second = self.radio(1)
        with self.assertRaisesRegex(bluetooth.BluetoothError, 'hardware blocked'):
            bluetooth.clear_software_blocks(self.adapter)
        self.assertEqual((first / 'soft').read_text(), '1\n')
        self.assertEqual((second / 'soft').read_text(), '1\n')

    def test_status_is_read_only_and_reports_software_block(self):
        radio = self.radio(0)
        with patch.object(bluetooth, 'run') as run, patch.object(bluetooth, 'selected_adapter', return_value=self.adapter):
            self.assertIn('software blocked', bluetooth.status())
            run.assert_not_called()
        self.assertEqual((radio / 'soft').read_text(), '1\n')

    def test_unknown_rfkill_state_cannot_be_treated_as_unblocked(self):
        self.radio(0, soft='unknown')
        with self.assertRaisesRegex(bluetooth.BluetoothError, 'invalid'):
            bluetooth.clear_software_blocks(self.adapter)

    def test_start_already_running_race_uses_actual_api_readiness(self):
        run = Mock(side_effect=[bluetooth.BluetoothError('starting'),
                               (1, 'already running'), (0, 'Controller 00:11:22:33:44:55 fixture')])
        with patch.object(bluetooth, 'run', run):
            bluetooth.ensure_daemon()
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [[bluetooth.CLIENT, 'list'], [bluetooth.INIT, 'start'], [bluetooth.CLIENT, 'list']])
        self.assertTrue(all(0 < call.args[1] <= 3 for call in run.call_args_list))

    def test_running_daemon_is_not_restarted(self):
        with patch.object(bluetooth, 'run', return_value=(0, '')) as run:
            bluetooth.ensure_daemon()
        run.assert_called_once_with([bluetooth.CLIENT, 'list'], 2)

    def test_enable_requires_actual_power_readback(self):
        self.radio(0, soft='0')
        run = Mock(side_effect=[(0, ''), (0, 'Version 5.86'),
                               (0, 'Controller 00:11:22:33:44:55 fixture'),
                               (0, 'Controller 00:11:22:33:44:55 fixture\n\tPowered: no')])
        with patch.object(bluetooth, 'run', run), \
             patch.object(bluetooth, 'selected_adapter', return_value=self.adapter), \
             patch.object(bluetooth, 'controller_command', return_value='Changing power on succeeded'):
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'without a powered adapter'):
                bluetooth.power_on()
        self.assertEqual(run.call_args_list[-1].args[0], [bluetooth.CLIENT, 'show', self.adapter['address']])

    def test_enable_checks_output_and_powered_controller(self):
        self.radio(0)
        run = Mock(side_effect=[(0, ''), (0, 'Version 5.86'),
                               (0, 'Controller 00:11:22:33:44:55 fixture'),
                               (0, 'Controller 00:11:22:33:44:55 fixture\n\tPowered: yes')])
        with patch.object(bluetooth, 'run', run), \
             patch.object(bluetooth, 'selected_adapter', return_value=self.adapter), \
             patch.object(bluetooth, 'controller_command', return_value='Changing power on succeeded') as command:
            self.assertEqual(bluetooth.power_on(), 'Changing power on succeeded')
        command.assert_called_once_with(self.adapter['address'], 'power on')
        self.assertEqual((self.rfkill / 'rfkill0/soft').read_text(), '0\n')

    def test_blocked_internal_adapter_does_not_block_usable_usb(self):
        internal = self.radio(0, hard='1')
        external = self.radio(1)
        chosen = dict(address='00:11:22:33:44:77', hci='hci1')
        bluetooth.clear_software_blocks(chosen)
        self.assertEqual((external / 'soft').read_text(), '0\n')
        self.assertEqual((internal / 'soft').read_text(), '1\n')
        self.assertEqual((internal / 'hard').read_text(), '1\n')

    def test_adapter_inventory_selects_usable_adapter_and_honors_explicit_choice(self):
        self.radio(0, hard='1')
        self.radio(1, soft='0')
        def client(argv, timeout):
            if argv[1] == 'list':
                return 0, 'Controller 00:11:22:33:44:55 Internal [default]\nController 00:11:22:33:44:77 USB'
            return 0, 'Controller ' + argv[2] + ' fixture\n\tPowered: yes'
        mapping = {'00:11:22:33:44:55': 'hci0', '00:11:22:33:44:77': 'hci1'}
        with patch.object(bluetooth, 'run', side_effect=client), \
             patch.object(bluetooth, 'adapter_hci_names', return_value=mapping):
            inventory = bluetooth.adapters()
            self.assertEqual(inventory['selected'], '00:11:22:33:44:77')
            self.assertFalse(inventory['adapters'][0]['usable'])
            self.assertEqual(inventory['adapters'][0]['name'], 'Internal')
            self.assertTrue(inventory['adapters'][1]['usable'])
            self.assertEqual(bluetooth.adapters('00:11:22:33:44:55')['selected'], '00:11:22:33:44:55')
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'no longer available'):
                bluetooth.adapters('00:11:22:33:44:99')

    def test_command_validation_rejects_injected_or_unqualified_operations(self):
        with patch.object(bluetooth.subprocess, 'Popen') as client:
            for adapter, command, device in (
                    ('00:11:22:33:44:55\nremove 00:11:22:33:44:66', 'devices', None),
                    (self.adapter['address'], 'pair', '00:11:22:33:44:66'),
                    (self.adapter['address'], 'remove', '00:11:22:33:44:66\nquit')):
                with self.assertRaises(bluetooth.BluetoothError):
                    bluetooth.controller_command(adapter, command, device)
            client.assert_not_called()

    def test_empty_successful_list_waits_for_firmware_registration(self):
        run = Mock(side_effect=[(0, ''), (0, ''), (0, 'Controller 00:11:22:33:44:55 fixture')])
        with patch.object(bluetooth, 'run', run), patch.object(bluetooth.time, 'sleep'):
            self.assertIn('Controller ', bluetooth.wait_adapter())
        self.assertEqual(run.call_count, 3)

    def test_silent_success_is_client_failure_before_any_power_request(self):
        (self.devices / 'hci0').mkdir()
        with patch.object(bluetooth, 'run', return_value=(0, '')) as run:
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'command client'):
                bluetooth.power_on()
            self.assertNotIn([bluetooth.CLIENT, 'power', 'on'], [item.args[0] for item in run.call_args_list])
            self.assertIn('command client', bluetooth.status())

    def test_shell_version_check_does_not_use_bypassing_version_option(self):
        with patch.object(bluetooth, 'run', return_value=(0, 'Version 5.86')) as run:
            self.assertEqual(bluetooth.check_client_output(), 'Version 5.86')
        run.assert_called_once_with([bluetooth.CLIENT, 'version'], 3)

    def test_timeout_distinguishes_no_transport_from_hidden_setup_controller(self):
        with patch.object(bluetooth, 'run', return_value=(0, '')):
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'exposed by the kernel'):
                bluetooth.wait_adapter(seconds=0)
            (self.devices / 'hci0').mkdir()
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'adapter initialization'):
                bluetooth.wait_adapter(seconds=0)

    def test_status_distinguishes_pending_firmware_from_missing_hardware(self):
        (self.devices / 'hci0').mkdir()
        with patch.object(bluetooth, 'run', return_value=(0, 'No default controller available')):
            self.assertIn('adapter initialization', bluetooth.status())

    def test_private_diagnostics_capture_transport_errors_without_addresses(self):
        adapter = self.usb / '1-4'
        adapter.mkdir()
        (adapter / 'idVendor').write_text('0cf3\n')
        (adapter / 'idProduct').write_text('e302\n')
        (adapter / 'serial').write_text('MUST-NOT-READ')
        outputs = [(0, 'Controller 00:11:22:33:44:55 fixture'),
                   (0, 'Controller 00:11:22:33:44:55 fixture'),
                   (0, 'Version 5.86'),
                   (0, 'Bluetooth: hci0: firmware qca/nvm_usb_00000302.bin failed -2\nUnrelated kernel line')]
        self.messages.write_text('bluetoothd: Failed to open crypto\nOther service: unrelated\n')
        with patch.object(bluetooth, 'run', side_effect=outputs):
            bluetooth.save_diagnostics('bluetooth-boot.json', 'fixture')
        path = self.logs / 'bluetooth-boot.json'
        text = path.read_text()
        record = json.loads(text)
        self.assertNotIn('00:11:22:33:44:55', text)
        self.assertNotIn('MUST-NOT-READ', text)
        self.assertEqual(record['usb'][0]['product'], 'e302')
        self.assertEqual(record['daemon_messages'], ['bluetoothd: Failed to open crypto'])
        self.assertEqual(len(record['kernel_messages']), 1)
        self.assertIn('failed -2', record['kernel_messages'][0])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_boot_check_saves_failures_without_power_or_reset(self):
        with patch.object(bluetooth, 'guard'), patch.object(bluetooth, 'wait_adapter', side_effect=bluetooth.BluetoothError('fixture timeout')):
            with patch.object(bluetooth, 'save_diagnostics') as save, patch.object(bluetooth, 'power_on') as power:
                self.assertEqual(bluetooth.main(['--boot-check']), 0)
        save.assert_called_once_with('bluetooth-boot.json', 'fixture timeout')
        power.assert_not_called()

    def test_kernel_adapter_missing_is_distinct_from_daemon_unavailable(self):
        with patch.object(bluetooth, 'run', return_value=(0, 'No default controller available')):
            self.assertIn('exposed by the kernel', bluetooth.status())
        with patch.object(bluetooth, 'run', side_effect=bluetooth.BluetoothError('unavailable')):
            self.assertIn('service is not responding', bluetooth.status())

    def test_command_timeout_is_specific_and_does_not_fall_back(self):
        with patch.object(bluetooth.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 2)):
            with self.assertRaisesRegex(bluetooth.BluetoothError, 'timed out'):
                bluetooth.run(['fixture-only'], 2)

    def test_guard_failure_runs_no_radio_or_daemon_work(self):
        with patch.object(bluetooth, 'guard', side_effect=bluetooth.BluetoothError('not the guest')):
            with patch.object(bluetooth, 'power_on') as power:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(bluetooth.main(['--power-on']), 1)
                power.assert_not_called()

    def test_pairing_error_record_is_guarded_bounded_and_redacts_address(self):
        with patch.object(bluetooth, 'guard'), patch.object(bluetooth, 'save_diagnostics') as save:
            self.assertEqual(bluetooth.main(['--record-error', 'Pair failed 00:11:22:33:44:55\n' + 'x' * 300]), 0)
            filename, reason = save.call_args.args
            self.assertEqual(filename, 'bluetooth-last-error.json')
            self.assertNotIn('00:11', reason)
            self.assertNotIn('\n', reason)
            self.assertLessEqual(len(reason), 256)
        with patch.object(bluetooth, 'guard', side_effect=bluetooth.BluetoothError('not the guest')):
            with patch.object(bluetooth, 'save_diagnostics') as save, redirect_stdout(io.StringIO()):
                self.assertEqual(bluetooth.main(['--record-error', 'Pair failed']), 1)
                save.assert_not_called()

    def test_actual_host_entrypoint_refuses_before_radio_access(self):
        if Path('/etc/surface-image-id').exists():
            self.skipTest('inside the intended guest')
        result = subprocess.run(['python3', str(ROOT / 'services/bluetooth.py'), '--power-on'],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Bluetooth:', result.stdout)


if __name__ == '__main__':
    unittest.main()
