import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hardware_config', ROOT / 'scripts/check-hardware-config.py')
hardware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hardware)


class HardwareConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'artifacts/tests')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.values = {'CONFIG_' + name: 'y' for names in hardware.REQUIRED.values() for name in names}

    def test_resolved_parent_dependency_cannot_be_replaced_by_hid_child(self):
        self.values['CONFIG_I2C_DESIGNWARE_PLATFORM'] = 'n'
        failures = hardware.check(self.values)
        self.assertEqual([item['symbol'] for item in failures], ['CONFIG_I2C_DESIGNWARE_PLATFORM'])
        self.assertEqual(failures[0]['group'], 'touch-controller')

    def test_pairing_support_without_hid_transport_is_rejected(self):
        del self.values['CONFIG_UHID']
        self.values['CONFIG_BT_HIDP'] = 'n'
        failures = hardware.check(self.values)
        self.assertEqual({item['symbol'] for item in failures}, {'CONFIG_UHID', 'CONFIG_BT_HIDP'})

    def test_unloaded_module_does_not_satisfy_builtin_sensor_requirement(self):
        self.values['CONFIG_HID_SENSOR_ACCEL_3D'] = 'm'
        self.assertEqual(hardware.check(self.values)[0]['resolved'], 'm')

    def test_advertised_console_drivers_cannot_disappear_with_led_parents(self):
        for symbol in ('NEW_LEDS', 'LEDS_CLASS_MULTICOLOR', 'HID_SONY', 'HID_NINTENDO', 'HID_PLAYSTATION'):
            with self.subTest(symbol=symbol):
                config = dict(self.values)
                config.pop('CONFIG_' + symbol)
                failures = hardware.check(config)
                self.assertEqual(failures[0]['group'], 'console-gamepad')
                self.assertEqual(failures[0]['symbol'], 'CONFIG_' + symbol)

    def test_firmware_missing_empty_or_symlink_is_rejected(self):
        directory = self.root / 'lib/firmware/qca'
        directory.mkdir(parents=True)
        for name in hardware.FIRMWARE:
            (self.root / 'lib/firmware' / name).write_bytes(b'fixture firmware')
        self.assertEqual(hardware.check(self.values, self.root), [])
        first = self.root / 'lib/firmware' / hardware.FIRMWARE[0]
        first.write_bytes(b'')
        self.assertEqual(hardware.check(self.values, self.root)[0]['group'], 'bluetooth-firmware')
        first.unlink()
        first.symlink_to(directory / Path(hardware.FIRMWARE[1]).name)
        self.assertEqual(hardware.check(self.values, self.root)[0]['file'], hardware.FIRMWARE[0])

    def test_cli_pass_never_claims_physical_validation(self):
        config = self.root / 'resolved.config'
        config.write_text('\n'.join(name + '=' + value for name, value in self.values.items()) + '\n')
        before = config.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            result = hardware.main(['--config', str(config)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'PASS')
        self.assertFalse(json.loads(output.getvalue())['physical_validation'])
        self.assertEqual(config.read_bytes(), before)

    def test_disabled_resolved_config_is_not_treated_as_selected(self):
        config = self.root / 'resolved.config'
        config.write_text('CONFIG_HID_SENSOR_ACCEL_3D=m\n# CONFIG_UHID is not set\nCONFIG_BT=y\n')
        values = hardware.read_config(config)
        self.assertEqual(values['CONFIG_UHID'], 'n')
        self.assertEqual(values['CONFIG_HID_SENSOR_ACCEL_3D'], 'm')
        self.assertTrue(hardware.check(values))

    def test_selected_bluez_plugins_do_not_accept_an_old_disabled_daemon(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID': 'y', 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG': 'y'}
        log = 'Invocation command line was\n\n  $ ./configure --disable-hid --disable-hog\n'
        failures = hardware.check_bluez(config, log)
        self.assertEqual({item.get('plugin') for item in failures}, {'hid', 'hog'})

    def test_actual_bluez_command_requires_both_selected_plugins(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID': 'y'}
        log = '  $ ./configure --enable-hid --enable-hog --disable-hid2hci\n'
        self.assertEqual(hardware.check_bluez(config, log)[0]['symbol'], 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG')
        config['BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG'] = 'y'
        self.assertEqual(hardware.check_bluez(config, log), [])

    def test_bluez_flags_in_diagnostics_and_conflicting_flags_cannot_pass(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID': 'y', 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG': 'y'}
        for log in ('help lists --enable-hid --enable-hog\n',
                    '  $ ./configure --enable-hid --enable-hog --disable-hid\n',
                    '  $ ./configure --enable-hid --enable-hog --enable-hog=no\n'):
            with self.subTest(log=log):
                self.assertTrue(hardware.check_bluez(config, log))

    def test_cli_bluez_evidence_pair_checks_resolved_buildroot_symbols(self):
        kernel = self.root / 'kernel.config'
        kernel.write_text('\n'.join(name + '=' + value for name, value in self.values.items()) + '\n')
        buildroot = self.root / 'buildroot.config'
        buildroot.write_text('BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID=y\nBR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG=y\n')
        log = self.root / 'config.log'
        log.write_text('  $ ./configure --enable-hid --enable-hog\n')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(hardware.main(['--config', str(kernel), '--buildroot-config', str(buildroot), '--bluez-config-log', str(log)]), 0)
            self.assertEqual(hardware.main(['--config', str(kernel), '--buildroot-config', str(buildroot)]), 1)


if __name__ == '__main__':
    unittest.main()
