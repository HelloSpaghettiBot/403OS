#!/usr/bin/env python3
"""Replay the PG-9023 HID descriptor through a disposable VM's real HID parser.

This is a descriptor/report fixture, not a simulated Bluetooth radio. It never
opens a real controller or uses its address, pairing state or authentication
keys. --inspect-only is safe on the host and creates no device. Otherwise both
the image identity and disposable VM DMI identity are mandatory.

Commands are newline-delimited JSON on stdin. Examples:
 {"button":"south","pressed":true}
 {"button":"south","pressed":false}
 {"hat":"right"}
 {"hat":"center"}
 {"axis":"leftx","value":255}
 {"neutral":true}
 {"inventory":true}
 {"stop":true}
"""
import argparse
import errno
import json
import os
from pathlib import Path
import select
import signal
import struct
import sys
import time

VM_NAME = 'Surface Go OS Disposable Test VM'
NAME = 'ipega Extending Game Controller'
# Locally administered synthetic Bluetooth identity, never the owner's address.
UNIQUE = '02:40:30:00:00:23'
DESCRIPTOR = bytes.fromhex(
    '05010906A101850175019508050719E029E715002501810295017508810395057501'
    '050819012905910295017503910395067508150026FF000507190029FF8100C0'
    '050C0901A1018502150025017501951509CD09B509B609E909EA0A94010A9601'
    '0A83010A23020A8A010AB1010A21020A24020A25020A9E010A2702094009B0'
    '09E2093009B88102950175038103C005010902A10185060901A10005091901'
    '2903150025017501950381027505950181010501093009311581257F75089502'
    '810609381581257F750895018106C0C005010905A10185070901A10009300931'
    '09320935150026FF00750895048102C00939150025073500463B01651475049501'
    '814275049501810105091901290F150025017501951081020502150026FF0009C4'
    '09C5950275088102750895018101C0')
UHID_DESTROY, UHID_START, UHID_STOP = 1, 2, 3
UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 4, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY = 9, 10
UHID_CREATE2, UHID_INPUT2 = 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
BUTTONS = {'south': 0, 'east': 1, 'c': 2, 'north': 3, 'west': 4,
           'z': 5, 'leftshoulder': 6, 'rightshoulder': 7,
           'lefttrigger': 8, 'righttrigger': 9, 'back': 10,
           'start': 11, 'guide': 12, 'leftstick': 13, 'rightstick': 14}
HATS = {'center': 8, 'up': 0, 'upright': 1, 'right': 2, 'downright': 3,
        'down': 4, 'downleft': 5, 'left': 6, 'upleft': 7}
AXES = {'leftx': 1, 'lefty': 2, 'rightx': 3, 'righty': 4,
        'accelerator': 8, 'brake': 9}


def packet(kind, payload=b''):
    return struct.pack('=I', kind) + payload


def create_packet():
    payload = struct.pack('=128s64s64sHHIIII', NAME.encode(),
                          b'403os/vm-ipega-hid-replay', UNIQUE.encode(),
                          len(DESCRIPTOR), 5, 0x1949, 0x0402, 0x011b, 0)
    return packet(UHID_CREATE2, payload + DESCRIPTOR)


def neutral_report():
    # Report 7: four 8-bit sticks, hat + padding, sixteen buttons,
    # accelerator/brake, padding. The hat's null value is 8, not 0 (up).
    return bytearray((7, 128, 128, 128, 128, 8, 0, 0, 0, 0, 0))


def command_report(report, command):
    """Validate a complete command before changing any fixture state."""
    if not isinstance(command, dict):
        raise ValueError('Expected a JSON object')
    updated = bytearray(report)
    if set(command) == {'neutral'} and command['neutral'] is True:
        return neutral_report()
    if set(command) == {'button', 'pressed'}:
        if command['button'] not in BUTTONS or type(command['pressed']) is not bool:
            raise ValueError('Unknown button or non-boolean pressed value')
        number = BUTTONS[command['button']]
        index, bit = 6 + number // 8, 1 << (number % 8)
        updated[index] = (updated[index] | bit) if command['pressed'] else (updated[index] & ~bit)
    elif set(command) == {'hat'} and command['hat'] in HATS:
        updated[5] = HATS[command['hat']]
    elif set(command) == {'axis', 'value'}:
        if command['axis'] not in AXES or type(command['value']) is not int or not 0 <= command['value'] <= 255:
            raise ValueError('Unknown axis or value outside 0..255')
        updated[AXES[command['axis']]] = command['value']
    else:
        raise ValueError('Unknown command or unexpected fields')
    return updated


def reply_to_request(event, report):
    """Return the packed UHID control response, or None for notifications."""
    if len(event) < 4:
        return None
    kind = struct.unpack_from('=I', event)[0]
    if kind == UHID_GET_REPORT and len(event) >= 10:
        ident, number, report_type = struct.unpack_from('=IBB', event, 4)
        responses = {1: bytes(9), 2: bytes(4), 6: bytes(5), 7: bytes(report)}
        value = responses.get(number) if report_type == 2 else None
        if value is None:
            return packet(UHID_GET_REPORT_REPLY, struct.pack('=IHH', ident, errno.EIO, 0))
        value = bytes((number,)) + value[1:]
        return packet(UHID_GET_REPORT_REPLY, struct.pack('=IHH', ident, 0, len(value)) + value)
    if kind == UHID_SET_REPORT and len(event) >= 12:
        ident = struct.unpack_from('=I', event, 4)[0]
        return packet(UHID_SET_REPORT_REPLY, struct.pack('=IH', ident, 0))
    return None


def inventory(root=Path('/sys/class/input')):
    result = []
    for node in sorted(root.glob('event*')):
        try:
            device = node / 'device'
            if (device / 'uniq').read_text().strip() != UNIQUE:
                continue
            row = {'node': '/dev/input/' + node.name,
                   'name': (device / 'name').read_text().strip()}
            for name in ('bustype', 'vendor', 'product', 'version'):
                row[name] = (device / 'id' / name).read_text().strip()
            for name in ('key', 'abs', 'rel', 'ev'):
                row[name] = (device / 'capabilities' / name).read_text().strip()
            result.append(row)
        except OSError:
            continue  # Hotplug can remove a node during inventory.
    return result


def emit(kind, **value):
    print(json.dumps({'event': kind, **value}, sort_keys=True), flush=True)


def guard():
    if (Path('/sys/class/dmi/id/product_name').read_text().strip() != VM_NAME or
            not Path('/etc/surface-image-id').is_file()):
        raise RuntimeError('HID replay is restricted to the disposable 403 OS VM; use --inspect-only on the host')
    if os.geteuid() != 0:
        raise RuntimeError('The disposable VM fixture needs root to create UHID')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-only', action='store_true')
    parser.add_argument('--seconds', type=int, default=180)
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.seconds <= 600:
        parser.error('--seconds must be between 1 and 600')
    if arguments.inspect_only:
        emit('descriptor', name=NAME, bytes=len(DESCRIPTOR), descriptor=DESCRIPTOR.hex(),
             neutral_report=neutral_report().hex(), vendor='1949', product='0402', version='011b')
        return 0
    guard()
    descriptor = os.open('/dev/uhid', os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    report, pending, running, stdin_open = neutral_report(), b'', True, True
    def stop(_signum, _frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def send(value):
        written = os.write(descriptor, value)
        if written != len(value):
            raise RuntimeError('Incomplete UHID event write')
    def input_report():
        send(packet(UHID_INPUT2, struct.pack('=H', len(report)) + report))
    try:
        send(create_packet())
        emit('created', descriptor_bytes=len(DESCRIPTOR), unique=UNIQUE)
        start = time.monotonic()
        deadline, next_inventory, seen = start + arguments.seconds, start, None
        while running and time.monotonic() < deadline:
            readers = [descriptor] + ([sys.stdin.fileno()] if stdin_open else [])
            available, _, _ = select.select(readers, [], [], 0.1)
            if descriptor in available:
                event = os.read(descriptor, 8192)
                if event:
                    kind = struct.unpack_from('=I', event)[0]
                    reply = reply_to_request(event, report)
                    if reply is not None:
                        send(reply)
                    if kind in (UHID_START, UHID_OPEN, UHID_CLOSE, UHID_STOP):
                        emit('kernel', type=kind)
                    if kind in (UHID_START, UHID_OPEN):
                        input_report()
            if stdin_open and sys.stdin.fileno() in available:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    stdin_open = False
                pending += data
                if len(pending) > 65536:
                    raise ValueError('Command input exceeded its bound')
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if not line.strip():
                        continue
                    command = json.loads(line)
                    if command == {'stop': True}:
                        running = False
                        break
                    if command == {'inventory': True}:
                        emit('inventory', nodes=inventory())
                    else:
                        report = command_report(report, command)
                        input_report()
                        emit('input', report=report.hex())
            if time.monotonic() >= next_inventory:
                nodes = inventory()
                if nodes != seen:
                    emit('inventory', nodes=nodes)
                    seen = nodes
                next_inventory = time.monotonic() + 0.5
        return 0
    finally:
        try:
            report = neutral_report()
            input_report()
            send(packet(UHID_DESTROY))
        finally:
            os.close(descriptor)
        emit('destroyed')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print('iPega HID replay: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
