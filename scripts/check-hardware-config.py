#!/usr/bin/env python3
"""Reject images whose resolved kernel lacks the Surface input driver chain.

This checks configuration and shipped firmware only. It neither probes hardware
nor changes a device, and it never establishes physical compatibility.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import stat


REQUIRED = {
    'touch-controller': ('X86_INTEL_LPSS', 'MFD_INTEL_LPSS_ACPI', 'MFD_INTEL_LPSS_PCI',
                         'I2C_DESIGNWARE_CORE', 'I2C_DESIGNWARE_PLATFORM', 'PINCTRL_SUNRISEPOINT',
                         'I2C_HID_ACPI', 'HID_MULTITOUCH', 'INPUT_EVDEV'),
    'accelerometer': ('INTEL_ISH_HID', 'HID_SENSOR_HUB', 'IIO', 'IIO_BUFFER',
                      'HID_SENSOR_IIO_COMMON', 'HID_SENSOR_IIO_TRIGGER', 'HID_SENSOR_ACCEL_3D'),
    'bluetooth-gamepad': ('BT', 'BT_BREDR', 'BT_LE', 'BT_HIDP', 'FW_LOADER', 'RFKILL',
                          'UHID', 'HID_GENERIC', 'HIDRAW'),
    # BlueZ's bt_crypto_new binds both ecb(aes) and cmac(aes) via AF_ALG.
    'bluetooth-crypto': ('CRYPTO_USER_API', 'CRYPTO_USER_API_HASH', 'CRYPTO_USER_API_SKCIPHER', 'CRYPTO_USER_API_ENABLE_OBSOLETE',
                         'CRYPTO_AES', 'CRYPTO_ECB', 'CRYPTO_CMAC'),
    'console-gamepad': ('NEW_LEDS', 'LEDS_CLASS', 'LEDS_CLASS_MULTICOLOR',
                        'HID_SONY', 'HID_NINTENDO', 'HID_PLAYSTATION'),
    'additional-gamepads': (
        'HID_ACRUX', 'HID_ACRUX_FF', 'HID_BETOP_FF', 'HID_BIGBEN_FF',
        'HID_DRAGONRISE', 'HID_EMS_FF', 'HID_GEMBIRD', 'HID_GOOGLE_STADIA_FF',
        'HID_HOLTEK', 'HID_ICADE', 'HID_LOGITECH', 'HID_LOGITECH_DJ',
        'HID_LOGITECH_HIDPP', 'HID_MAYFLASH', 'HID_MEGAWORLD_FF', 'HID_NVIDIA_SHIELD',
        'HID_PANTHERLORD', 'HID_PXRC', 'HID_RETRODE', 'HID_SAITEK',
        'HID_SPEEDLINK', 'HID_STEAM', 'HID_STEELSERIES', 'HID_GREENASIA',
        'HID_SMARTJOYPLUS', 'HID_THRUSTMASTER', 'HID_UDRAW_PS3', 'HID_PID',
        'HID_UNIVERSAL_PIDFF', 'HID_WIIMOTE', 'HID_WINWING', 'HID_XINMO',
        'HID_ZEROPLUS', 'JOYSTICK_XPAD_FF', 'JOYSTICK_XPAD_LEDS', 'JOYSTICK_PXRC',
    ),
    'tablet-buttons': ('INPUT_MISC', 'KEYBOARD_GPIO', 'INPUT_SOC_BUTTON_ARRAY', 'INTEL_HID_EVENT'),
}
FIRMWARE = ('qca/rampatch_usb_00000302.bin', 'qca/nvm_usb_00000302.bin')
MODULES = {'BT_HCIBTUSB': 'btusb'}


def read_config(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if match := re.fullmatch(r'((?:CONFIG_|BR2_)[A-Z0-9_]+)=(.*)', line):
            values[match[1]] = match[2]
        elif match := re.fullmatch(r'# ((?:CONFIG_|BR2_)[A-Z0-9_]+) is not set', line):
            values[match[1]] = 'n'
    return values


def check(config, target=None):
    missing = []
    for group, symbols in REQUIRED.items():
        for symbol in symbols:
            name = 'CONFIG_' + symbol
            if config.get(name) != 'y':
                missing.append(dict(group=group, symbol=name, resolved=config.get(name, 'absent'), required='y'))
    for symbol in MODULES:
        name = 'CONFIG_' + symbol
        if config.get(name) != 'm':
            missing.append(dict(group='bluetooth-firmware-order', symbol=name,
                                resolved=config.get(name, 'absent'), required='m'))
    if target is not None:
        for name in FIRMWARE:
            path = Path(target) / 'lib/firmware' / name
            try:
                info = path.lstat()
                valid = stat.S_ISREG(info.st_mode) and info.st_size > 0
            except OSError:
                valid = False
            if not valid:
                missing.append(dict(group='bluetooth-firmware', file=name, required='nonempty regular file'))
        target = Path(target)
        for name in MODULES.values():
            modules = list((target / 'lib/modules').glob('*/kernel/drivers/bluetooth/' + name + '.ko'))
            valid = len(modules) == 1 and modules[0].is_file() and not modules[0].is_symlink()
            if valid:
                valid = modules[0].read_bytes()[:4] == b'\x7fELF'
            if not valid:
                missing.append(dict(group='bluetooth-module', required='one installed ELF ' + name + '.ko'))
                continue
            directory = modules[0].parents[3]
            dependency = directory / 'modules.dep'
            aliases = directory / 'modules.alias'
            if not dependency.is_file() or not re.search(r'^kernel/drivers/bluetooth/' + name + r'\.ko:', dependency.read_text(), re.M):
                missing.append(dict(group='bluetooth-module', required=name + ' in modules.dep'))
            if not aliases.is_file() or not re.search(r'^alias usb:.* ' + name + r'$', aliases.read_text(), re.M):
                missing.append(dict(group='bluetooth-module', required='USB modalias for ' + name))
            load = target / 'etc/modules-load.d/bluetooth.conf'
            init = target / 'etc/init.d/S11modules'
            if (not load.is_file() or name not in load.read_text().splitlines()
                    or not init.is_file() or not init.stat().st_mode & 0o111):
                missing.append(dict(group='bluetooth-firmware-order', required='S11modules loads ' + name + ' after root mount'))
    return missing


def check_bluez(config, log):
    """Check the selected plugins and the daemon's actual configure invocation."""
    failures = []
    for plugin in ('hid', 'hog'):
        name = 'BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_' + plugin.upper()
        if config is None or config.get(name) != 'y':
            failures.append(dict(group='bluetooth-plugins', symbol=name,
                                 resolved='unavailable' if config is None else config.get(name, 'absent'), required='y'))
    commands = []
    for line in (log or '').splitlines():
        if re.match(r'^\s*\$\s+(?:\S*/)?configure(?:\s|$)', line):
            try:
                commands.append(shlex.split(line.lstrip()[1:].strip()))
            except ValueError:
                commands.append([])
    if len(commands) != 1 or not commands[0]:
        failures.append(dict(group='bluetooth-plugins', required='one actual BlueZ configure invocation in config.log'))
        return failures
    arguments = commands[0][1:]
    for plugin in ('hid', 'hog'):
        disabled = any(argument == '--disable-' + plugin or argument.startswith('--disable-' + plugin + '=') or
                       argument == '--enable-' + plugin + '=no' for argument in arguments)
        if '--enable-' + plugin not in arguments or disabled:
            failures.append(dict(group='bluetooth-plugins', plugin=plugin,
                                 required='--enable-' + plugin + ' without a disabling alternative'))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Resolved Linux .config after olddefconfig')
    parser.add_argument('--target', type=Path, help='Built image root tree; also check Qualcomm USB Bluetooth firmware')
    parser.add_argument('--buildroot-config', type=Path, help='Resolved Buildroot .config; requires --bluez-config-log')
    parser.add_argument('--bluez-config-log', type=Path, help='Compiled BlueZ config.log; requires --buildroot-config')
    args = parser.parse_args(argv)
    failures = check(read_config(args.config), args.target)
    if args.buildroot_config is not None or args.bluez_config_log is not None:
        failures += check_bluez(read_config(args.buildroot_config) if args.buildroot_config is not None else None,
                                args.bluez_config_log.read_text() if args.bluez_config_log is not None else None)
    print(json.dumps(dict(event='surface-hardware-config', status='FAIL' if failures else 'PASS',
                          scope='static-configuration-and-files', physical_validation=False, failures=failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
