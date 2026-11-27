#!/usr/bin/python3
"""Read-only evidence that a Bluetooth peer has usable Linux input nodes.

BlueZ's Connected/ServicesResolved properties can precede asynchronous HOG
report-map parsing and UHID creation. Match the actual event node to the peer's
address and inspect its evdev identity/capabilities without consuming input,
grabbing a device, sending reports, or modifying the Bluetooth adapter.
"""
import fcntl
import grp
import os
from pathlib import Path
import re
import stat
import struct
import time

ADDRESS = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z')
BUS_BLUETOOTH = 5
KEY_BYTES = 96
UDEV_RECORD_MAX = 65536


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


def require_application_access(path, info, udev_root=Path('/run/udev/data')):
    """Require the same classification and permissions used by SDL applications.

    Root can inspect an evdev node before udev has finished classifying it or
    granting the gamepad group access. That is not a usable controller yet.
    The caller retries this read-only gate during its existing bounded wait.
    """
    try:
        group = grp.getgrnam('gamepad').gr_gid
    except KeyError as error:
        raise ValueError('Controller application group is unavailable') from error
    if info.st_gid != group or not info.st_mode & stat.S_IRGRP:
        raise ValueError('Controller input is not readable by applications yet')
    record = Path(udev_root) / f'c{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    descriptor = os.open(record, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_mode & 0o022 or not 0 < metadata.st_size <= UDEV_RECORD_MAX):
            raise ValueError('Controller classification record is unsafe or incomplete')
        data = os.read(descriptor, UDEV_RECORD_MAX + 1)
        after = os.fstat(descriptor)
        if (len(data) != metadata.st_size or len(data) > UDEV_RECORD_MAX or
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)):
            raise ValueError('Controller classification is still changing')
        properties = {}
        for line in data.decode('utf-8').splitlines():
            if not line.startswith('E:') or '=' not in line:
                continue
            key, value = line[2:].split('=', 1)
            if key in properties:
                raise ValueError('Controller classification record contains duplicate fields')
            properties[key] = value
        if properties.get('ID_INPUT_JOYSTICK') != '1':
            raise ValueError('Controller input is not classified for SDL applications yet')
        # The record filename identifies this character device. Recheck the
        # node after reading it so hotplug cannot validate a replacement path.
        current = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISCHR(current.st_mode) or
                (current.st_dev, current.st_ino, current.st_rdev, current.st_uid, current.st_gid) !=
                (info.st_dev, info.st_ino, info.st_rdev, info.st_uid, info.st_gid) or
                not current.st_mode & stat.S_IRGRP):
            raise ValueError('Controller input changed while checking application access')
    finally:
        os.close(descriptor)


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
        gamepad = bool(buttons or dpad or generic)
        if gamepad:
            require_application_access(path, info)
        return dict(node=str(path), gamepad=gamepad,
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
