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
CMDLINE = Path('/proc/cmdline')
ISO_RUNTIME = Path('/run/surface-iso')
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
                           filesystem=fields[separator + 1], options=set(fields[5].split(',')),
                           source=decode(fields[separator + 2]),
                           super_options=set(decode(fields[separator + 3]).split(','))))
    return result


def iso_layout(content):
    """Parse packer-generated values without evaluating shell syntax."""
    values = {}
    for line in content.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key in values or key not in {'ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES', 'IMAGE_SHA256'}:
            raise Refused('Invalid optical boot layout')
        values[key] = value
    if set(values) != {'ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES', 'IMAGE_SHA256'}:
        raise Refused('Incomplete optical boot layout')
    if not re.fullmatch('[0-9a-f]{64}', values['IMAGE_SHA256']):
        raise Refused('Invalid optical image checksum')
    for key in ('ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES'):
        if not re.fullmatch('[0-9]{1,12}', values[key]):
            raise Refused('Invalid optical image geometry')
        values[key] = int(values[key])
    if (values['ROOT_OFFSET'] < 1024**2 or values['ROOT_BYTES'] <= 0
            or any(values[key] % 512 for key in ('ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES'))
            or values['ROOT_OFFSET'] + values['ROOT_BYTES'] >= values['IMAGE_BYTES']):
        raise Refused('Invalid optical root partition range')
    return values


def discover_iso(current_mounts, layout, sys_dev_block=SYS_DEV_BLOCK,
                 sys_devices=SYS_DEVICES, dev_root=DEV_ROOT):
    """Require the complete initramfs topology, never just a boot marker.

    The optical source and both loop-backed lower filesystems must be read only.
    DATA may only be the separate temporary file on the bounded /run tmpfs.
    Alternate sysfs roots exist solely for unprivileged topology fixtures.
    """
    def mounted(path, filesystem, writable, root='/'):
        found = [item for item in current_mounts if item['path'] == path]
        if (len(found) != 1 or found[0]['filesystem'] != filesystem or found[0]['root'] != root
                or ('rw' if writable else 'ro') not in found[0]['options']):
            raise Refused('Invalid optical live mount: ' + path)
        return found[0]

    root = mounted('/', 'overlay', True)
    runtime = mounted('/run', 'tmpfs', True)
    media = mounted('/run/surface-iso/media', 'iso9660', False)
    payload = mounted('/run/surface-iso/payload', 'squashfs', False)
    lower = mounted('/run/surface-iso/lower', 'ext4', False)
    data = mounted('/data', 'ext4', True)
    installer = mounted('/data/SurfaceGoGamingOS', 'squashfs', False, '/SurfaceGoGamingOS')
    expected_overlay = {'lowerdir=/run/surface-iso/lower', 'upperdir=/run/surface-iso/upper',
                        'workdir=/run/surface-iso/work'}
    if not expected_overlay.issubset(root.get('super_options', set())):
        raise Refused('The optical writable root has unexpected overlay backing')
    if not {'nosuid', 'nodev'}.issubset(runtime['options']):
        raise Refused('The optical runtime must use protected temporary storage')
    # Linux reports this tmpfs bound in KiB rather than the mount's MiB spelling.
    if 'size=1572864k' not in runtime.get('super_options', set()):
        raise Refused('The optical runtime memory bound is missing')
    if installer['dev'] != payload['dev']:
        raise Refused('The optical installer binding is not the verified payload')
    if len({runtime['dev'], media['dev'], payload['dev'], lower['dev'], data['dev']}) != 5:
        raise Refused('Optical live storage devices overlap')

    def node(record):
        selected = (sys_dev_block / record['dev']).resolve(strict=True)
        if not selected.is_relative_to(sys_devices.resolve(strict=True)):
            raise Refused('Optical device is outside kernel device topology')
        identity = node_identity(selected)
        if identity['dev'] != record['dev']:
            raise Refused('Optical mount and kernel device identities disagree')
        return selected

    def loop(selected, backing, readonly, image_bytes=None):
        if not re.fullmatch(r'loop[0-9]+', selected.name) or (selected / 'partition').exists():
            raise Refused('The optical backing device is not a whole loop device')
        expected = {'backing_file': backing, 'offset': '0', 'sizelimit': '0'}
        if any((selected / 'loop' / key).read_text().strip() != value for key, value in expected.items()):
            raise Refused('Unexpected optical loop backing file or range')
        if (selected / 'ro').read_text().strip() != ('1' if readonly else '0'):
            raise Refused('Unexpected optical loop write access')
        if image_bytes is not None and kernel_geometry(selected)['bytes'] != image_bytes:
            raise Refused('Optical loop capacity differs from its image')

    media_node, payload_node, root_node, data_node = map(node, (media, payload, lower, data))
    # A hybrid ISO may be on USB; the mounted ISO9660 filesystem remains ro.
    if media_node.name.startswith(('loop', 'ram', 'zram', 'dm-', 'md')):
        raise Refused('Optical media must come directly from a physical block device')
    loop(payload_node, '/run/surface-iso/media/403os/payload.squashfs', True)
    if (root_node / 'partition').read_text().strip() != '2':
        raise Refused('The optical root is not pristine image partition 2')
    loop(root_node.parent, '/run/surface-iso/payload/SurfaceGoGamingOS/surface-go.img',
         True, layout['IMAGE_BYTES'])
    geometry = kernel_geometry(root_node, partition=True)
    if geometry != {'bytes': layout['ROOT_BYTES'], 'first': layout['ROOT_OFFSET'] // 512}:
        raise Refused('Optical root partition differs from its boot layout')
    loop(data_node, '/run/surface-iso/live-data.ext4', False, 4 * 1024**3)
    result = {'iso': True}
    for kind, selected in (('disk', media_node), ('root', root_node), ('data', data_node)):
        result[kind] = dict(node_identity(selected), path=str(dev_root / selected.name), partuuid='')
    return result


def mount_iso_data():
    """Validate already mounted optical live storage; never mount a disk here."""
    if CMDLINE.read_text().split().count('surface.boot=iso') != 1:
        raise Refused('Explicit optical live boot is required')
    runtime = ISO_RUNTIME.lstat()
    if not stat.S_ISDIR(runtime.st_mode) or runtime.st_uid != 0 or runtime.st_mode & 0o077:
        raise Refused('The optical runtime directory is not private and root-owned')
    fd = os.open(ISO_RUNTIME / 'layout', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222 or info.st_size > 4096:
            raise Refused('The optical layout is not a protected regular file')
        layout = iso_layout(os.read(fd, 4097).decode('ascii'))
    finally:
        os.close(fd)
    selected = discover_iso(mounts(MOUNTINFO.read_text()), layout)
    for kind in ('disk', 'root', 'data'):
        fd = opened_block(selected[kind])
        try:
            if kind == 'data':
                probe_data(fd)
        finally:
            os.close(fd)
    # Confirm DATA really sits on /run tmpfs, with a private regular backing
    # file. A same-name directory or bind mount is not sufficient evidence.
    backing = (ISO_RUNTIME / 'live-data.ext4').lstat()
    run_info = Path('/run').stat()
    if (not stat.S_ISREG(backing.st_mode) or backing.st_uid != 0 or backing.st_mode & 0o077
            or backing.st_size != 4 * 1024**3 or backing.st_dev != run_info.st_dev):
        raise Refused('Invalid temporary optical DATA backing file')
    marker = json.dumps(dict(schema='surface-iso-boot-v1', verified_topology=True, ephemeral_data=True)) + '\n'
    marker_path = ISO_RUNTIME / 'boot.json'
    try:
        fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
    except FileExistsError:
        fd = os.open(marker_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222
                    or info.st_size != len(marker) or os.read(fd, 4096) != marker.encode('ascii')):
                raise Refused('The existing optical boot marker is invalid')
        finally:
            os.close(fd)
        return selected
    try:
        if os.write(fd, marker.encode('ascii')) != len(marker):
            raise Refused('Could not publish the verified optical boot marker')
    finally:
        os.close(fd)
    return selected


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
    if 'surface.boot=iso' in CMDLINE.read_text().split():
        return mount_iso_data()
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
    if selected.get('iso'):
        boot_marker('SURFACE_ISO_BOOT_PASS ephemeral_data=1 optical_payload_verified=1')
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
