#!/usr/bin/python3
"""Read-only evidence that a Bluetooth peer has usable Linux input nodes.

BlueZ's Connected/ServicesResolved properties can precede asynchronous HOG
report-map parsing and UHID creation. Match the actual event node to the peer's
address and inspect its evdev identity/capabilities without consuming input,
grabbing a device, sending reports, or modifying the Bluetooth adapter.
"""
import fcntl
import os
from pathlib import Path
import re
import stat
import struct
import time

ADDRESS = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z')
BUS_BLUETOOTH = 5
KEY_BYTES = 96


class InputNotReady(ValueError):
    pass


def address_value(value):
    value = value.strip()
    if not ADDRESS.fullmatch(value):
        raise ValueError('Invalid Bluetooth input address')
    return value.upper()


def read_ioctl(fd, number, size):
    result = bytearray(size)
    fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord('E') << 8) | number,
                result, True)
    return bytes(result)


def inspect_event(path, address):
    """Inspect a real evdev node; never read events or retain the descriptor."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0:
            raise ValueError('Bluetooth input is not a root-owned character device')
        # Recheck identity on the opened descriptor, preventing a hotplug race
        # from accepting a replacement eventN belonging to a different device.
        bus, vendor, product, version = struct.unpack('@HHHH', read_ioctl(fd, 0x02, 8))
        unique = read_ioctl(fd, 0x08, 64).split(b'\0', 1)[0].decode('ascii')
        if bus != BUS_BLUETOOTH or address_value(unique) != address:
            raise ValueError('The input node belongs to a different device')
        keys = int.from_bytes(read_ioctl(fd, 0x21, KEY_BYTES), 'little')
        axes = int.from_bytes(read_ioctl(fd, 0x23, 8), 'little')
        if not keys:
            # Motion sensors/touch interfaces from the same controller must
            # not stand in for the button-bearing input device.
            raise ValueError('The input node has no buttons or keys')
        buttons = any(keys & (1 << bit) for bit in range(0x120, 0x140))
        dpad = any(keys & (1 << bit) for bit in range(0x220, 0x224))
        generic = axes and any(keys & (1 << bit) for bit in range(0x100, 0x110))
        return dict(node=str(path), gamepad=bool(buttons or dpad or generic),
                    bus=bus, vendor=vendor, product=product, version=version)
    finally:
        os.close(fd)


def find_input(address, sysfs_root=Path('/sys/class/input'), dev_root=Path('/dev/input'),
               inspect=None, require_gamepad=False):
    """Return verified nodes for exactly this peer; tolerate hotplug races."""
    address = address_value(address)
    inspect = inspect or inspect_event
    result = []
    for event in sorted(Path(sysfs_root).glob('event*')):
        if not re.fullmatch(r'event[0-9]+', event.name):
            continue
        try:
            unique = (event / 'device/uniq').read_text(encoding='ascii')
            if address_value(unique) != address:
                continue
            evidence = inspect(Path(dev_root) / event.name, address)
            if not require_gamepad or evidence['gamepad']:
                result.append(evidence)
        except (OSError, ValueError, UnicodeError):
            continue
    return result


def wait_for_input(address, timeout=12, sysfs_root=Path('/sys/class/input'),
                   dev_root=Path('/dev/input'), inspect=None, require_gamepad=False,
                   clock=time.monotonic, sleep=time.sleep):
    """Allow asynchronous HID initialization, bounded by a monotonic deadline."""
    address = address_value(address)
    if not 0 <= timeout <= 30:
        raise ValueError('Invalid Bluetooth input timeout')
    deadline = clock() + timeout
    while True:
        nodes = find_input(address, sysfs_root, dev_root, inspect, require_gamepad)
        if nodes:
            return nodes
        remaining = deadline - clock()
        if remaining <= 0:
            raise InputNotReady('Bluetooth connected, but the device did not create a working input device. '
                                'Check its controller mode and reconnect')
        sleep(min(0.2, remaining))
