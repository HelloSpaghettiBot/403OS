#!/usr/bin/python3
"""Bounded Bluetooth preparation and diagnostics inside the booted image.

Power-on is an explicit UI action. It clears only Bluetooth software radio
blocks, leaves hardware blocks and other radios alone, and never resets a USB
device or removes an existing bond.
"""
import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import time

MARKER = Path('/etc/surface-image-id')
MOUNTS = Path('/proc/mounts')
RFKILL = Path('/sys/class/rfkill')
BLUETOOTH = Path('/sys/class/bluetooth')
INIT = '/etc/init.d/S40bluetoothd'
CLIENT = '/usr/bin/bluetoothctl'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class BluetoothError(ValueError):
    pass


def guard():
    info = MARKER.lstat()
    if (os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not MARKER.read_text().strip().startswith('surface-go-')):
        raise BluetoothError('Bluetooth changes require the privileged booted 403 OS image')
    if not any(row.split()[1:3] == ['/data', 'ext4'] for row in MOUNTS.read_text().splitlines()):
        raise BluetoothError('The persistent DATA filesystem must be mounted before using Bluetooth')


def run(arguments, timeout=3):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False,
                                env=dict(os.environ, LC_ALL='C', TERM='dumb'))
    except subprocess.TimeoutExpired as error:
        raise BluetoothError('Bluetooth service did not answer before the operation timed out') from error
    output = ANSI.sub('', result.stdout)
    return result.returncode, ''.join(c for c in output if c in '\n\t' or ord(c) >= 32).strip()


def radio_blocks():
    blocks = []
    for path in sorted(RFKILL.glob('rfkill[0-9]*')):
        if (path / 'type').read_text().strip() != 'bluetooth':
            continue
        soft = (path / 'soft').read_text().strip()
        hard = (path / 'hard').read_text().strip()
        if soft not in ('0', '1') or hard not in ('0', '1'):
            raise BluetoothError('The kernel reported an invalid Bluetooth radio-block state')
        blocks.append((path, soft == '1', hard == '1'))
    return blocks


def clear_software_blocks():
    blocks = radio_blocks()
    if any(hard for _, _, hard in blocks):
        raise BluetoothError('Bluetooth is hardware blocked; enable the radio in firmware or turn off airplane mode')
    for path, soft, _ in blocks:
        if soft:
            # This is a single positively identified Bluetooth rfkill entry;
            # never write WLAN, WWAN, a global switch, or its hard-block state.
            descriptor = os.open(path / 'soft', os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                if os.write(descriptor, b'0\n') != 2:
                    raise BluetoothError('Could not clear the Bluetooth software radio block')
            finally:
                os.close(descriptor)
    if any(soft or hard for _, soft, hard in radio_blocks()):
        raise BluetoothError('Bluetooth remains radio blocked after the enable request')


def ensure_daemon():
    try:
        code, _ = run([CLIENT, 'list'], 2)
        if code == 0:
            return
    except BluetoothError:
        pass
    # The SysV start action may return "already running" before D-Bus is
    # ready. The actual bounded API probes below decide readiness.
    run([INIT, 'start'], 3)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        try:
            code, _ = run([CLIENT, 'list'], 1)
            if code == 0:
                return
        except BluetoothError:
            pass
        time.sleep(0.1)
    raise BluetoothError('Bluetooth daemon is unavailable; see the boot log for bluetoothd or D-Bus errors')


def status():
    blocks = radio_blocks()
    if any(hard for _, _, hard in blocks):
        return 'Bluetooth is hardware blocked; enable the radio in firmware or turn off airplane mode'
    if any(soft for _, soft, _ in blocks):
        return 'Bluetooth is software blocked. Choose Scan for controllers to enable Bluetooth'
    try:
        code, output = run([CLIENT, 'show'])
    except BluetoothError:
        return 'Bluetooth service is not responding. Choose Scan for controllers to start it'
    if code == 0 and re.search(r'^Controller [0-9A-Fa-f:]{17}(?:\s|$)', output, re.M):
        return output
    if not any(BLUETOOTH.glob('hci[0-9]*')):
        return 'No Bluetooth adapter is exposed by the kernel. Cold boot after shutdown; check Bluetooth driver and firmware in the boot log'
    return output or 'Bluetooth adapter initialization is incomplete. Choose Scan for controllers to retry'


def power_on():
    clear_software_blocks()
    ensure_daemon()
    code, output = run([CLIENT, 'power', 'on'], 5)
    if code or 'Failed' in output or 'succeeded' not in output:
        raise BluetoothError(output or 'Bluetooth radio power-on was not confirmed')
    code, information = run([CLIENT, 'show'], 3)
    if code or not re.search(r'^\s*Powered:\s+yes\s*$', information, re.M):
        raise BluetoothError('Bluetooth power-on returned without a powered adapter')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--status', action='store_true')
    action.add_argument('--power-on', action='store_true')
    args = parser.parse_args(argv)
    try:
        guard()
        print(status() if args.status else power_on(), flush=True)
        return 0
    except (OSError, ValueError) as error:
        print('Bluetooth: ' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
