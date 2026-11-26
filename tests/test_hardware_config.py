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
        self.values.update({'CONFIG_' + name: 'm' for name in hardware.MODULES})

    def module_tree(self):
        directory = self.root / 'lib/modules/fixture'
        module = directory / 'kernel/drivers/bluetooth/btusb.ko'
        module.parent.mkdir(parents=True)
        module.write_bytes(b'\x7fELFfixture')
        (directory / 'modules.dep').write_text('kernel/drivers/bluetooth/btusb.ko:\n')
        (directory / 'modules.alias').write_text('alias usb:v*p*d*dcE0dsc01dp01ic*isc*ip*in* btusb\n')
        config = self.root / 'etc/modules-load.d/bluetooth.conf'
        config.parent.mkdir(parents=True)
        config.write_text('btusb\n')
        init = self.root / 'etc/init.d/S11modules'
        init.parent.mkdir(parents=True)
        init.write_text('#!/bin/sh\n')
        init.chmod(0o755)
        return directory

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

    def test_aes_algorithms_without_userspace_crypto_api_are_rejected(self):
        # Regression for the shipped kernel: AES/ECB/CMAC existed, but the
        # AF_ALG endpoints used by BlueZ were disabled after olddefconfig.
        for symbol in ('CRYPTO_USER_API', 'CRYPTO_USER_API_HASH', 'CRYPTO_USER_API_SKCIPHER'):
            self.values['CONFIG_' + symbol] = 'n'
        failures = hardware.check(self.values)
        self.assertEqual({item['symbol'] for item in failures}, {
            'CONFIG_CRYPTO_USER_API', 'CONFIG_CRYPTO_USER_API_HASH', 'CONFIG_CRYPTO_USER_API_SKCIPHER'})
        self.assertTrue(all(item['group'] == 'bluetooth-crypto' for item in failures))

    def test_bluetooth_crypto_must_be_available_without_module_loading(self):
        for symbol in ('CRYPTO_USER_API', 'CRYPTO_USER_API_HASH', 'CRYPTO_USER_API_SKCIPHER',
                       'CRYPTO_AES', 'CRYPTO_ECB', 'CRYPTO_CMAC'):
            for resolved in ('n', 'm', None):
                with self.subTest(symbol=symbol, resolved=resolved):
                    values = dict(self.values)
                    if resolved is None:
                        values.pop('CONFIG_' + symbol)
                    else:
                        values['CONFIG_' + symbol] = resolved
                    failures = hardware.check(values)
                    self.assertEqual(len(failures), 1)
                    self.assertEqual(failures[0]['group'], 'bluetooth-crypto')
                    self.assertEqual(failures[0]['symbol'], 'CONFIG_' + symbol)
                    self.assertEqual(failures[0]['required'], 'y')

    def test_cli_rejects_resolved_kernel_with_disabled_af_alg(self):
        config = self.root / 'resolved.config'
        config.write_text(''.join(name + '=' + value + '\n' for name, value in self.values.items()
                                  if name != 'CONFIG_CRYPTO_USER_API_HASH') +
                          '# CONFIG_CRYPTO_USER_API_HASH is not set\n')
        before = config.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            result = hardware.main(['--config', str(config)])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['failures'], [dict(group='bluetooth-crypto',
            symbol='CONFIG_CRYPTO_USER_API_HASH', resolved='n', required='y')])
        self.assertFalse(report['physical_validation'])
        self.assertEqual(config.read_bytes(), before)

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
        self.module_tree()
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

    def test_builtin_usb_bluetooth_is_rejected_without_early_firmware(self):
        self.values['CONFIG_BT_HCIBTUSB'] = 'y'
        self.assertEqual(hardware.check(self.values)[0]['group'], 'bluetooth-firmware-order')

    def test_selected_module_without_installed_object_is_rejected(self):
        failures = hardware.check(self.values, self.root)
        self.assertTrue(any(item['group'] == 'bluetooth-module' for item in failures))

    def test_module_requires_dependency_alias_and_rootfs_startup(self):
        directory = self.module_tree()
        firmware = self.root / 'lib/firmware'
        for name in hardware.FIRMWARE:
            path = firmware / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        self.assertEqual(hardware.check(self.values, self.root), [])
        for relative in ('lib/modules/fixture/modules.alias', 'lib/modules/fixture/modules.dep',
                         'etc/modules-load.d/bluetooth.conf', 'etc/init.d/S11modules'):
            path = self.root / relative
            previous = path.read_text()
            path.write_text('')
            if path.name == 'S11modules':
                path.chmod(0o644)
            with self.subTest(path=relative):
                self.assertTrue(hardware.check(self.values, self.root))
            path.write_text(previous)
            if path.name == 'S11modules':
                path.chmod(0o755)

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
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_' + name.upper(): 'y' for name in hardware.BLUEZ_PLUGINS}
        log = 'Invocation command line was\n\n  $ ./configure --disable-hid --disable-hog --disable-sixaxis\n'
        failures = hardware.check_bluez(config, log)
        self.assertEqual({item.get('plugin') for item in failures}, {'hid', 'hog', 'sixaxis'})

    def test_actual_bluez_command_requires_all_selected_plugins(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID': 'y'}
        log = '  $ ./configure --enable-hid --enable-hog --enable-sixaxis --disable-hid2hci\n'
        self.assertEqual(hardware.check_bluez(config, log)[0]['symbol'], 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG')
        config['BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG'] = 'y'
        self.assertEqual(hardware.check_bluez(config, log)[0]['symbol'], 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_SIXAXIS')
        config['BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_SIXAXIS'] = 'y'
        self.assertEqual(hardware.check_bluez(config, log), [])

    def test_selected_sixaxis_requires_daemon_reconfiguration(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_' + name.upper(): 'y' for name in hardware.BLUEZ_PLUGINS}
        failures = hardware.check_bluez(config, '  $ ./configure --enable-hid --enable-hog --disable-sixaxis\n')
        self.assertEqual(failures, [dict(group='bluetooth-plugins', plugin='sixaxis',
            required='--enable-sixaxis without a disabling alternative')])

    def test_bluez_flags_in_diagnostics_and_conflicting_flags_cannot_pass(self):
        config = {'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_' + name.upper(): 'y' for name in hardware.BLUEZ_PLUGINS}
        for log in ('help lists --enable-hid --enable-hog --enable-sixaxis\n',
                    '  $ ./configure --enable-hid --enable-hog --enable-sixaxis --disable-hid\n',
                    '  $ ./configure --enable-hid --enable-hog --enable-sixaxis --enable-hog=no\n',
                    '  $ ./configure --enable-hid --enable-hog --enable-sixaxis --disable-sixaxis\n'):
            with self.subTest(log=log):
                self.assertTrue(hardware.check_bluez(config, log))

    def test_cli_bluez_evidence_pair_checks_resolved_buildroot_symbols(self):
        kernel = self.root / 'kernel.config'
        kernel.write_text('\n'.join(name + '=' + value for name, value in self.values.items()) + '\n')
        buildroot = self.root / 'buildroot.config'
        buildroot.write_text(''.join('BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_' + name.upper() + '=y\n'
                                    for name in hardware.BLUEZ_PLUGINS))
        log = self.root / 'config.log'
        log.write_text('  $ ./configure --enable-hid --enable-hog --enable-sixaxis\n')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(hardware.main(['--config', str(kernel), '--buildroot-config', str(buildroot), '--bluez-config-log', str(log)]), 0)
            self.assertEqual(hardware.main(['--config', str(kernel), '--buildroot-config', str(buildroot)]), 1)


if __name__ == '__main__':
    unittest.main()
