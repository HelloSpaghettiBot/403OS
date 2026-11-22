#!/usr/bin/python3
"""Bounded Bluetooth preparation and diagnostics inside the booted image.

Power-on is an explicit UI action. It clears only Bluetooth software radio
blocks, leaves hardware blocks and other radios alone, and never resets a USB
device or removes an existing bond.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

MARKER = Path('/etc/surface-image-id')
MOUNTS = Path('/proc/mounts')
RFKILL = Path('/sys/class/rfkill')
BLUETOOTH = Path('/sys/class/bluetooth')
USB = Path('/sys/bus/usb/devices')
LOGS = Path('/data/logs')
FIRMWARE = Path('/lib/firmware/qca')
MESSAGES = Path('/var/log/messages')
INIT = '/etc/init.d/S40bluetoothd'
CLIENT = '/usr/bin/bluetoothctl'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
CONTROLLER = re.compile(r'^Controller [0-9A-Fa-f:]{17}(?:\s|$)', re.M)
ADDRESS = re.compile(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b')


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


def check_client_output():
    """A real shell command catches silent noninteractive client regressions.

    bluetoothctl --version uses a separate path and cannot detect this fault.
    No adapter needs to be powered or present for the shell version command.
    """
    code, output = run([CLIENT, 'version'], 3)
    if code or not re.search(r'^Version \d+\.\d+', output, re.M):
        raise BluetoothError('Bluetooth command client returned no usable response; update 403 OS to repair the Bluetooth client')
    return output


def wait_adapter(seconds=8):
    """A successful list command can still precede firmware/HCI registration."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            code, output = run([CLIENT, 'list'], 1)
        except BluetoothError:
            code, output = 1, ''
        if code == 0 and CONTROLLER.search(output):
            return output
        if time.monotonic() >= deadline:
            if any(BLUETOOTH.glob('hci[0-9]*')):
                raise BluetoothError('Bluetooth hardware was found, but adapter initialization did not finish; see Bluetooth diagnostics in /data/logs')
            raise BluetoothError('No Bluetooth adapter is exposed by the kernel; see Bluetooth diagnostics in /data/logs')
        time.sleep(0.2)


def read_optional(path):
    try:
        return path.read_text()[:512].strip()
    except OSError:
        return None


def diagnostics(reason):
    """Bounded radio-only evidence; no USB serials, peers, bonds or credentials."""
    result = dict(schema='surface-bluetooth-diagnostics-v1',
                  time=datetime.now(timezone.utc).isoformat(), kernel=os.uname().release,
                  reason=reason, controllers=[], usb=[], firmware={})
    for path in sorted(BLUETOOTH.glob('hci[0-9]*'))[:16]:
        result['controllers'].append(dict(name=path.name, device=str((path / 'device').resolve())))
    for path in sorted(USB.glob('*'))[:256]:
        driver = (path / 'driver').resolve().name if (path / 'driver').is_symlink() else None
        vendor = read_optional(path / 'idVendor')
        if (driver != 'btusb' and vendor != '0cf3' and
                read_optional(path / 'bDeviceClass') != 'e0' and
                read_optional(path / 'bInterfaceClass') != 'e0'):
            continue
        parent = USB / path.name.split(':')[0]
        result['usb'].append(dict(port=path.name, vendor=vendor or read_optional(parent / 'idVendor'),
            product=read_optional(path / 'idProduct') or read_optional(parent / 'idProduct'), driver=driver,
            authorized=read_optional(path / 'authorized'),
            power_control=read_optional(path / 'power/control'),
            runtime_status=read_optional(path / 'power/runtime_status')))
    for name in ('rampatch_usb_00000302.bin', 'nvm_usb_00000302.bin'):
        path = FIRMWARE / name
        try:
            result['firmware'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            result['firmware'][name] = 'MISSING'
    try:
        result['radio_blocks'] = [dict(name=p.name, soft=s, hard=h) for p, s, h in radio_blocks()]
    except (OSError, ValueError) as error:
        result['radio_error'] = str(error)
    for command in ('list', 'show'):
        try:
            code, output = run([CLIENT, command], 2)
            result[command] = dict(code=code, output=output[-8192:])
        except (OSError, ValueError) as error:
            result[command] = dict(error=str(error))
    try:
        code, output = run([CLIENT, 'version'], 2)
        result['client_version'] = dict(code=code, output=output[-512:])
    except (OSError, ValueError) as error:
        result['client_version'] = dict(error=str(error))
    try:
        descriptor = os.open(MESSAGES, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise BluetoothError('Bluetooth daemon log is not an ordinary file')
            stream.seek(max(0, info.st_size - 131072))
            output = stream.read(131072).decode(errors='replace')
        result['daemon_messages'] = [line for line in output.splitlines()
                                    if 'bluetoothd' in line.lower()][-80:]
    except (OSError, ValueError) as error:
        result['daemon_messages'] = [str(error)]
    try:
        code, output = run(['/bin/dmesg', '-s', '131072'], 2)
        lines = [line for line in output.splitlines()
                 if re.search(r'bluetooth|btusb|btintel|btbcm|\bqca|firmware|\bhci[0-9]', line, re.I)]
        result['kernel_messages'] = lines[-120:] if code == 0 else ['Kernel log unavailable']
    except (OSError, ValueError) as error:
        result['kernel_messages'] = [str(error)]
    # Adapter addresses can occur in BlueZ output or kernel messages. This log
    # needs transport/firmware details, not stable personal radio identifiers.
    return ADDRESS.sub('[address]', json.dumps(result, indent=2)) + '\n'


def save_diagnostics(filename, reason):
    info = LOGS.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise BluetoothError('Bluetooth diagnostics directory is not trusted')
    content = diagnostics(reason)
    descriptor, name = tempfile.mkstemp(prefix='.bluetooth-', dir=LOGS)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, LOGS / filename)
    finally:
        temporary.unlink(missing_ok=True)


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
    if code == 0 and CONTROLLER.search(output):
        return output
    if code == 0 and not output:
        try:
            check_client_output()
        except BluetoothError as error:
            return str(error)
    if not any(BLUETOOTH.glob('hci[0-9]*')):
        return 'No Bluetooth adapter is exposed by the kernel. Cold boot after shutdown; check Bluetooth driver and firmware in the boot log'
    return 'Bluetooth hardware was found, but adapter initialization is incomplete. Choose Scan for controllers to retry'


def power_on():
    clear_software_blocks()
    ensure_daemon()
    check_client_output()
    wait_adapter()
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
    action.add_argument('--boot-check', action='store_true')
    action.add_argument('--record-error', metavar='REASON')
    args = parser.parse_args(argv)
    try:
        guard()
        if args.record_error is not None:
            reason = ADDRESS.sub('<address>', args.record_error)
            reason = ''.join(c for c in reason if ord(c) >= 32 and ord(c) != 127)[:256]
            if not reason.strip():
                raise BluetoothError('A diagnostic reason is required')
            save_diagnostics('bluetooth-last-error.json', reason)
        elif args.boot_check:
            try:
                wait_adapter()
                reason = 'Adapter registered with BlueZ'
            except (OSError, ValueError) as error:
                reason = str(error)
            save_diagnostics('bluetooth-boot.json', reason)
        else:
            print(status() if args.status else power_on(), flush=True)
        return 0
    except (OSError, ValueError) as error:
        if args.power_on:
            # Recheck the image guard so a failed host invocation cannot write
            # diagnostic files or probe host radios.
            try:
                guard()
                save_diagnostics('bluetooth-last-error.json', str(error))
            except (OSError, ValueError):
                pass
        print('Bluetooth: ' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
