#!/usr/bin/env python3
"""Mount DATA from the disk whose second partition is the running root.

Partition UUIDs may be duplicated by an older installed image or a cloned USB.
The kernel's mounted root major/minor, rather than a global UUID symlink, binds
this boot to its own third partition. Fixture paths are function arguments only;
the privileged CLI always uses the running image and actual kernel paths.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import stat
import struct
import subprocess
import sys

MARKER = Path('/etc/surface-image-id')
MOUNTINFO = Path('/proc/self/mountinfo')
SYS_DEV_BLOCK = Path('/sys/dev/block')
SYS_DEVICES = Path('/sys/devices')
DEV_ROOT = Path('/dev')
DATA = Path('/data')
GROW_HELPER = Path('/usr/sbin/surface-grow-data')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}


class Refused(ValueError):
    pass


def require_guest():
    info = MARKER.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not MARKER.read_text().strip().startswith('surface-go-') or os.geteuid() != 0):
        raise Refused('The privileged booted 403 OS image is required')


def mounts(content):
    result = []
    for line in content.splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields:
            raise Refused('Malformed kernel mount inventory')
        separator = fields.index('-')
        if separator < 6 or separator + 3 >= len(fields) or not re.fullmatch(r'\d+:\d+', fields[2]):
            raise Refused('Malformed kernel mount inventory')
        decode = lambda value: re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
        result.append(dict(dev=fields[2], root=decode(fields[3]), path=decode(fields[4]),
                           filesystem=fields[separator + 1], options=set(fields[5].split(','))))
    return result


def node_identity(node):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', node.name):
        raise Refused('Invalid kernel block device name')
    dev = (node / 'dev').read_text().strip()
    if not re.fullmatch(r'\d+:\d+', dev):
        raise Refused('Invalid kernel block device number')
    return dict(name=node.name, dev=dev)


def kernel_geometry(node, partition=False):
    fields = ('size', 'start') if partition else ('size',)
    values = {key: (node / key).read_text().strip() for key in fields}
    if any(not value.isdecimal() for value in values.values()) or int(values['size']) <= 0:
        raise Refused('Invalid kernel block device geometry')
    result = dict(bytes=int(values['size']) * 512)
    if partition:
        result['first'] = int(values['start'])
    return result


def discover(current_mounts, sys_dev_block=SYS_DEV_BLOCK, sys_devices=SYS_DEVICES, dev_root=DEV_ROOT):
    roots = [item for item in current_mounts if item['path'] == '/']
    if len(roots) != 1 or roots[0]['root'] != '/' or roots[0]['filesystem'] != 'ext4':
        raise Refused('A single ext4 root partition is required')
    root = (sys_dev_block / roots[0]['dev']).resolve(strict=True)
    if not root.is_relative_to(sys_devices.resolve(strict=True)) or (root / 'partition').read_text().strip() != '2':
        raise Refused('The mounted root is not image partition 2')
    disk = root.parent
    if (disk / 'partition').exists():
        raise Refused('The root parent is not a whole disk')
    candidates = [node for node in disk.iterdir()
                  if (node / 'partition').is_file() and (node / 'partition').read_text().strip() == '3']
    if len(candidates) != 1:
        raise Refused('Exactly one DATA partition 3 must share the root disk')
    data = candidates[0].resolve(strict=True)
    if data.parent != disk or data == root:
        raise Refused('DATA does not belong to the mounted root disk')
    result = {kind: node_identity(node) for kind, node in (('disk', disk), ('root', root), ('data', data))}
    for kind, node in (('disk', disk), ('root', root), ('data', data)):
        result[kind].update(kernel_geometry(node, partition=kind != 'disk'))
    if result['root']['dev'] != roots[0]['dev'] or len({value['dev'] for value in result.values()}) != 3:
        raise Refused('Mounted root and kernel disk identities disagree')
    for record in result.values():
        record['path'] = str(dev_root / record['name'])
    return result


def gpt_reader():
    # The installed growth helper provides the same strict read-only GPT
    # validator used by installation. Importing it never enters its CLI.
    current = Path('/')
    for component in GROW_HELPER.parts[1:]:
        current /= component
        if stat.S_ISLNK(current.lstat().st_mode):
            raise Refused('The installed GPT helper must not contain symlinks')
    info = GROW_HELPER.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Refused('The installed GPT helper must be trusted and root-owned')
    helper = runpy.run_path(str(GROW_HELPER), run_name='surface_boot_gpt_reader')
    reader = helper.get('read_media_gpt')
    if not callable(reader):
        raise Refused('The installed GPT helper is incompatible')
    return reader


def read_partition_guids(fd, selected, reader):
    """Read held-disk GPT identities, matching the selected kernel ranges.

    Linux 6.12 does not include PARTUUID in partition uevents. Reading both
    validated GPT copies also avoids depending on global UUID symlinks.
    """
    layout = reader(fd, selected['disk']['bytes'])
    for kind, number in (('root', 2), ('data', 3)):
        part = layout['parts'][number - 1]
        if (part['number'] != number or part['first'] != selected[kind]['first']
                or part['bytes'] != selected[kind]['bytes']):
            raise Refused('GPT partition geometry differs from the mounted root disk kernel inventory')
        selected[kind]['partuuid'] = part['uuid']
    return selected


def opened_block(record):
    fd = os.open(record['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != os.makedev(*map(int, record['dev'].split(':'))):
            raise Refused('Opened block device differs from the kernel inventory')
        return fd
    except Exception:
        os.close(fd)
        raise


def confirm_data(current_mounts, selected):
    found = [item for item in current_mounts if item['path'] == '/data']
    if not found:
        return False
    if (len(found) != 1 or found[0]['dev'] != selected['data']['dev'] or found[0]['root'] != '/'
            or found[0]['filesystem'] != 'ext4' or 'rw' not in found[0]['options']):
        raise Refused('Mounted DATA differs from the running root disk')
    return True


def run(command, pass_fds=()):
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=30,
                          pass_fds=pass_fds, env=ENV)


def probe_data(fd):
    result = run(['/sbin/blkid', '-p', '-o', 'export', f'/proc/self/fd/{fd}'], pass_fds=(fd,))
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or fields.get('TYPE') != 'ext4' or fields.get('LABEL') != 'SURFACE_DATA':
        raise Refused('The root disk third partition is not the expected ext4 DATA filesystem')


def mount_data():
    require_guest()
    selected = discover(mounts(MOUNTINFO.read_text()))
    disk_fd = opened_block(selected['disk'])
    root_fd = data_fd = directory_fd = None
    try:
        capacity = struct.unpack('Q', fcntl.ioctl(disk_fd, 0x80081272, bytes(8)))[0]
        if capacity != selected['disk']['bytes']:
            raise Refused('Opened root disk capacity differs from the kernel inventory')
        read_partition_guids(disk_fd, selected, gpt_reader())
        root_fd = opened_block(selected['root'])
        data_fd = opened_block(selected['data'])
        probe_data(data_fd)
        if not confirm_data(mounts(MOUNTINFO.read_text()), selected):
            # Hold both endpoints so source or target symlinks cannot redirect
            # the mount between validation and the mount child process.
            directory_fd = os.open(DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(directory_fd)
            # Buildroot creates the compatibility user's empty home below
            # this mount point. Mounting DATA hides it without changing it.
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise Refused('DATA mount point must be owned by root without shared write access')
            if any(item['dev'] == selected['data']['dev'] for item in mounts(MOUNTINFO.read_text())):
                raise Refused('The selected DATA partition is already mounted elsewhere')
            result = run(['/bin/mount', '-n', '-i', '-c', '-t', 'ext4', '-o', 'rw',
                          f'/proc/self/fd/{data_fd}', f'/proc/self/fd/{directory_fd}'],
                         pass_fds=(data_fd, directory_fd))
            if result.returncode or not confirm_data(mounts(MOUNTINFO.read_text()), selected):
                raise Refused('Mounting and confirming the root disk DATA partition failed')
        return selected
    finally:
        for fd in (directory_fd, data_fd, root_fd, disk_fd):
            if fd is not None:
                os.close(fd)


def boot_marker(value):
    # Diagnostics must not prevent a successful physical boot when the
    # machine has no UART. VM serial capture remains available independently
    # of whichever console init inherited.
    try:
        print(value, flush=True)
    except OSError:
        pass
    serial_fd = None
    try:
        serial_fd = os.open('/dev/ttyS0', os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
        os.write(serial_fd, (value + '\n').encode('ascii'))
    except OSError:
        pass
    finally:
        if serial_fd is not None:
            os.close(serial_fd)


def main():
    if len(sys.argv) != 1:
        raise Refused('The boot DATA helper takes no arguments')
    selected = mount_data()
    boot_marker('SURFACE_BOOT_DISK={disk} ROOT={root} DATA={data} ROOT_DEVNUM={root_dev} DATA_DEVNUM={data_dev}'.format(
        disk=selected['disk']['path'], root=selected['root']['path'], data=selected['data']['path'],
        root_dev=selected['root']['dev'], data_dev=selected['data']['dev']))
    boot_marker('SURFACE_BOOT_IDENTITY ' + json.dumps(dict(
        disk=selected['disk']['path'], root_device=selected['root']['path'], data_device=selected['data']['path'],
        root_partuuid=selected['root']['partuuid'], data_partuuid=selected['data']['partuuid']), sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('SURFACE_DATA_MOUNT_FAIL: ' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
