#!/usr/bin/env python3
"""Guarded Surface OS installer. Listing/planning never opens a device writable.

There is deliberately no environment variable, regular-file execution mode,
alternate /sys or /dev CLI option, or host-guard override.
"""
import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import uuid
import zlib

MARKER = Path('/etc/surface-image-id')
SOURCE_MOUNTS = Path('/run/surface-installer')
SYS_BLOCK = Path('/sys/class/block')
DEV_ROOT = Path('/dev')
MINIMUM_TARGET = 60_000_000_000
ROOT_UUID = '8b4a530e-d6d4-4e69-9307-310000000001'
DATA_UUID = '8b4a530e-d6d4-4e69-9307-310000000002'
EFI_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
BLOCK_BYTES = 4 * 1024 * 1024
GROW_HELPER = Path('/usr/sbin/surface-grow-data')
SEED_HELPER = Path('/usr/sbin/surface-seed-data')


class Refused(ValueError):
    pass


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def require_guest():
    try:
        marker = MARKER.lstat()
        valid = stat.S_ISREG(marker.st_mode) and marker.st_uid == 0 and not marker.st_mode & 0o022
        valid = valid and MARKER.read_text().strip().startswith('surface-go-')
    except (OSError, UnicodeError):
        valid = False
    if not valid:
        raise Refused('Refusing host execution: this is not the booted Surface OS image')
    if os.geteuid() != 0:
        raise Refused('The image installer requires its privileged Surface OS service')


def text(path, fallback=''):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return fallback


def no_symlinks(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('An absolute path without parent traversal is required')
    current = Path('/')
    for part in path.parts[1:]:
        current = current / part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise Refused(f'Symlink paths are not permitted: {current}')
    return path


@functools.lru_cache(maxsize=1)
def growth_helper():
    """Load only the root-owned installed helper; there is no CLI path override."""
    path = no_symlinks(GROW_HELPER)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not os.access(path, os.X_OK):
        raise Refused('The installed DATA growth helper must be an executable root-owned regular file without shared write access')
    helper = runpy.run_path(str(path), run_name='surface_physical_installer_growth')
    for name in ('inspect_target', 'current_target', 'read_gpt', 'read_ext4', 'verify_preserved',
                 'open_readonly_target', 'data_device', 'read_data_filesystem'):
        if not callable(helper.get(name)):
            raise Refused('The installed DATA growth helper has an incompatible interface')
    return helper


def growth_call(helper, name, *arguments, **keywords):
    try:
        return helper[name](*arguments, **keywords)
    except ValueError as error:
        raise Refused('DATA growth preflight: ' + str(error)) from error


def source_data_layout(descriptor, size, helper=None):
    helper = helper or growth_helper()
    gpt = growth_call(helper, 'read_gpt', descriptor, size)
    data = gpt['parts'][2]
    filesystem = growth_call(helper, 'read_ext4', descriptor, data, pristine=True)
    if data['bytes'] != 4 * 1024 ** 3:
        raise Refused('The staged source must contain the original 4 GiB DATA partition')
    return dict(format='surface-internal-v1', partition=3, partuuid=DATA_UUID,
                start_lba=data['first'], end_lba=data['last'], bytes=data['bytes'],
                filesystem_uuid=filesystem['uuid'], filesystem_block_bytes=filesystem['block_bytes'],
                gpt_sha256=gpt['fingerprint'], disk_guid=gpt['disk_guid'],
                filesystem_superblock_sha256=filesystem['superblock_sha256'])


def preflight_utilities():
    growth_helper()
    seeding_helper()
    for utility in (str(GROW_HELPER), str(SEED_HELPER), 'sfdisk', 'e2fsck', 'resize2fs', 'mount', 'umount'):
        if not shutil.which(utility):
            raise Refused('Required installer/DATA growth utility is not installed: ' + utility)


@functools.lru_cache(maxsize=1)
def seeding_helper():
    path=no_symlinks(SEED_HELPER);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode&0o022 or not os.access(path,os.X_OK):
        raise Refused('The installed DATA migration helper is unsafe or missing')
    helper=runpy.run_path(str(path),run_name='surface_installer_seed')
    if any(not callable(helper.get(name)) for name in ('library_lock','mounted_data','source_plan','public_plan','apply_seed')):
        raise Refused('The installed DATA migration helper has an incompatible interface')
    return helper


def seed_call(helper,name,*args):
    try:return helper[name](*args)
    except ValueError as error:raise Refused('Live DATA migration: '+str(error)) from error


def migration_plan(helper,plan):
    seed_call(helper,'mounted_data')
    value=seed_call(helper,'source_plan',plan['data_proposed_bytes'])
    plan.update(user_data_bytes=value['bytes'],user_data_files=value['files'],rom_files=value['rom_files'],
                user_data_migration=True,root_auth_preserved=value['root_auth_preserved'],
                migration_manifest_sha256=value['manifest_sha256'])
    return value


def mounted_devices(mountinfo):
    result, root = {}, ''
    for line in mountinfo.splitlines():
        columns = line.split()
        if len(columns) < 6 or not re.fullmatch(r'\d+:\d+', columns[2]):
            continue
        mountpoint = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), columns[4])
        result.setdefault(columns[2], []).append(mountpoint)
        if mountpoint == '/':
            root = columns[2]
    return result, root


def device_family(name, sys_block=SYS_BLOCK):
    """Include partitions and device-mapper holders before checking mounts."""
    result, pending = set(), [name]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        entry = sys_block / current
        try:
            actual = entry.resolve(strict=True)
            for candidate in sys_block.iterdir():
                if (candidate / 'partition').exists() and candidate.resolve().parent == actual:
                    pending.append(candidate.name)
            pending.extend(holder.name for holder in (entry / 'holders').iterdir())
        except OSError:
            continue
    return result


def boot_partition_devnums():
    # Resolve the boot PARTUUID once before copying. After cloning, duplicate
    # UUIDs can change udev's by-partuuid symlink; the mounted root devnum remains
    # authoritative and the initial snapshot must not be silently replaced.
    match = re.search(r'(?:^|\s)root=PARTUUID=([0-9a-fA-F-]{36})(?:\s|$)', text('/proc/cmdline'))
    if not match:
        return set()
    try:
        entry = (DEV_ROOT / 'disk/by-partuuid' / match[1]).stat()
        if stat.S_ISBLK(entry.st_mode):
            return {f'{os.major(entry.st_rdev)}:{os.minor(entry.st_rdev)}'}
    except OSError:
        pass
    return set()


def disk_info(name, mountinfo, swaps='', sys_block=SYS_BLOCK, dev_root=DEV_ROOT, boot_devnums=None):
    """Pure read-only inventory; alternate roots support unit fixture planning."""
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
        raise Refused('Invalid kernel disk name')
    entry = sys_block / name
    if (entry / 'partition').exists():
        raise Refused('Select a whole disk, never a partition')
    family = device_family(name, sys_block)
    devnums = {text(sys_block / member / 'dev') for member in family}
    devnums.discard('')
    mounts, rootdev = mounted_devices(mountinfo)
    points = sorted({point for number in devnums for point in mounts.get(number, [])})
    stable = text(entry / 'device/serial') or text(entry / 'serial') or text(entry / 'wwid') or text(entry / 'device/wwid')
    if not stable or len(stable) > 128 or not all(32 <= ord(char) < 127 for char in stable):
        stable = ''
    model = ' '.join((text(entry / 'device/vendor') + ' ' + text(entry / 'device/model')).split())
    if not model:
        model = text(entry / 'device/name') or text(entry / 'device/model')
    try:
        size = int(text(entry / 'size', '0')) * 512
        logical = int(text(entry / 'queue/logical_block_size', '0'))
    except ValueError as error:
        raise Refused('Invalid disk size/sector metadata') from error
    real_parts = entry.resolve().parts
    usb = any(re.fullmatch(r'usb\d+', part, re.I) for part in real_parts)
    removable = text(entry / 'removable', '1') != '0'
    reasons = []
    if name.startswith(('loop', 'ram', 'zram', 'dm-', 'md', 'sr', 'fd')):
        reasons.append('not a supported physical internal disk')
    if usb:
        reasons.append('USB transport is excluded')
    if removable:
        reasons.append('removable media is excluded')
    if text(entry / 'ro', '1') != '0':
        reasons.append('disk is read-only')
    if not stable:
        reasons.append('no stable hardware serial/WWID')
    if not model:
        reasons.append('no hardware model')
    if not re.fullmatch(r'\d+:\d+', text(entry / 'dev')):
        reasons.append('no validated kernel device number')
    if size < MINIMUM_TARGET:
        reasons.append('disk is smaller than the 60,000,000,000-byte installation minimum')
    if logical != 512:
        reasons.append('this image requires 512-byte logical sectors')
    if stable:
        try:
            growth_call(growth_helper(), 'inspect_target', name, 'physical-internal', stable,
                        mountinfo, swaps, '', '', sys_block=sys_block, dev_root=dev_root)
        except (OSError, Refused) as error:
            reasons.append(str(error))
    running_system = bool(rootdev in devnums or devnums.intersection(boot_devnums or set()))
    if running_system:
        reasons.append('disk contains the running boot filesystem')
    if points:
        reasons.append('disk or a descendant is mounted: ' + ', '.join(points))
    # Active swap can reside on an otherwise unmounted partition.
    for line in swaps.splitlines()[1:]:
        fields = line.split()
        active = bool(fields and Path(fields[0]).name in family)
        if fields:
            try:
                swap = Path(fields[0]).stat()
                number = swap.st_rdev if stat.S_ISBLK(swap.st_mode) else swap.st_dev
                active = active or f'{os.major(number)}:{os.minor(number)}' in devnums
            except OSError:
                pass
        if active:
            reasons.append('disk contains active swap')
    return dict(device=str(dev_root / name), name=name, model=model[:160],
                stable_id=stable, bytes=size, logical_sector=logical,
                devnum=text(entry / 'dev'), family_devnums=sorted(devnums),
                usb=usb, removable=removable, mounts=points, running_system=running_system,
                eligible=not reasons, reasons=reasons, reason='; '.join(reasons))


def inventory(boot_devnums=None):
    mountinfo = Path('/proc/self/mountinfo').read_text()
    swaps = text('/proc/swaps')
    result = []
    for entry in sorted(SYS_BLOCK.iterdir()):
        if (entry / 'partition').exists() or entry.name.startswith(('loop', 'ram', 'zram', 'dm-', 'md', 'sr', 'fd')):
            continue
        try:
            result.append(disk_info(entry.name, mountinfo, swaps, boot_devnums=boot_devnums))
        except (OSError, Refused):
            continue
        if len(result) >= 64:
            break
    return result


def read_gpt(descriptor, size):
    if size < 1024 * 1024 or size % 512:
        raise Refused('Source is not a complete sector-aligned disk image')
    mbr = os.pread(descriptor, 512, 0)
    header = os.pread(descriptor, 512, 512)
    if len(mbr) != 512 or mbr[510:512] != b'\x55\xaa' or not any(mbr[450 + index * 16] == 0xEE for index in range(4)):
        raise Refused('Source has no protective GPT MBR')
    if len(header) != 512 or header[:8] != b'EFI PART':
        raise Refused('Source has no primary GPT')
    header_size, header_crc = struct.unpack_from('<II', header, 12)
    if not 92 <= header_size <= 512:
        raise Refused('Invalid GPT header size')
    checked = bytearray(header[:header_size])
    checked[16:20] = b'\0' * 4
    if zlib.crc32(checked) & 0xffffffff != header_crc:
        raise Refused('GPT header checksum failed')
    current, backup = struct.unpack_from('<QQ', header, 24)
    first_usable, last_usable = struct.unpack_from('<QQ', header, 40)
    table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', header, 72)
    if current != 1 or backup != size // 512 - 1 or not 1 <= count <= 4096 or not 128 <= entry_size <= 1024:
        raise Refused('Invalid GPT geometry')
    table_size = count * entry_size
    table_sectors = (table_size + 511) // 512
    if table_size > 4 * 1024 * 1024 or table_lba < 2 or table_lba * 512 + table_size > size:
        raise Refused('GPT partition table exceeds source bounds')
    if first_usable < table_lba + table_sectors or last_usable < first_usable or last_usable >= backup - table_sectors:
        raise Refused('GPT usable sectors overlap partition-table metadata')
    table = os.pread(descriptor, table_size, table_lba * 512)
    if len(table) != table_size or zlib.crc32(table) & 0xffffffff != table_crc:
        raise Refused('GPT partition table checksum failed')
    identities, types, ranges = set(), set(), []
    for offset in range(0, table_size, entry_size):
        entry = table[offset:offset + entry_size]
        if entry[:16] == b'\0' * 16:
            continue
        kind, identity = str(uuid.UUID(bytes_le=entry[:16])), str(uuid.UUID(bytes_le=entry[16:32]))
        first, last = struct.unpack_from('<QQ', entry, 32)
        if first < first_usable or last < first or last > last_usable:
            raise Refused('Source partition is outside image bounds')
        if identity in identities or identity == str(uuid.UUID(int=0)):
            raise Refused('Source has missing or duplicate partition identities')
        identities.add(identity)
        types.add(kind)
        ranges.append((first, last))
    ordered = sorted(ranges)
    if any(ordered[index][0] <= ordered[index - 1][1] for index in range(1, len(ordered))):
        raise Refused('Source partitions overlap')
    if ROOT_UUID not in identities or DATA_UUID not in identities or EFI_TYPE not in types:
        raise Refused('Source is not the expected Surface EFI/root/DATA image')
    secondary = os.pread(descriptor, 512, backup * 512)
    if len(secondary) != 512 or secondary[:8] != b'EFI PART':
        raise Refused('Backup GPT header is missing at the disk end')
    secondary_size, secondary_crc = struct.unpack_from('<II', secondary, 12)
    if not 92 <= secondary_size <= 512:
        raise Refused('Invalid backup GPT header size')
    checked = bytearray(secondary[:secondary_size])
    checked[16:20] = b'\0' * 4
    if zlib.crc32(checked) & 0xffffffff != secondary_crc:
        raise Refused('Backup GPT header checksum failed')
    secondary_current, secondary_backup, secondary_first, secondary_last = struct.unpack_from('<QQQQ', secondary, 24)
    secondary_lba, secondary_count, secondary_entry_size, secondary_table_crc = struct.unpack_from('<QIII', secondary, 72)
    if (secondary_current, secondary_backup, secondary_first, secondary_last) != (backup, 1, first_usable, last_usable) or secondary[56:72] != header[56:72]:
        raise Refused('Primary and backup GPT geometry disagree')
    if (secondary_count, secondary_entry_size, secondary_table_crc) != (count, entry_size, table_crc) or secondary_lba <= last_usable or secondary_lba + table_sectors > backup:
        raise Refused('Invalid backup GPT partition-table geometry')
    secondary_table = os.pread(descriptor, table_size, secondary_lba * 512)
    if secondary_table != table:
        raise Refused('Primary and backup GPT partition tables disagree')


def source_info(path):
    path = no_symlinks(Path(path).absolute())
    if path.name != 'surface-go.img':
        raise Refused('The staged installer source must be named surface-go.img')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise Refused('Installer source must be a regular file')
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise Refused('Source changed while being opened')
        read_gpt(descriptor, info.st_size)
        layout = source_data_layout(descriptor, info.st_size)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise Refused('Source changed while validating its DATA layout')
    finally:
        os.close(descriptor)
    sidecar = no_symlinks(Path(str(path) + '.sha256'))
    checksum_stat = sidecar.lstat()
    if not stat.S_ISREG(checksum_stat.st_mode) or checksum_stat.st_size > 4096:
        raise Refused('Missing or invalid trusted staged checksum sidecar')
    checksum = sidecar.read_text().strip()
    match = re.fullmatch(r'([0-9a-fA-F]{64})(?:\s+\*?(.+))?', checksum)
    if not match or (match[2] and Path(match[2]).name != path.name):
        raise Refused('Checksum sidecar does not identify this image')
    return dict(path=str(path), bytes=info.st_size, sha256=match[1].lower(),
                filesystem_devnum=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}',
                inode=info.st_ino, mtime_ns=info.st_mtime_ns,
                checksum_verified=False, checksum_source=str(sidecar), data_layout=layout)


def command(arguments, seconds=20):
    program = shutil.which(arguments[0])
    if not program:
        raise Refused(f'Required installer utility is not installed: {arguments[0]}')
    result = subprocess.run([program, *arguments[1:]], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=seconds, check=False)
    if result.returncode:
        raise Refused(f'{arguments[0]} failed: {(result.stderr or result.stdout).strip()[-500:]}')
    return result.stdout


def discover_sources(disks):
    require_guest()
    found, errors, seen = [], [], set()
    def inspect_mount(mountpoint):
        candidate = Path(mountpoint) / 'SurfaceGoGamingOS/surface-go.img'
        if not candidate.exists():
            return False
        try:
            info = source_info(candidate)
            key = (info['filesystem_devnum'], info['inode'])
            if key not in seen:
                found.append(info)
                seen.add(key)
        except (OSError, Refused, UnicodeError) as error:
            errors.append(str(error))
        return True
    mounts, _ = mounted_devices(Path('/proc/self/mountinfo').read_text())
    for points in mounts.values():
        for mountpoint in points:
            inspect_mount(mountpoint)
    if found:
        return found, errors
    # Only the removable/USB staging media is probed. Mounting a Windows target
    # partition here would make a safe internal target appear unavailable.
    for disk in disks:
        if not (disk['usb'] or disk['removable']):
            continue
        for name in sorted(device_family(disk['name'])):
            if not (SYS_BLOCK / name / 'partition').exists() or text(SYS_BLOCK / name / 'dev') in mounts:
                continue
            device = DEV_ROOT / name
            try:
                validate_target_node(device, whole_disk=False)
                probe = command(['blkid', '-p', '-o', 'export', str(device)], 8)
                fields = dict(line.split('=', 1) for line in probe.splitlines() if '=' in line)
                driver = {'ntfs': 'ntfs3', 'ext4': 'ext4', 'vfat': 'vfat', 'exfat': 'exfat'}.get(fields.get('TYPE'))
                if not driver:
                    continue
                mountpoint = SOURCE_MOUNTS / ('source-' + name)
                mountpoint.mkdir(parents=True, exist_ok=True, mode=0o700)
                options = 'ro,nosuid,nodev,noexec' + (',noload' if driver == 'ext4' else '')
                command(['mount', '-t', driver, '-o', options, str(device), str(mountpoint)])
                if not inspect_mount(mountpoint):
                    command(['umount', str(mountpoint)])
                    mountpoint.rmdir()
            except (OSError, Refused, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
            if len(found) >= 8:
                break
    return found, errors


def validate_target_node(path, whole_disk=True, dev_root=DEV_ROOT, sys_block=SYS_BLOCK):
    path = no_symlinks(Path(path))
    if path.parent != dev_root or not re.fullmatch(r'[A-Za-z0-9._-]+', path.name):
        raise Refused('Target must be a direct /dev kernel block node')
    info = path.lstat()
    if not stat.S_ISBLK(info.st_mode):
        raise Refused('Execution requires a real block device; regular files are never writable targets')
    if whole_disk and (sys_block / path.name / 'partition').exists():
        raise Refused('Target must be a whole disk')
    return info


def plan_install(target, source, expected_id=None, expected_sha=None):
    """Pure plan policy. Its inputs may come from read-only unit fixtures."""
    if not target['eligible']:
        raise Refused('Unsafe target: ' + target['reason'])
    if target['bytes'] < max(MINIMUM_TARGET, source['bytes']):
        raise Refused('Target is too small for this image')
    if source['filesystem_devnum'] in target['family_devnums']:
        raise Refused('Source image is stored on the target disk')
    if expected_id is not None and expected_id != target['stable_id']:
        raise Refused('Target stable identity changed since review')
    if expected_sha is not None and expected_sha.lower() != source['sha256']:
        raise Refused('Expected source SHA256 differs from the trusted staged sidecar')
    layout = source.get('data_layout', {})
    if (layout.get('format') != 'surface-internal-v1' or layout.get('partition') != 3 or
        layout.get('partuuid') != DATA_UUID or layout.get('bytes') != 4 * 1024 ** 3 or
        type(layout.get('start_lba')) is not int or type(layout.get('filesystem_block_bytes')) is not int):
        raise Refused('The source lacks a verified original internal DATA layout')
    data_end = target['bytes'] // 512 - 34
    partition_bytes = (data_end - layout['start_lba'] + 1) * 512
    block_bytes = layout['filesystem_block_bytes']
    if block_bytes not in (1024, 2048, 4096, 8192, 16384, 32768, 65536) or partition_bytes <= layout['bytes']:
        raise Refused('Target cannot safely expand the source DATA filesystem')
    filesystem_bytes = partition_bytes // block_bytes * block_bytes
    return dict(event='plan', target=target['device'], target_id=target['stable_id'],
                target_model=target['model'], target_bytes=target['bytes'],
                source=source['path'], source_bytes=source['bytes'],
                source_sha256=source['sha256'], confirmation='ERASE ' + target['stable_id'],
                warning='ALL DATA AND PARTITIONS ON THE SELECTED INTERNAL DISK WILL BE ERASED',
                data_expansion=f'Grow DATA from 4 GiB to {filesystem_bytes / 1024 ** 3:.2f} GiB before first mount',
                data_original_bytes=layout['bytes'], data_proposed_bytes=filesystem_bytes,
                data_partition_bytes=partition_bytes, data_start_lba=layout['start_lba'], data_end_lba=data_end,
                data_filesystem_uuid=layout['filesystem_uuid'],
                unused_bytes=target['bytes'] - source['bytes'], automatic_restart=False)


def progress_reader(descriptor, size, phase, write_descriptor=None):
    digest = hashlib.sha256()
    done, previous = 0, -1
    os.lseek(descriptor, 0, os.SEEK_SET)
    while done < size:
        data = os.read(descriptor, min(BLOCK_BYTES, size - done))
        if not data:
            raise Refused('Unexpected end of source/device during verification')
        digest.update(data)
        if write_descriptor is not None:
            offset = 0
            while offset < len(data):
                written = os.write(write_descriptor, data[offset:])
                if written <= 0:
                    raise Refused('Target write did not make progress')
                offset += written
        done += len(data)
        percent = done * 100 // size
        if percent != previous:
            emit('progress', phase=phase, percent=percent, bytes=done, total=size)
            previous = percent
    return digest.hexdigest()


def current_target(device, expected_id, boot_devnums=None):
    validate_target_node(Path(device))
    value = disk_info(Path(device).name, Path('/proc/self/mountinfo').read_text(), text('/proc/swaps'), boot_devnums=boot_devnums)
    if not value['eligible'] or value['stable_id'] != expected_id:
        raise Refused('Target identity, mount state, or eligibility changed; installation refused')
    strict = growth_call(growth_helper(), 'current_target', device, 'physical-internal', expected_id)
    if (strict['target'], strict['stable_id'], strict['bytes'], strict['devnum'], strict['logical_sector']) != (value['device'], value['stable_id'], value['bytes'], value['devnum'], value['logical_sector']):
        raise Refused('Installer and DATA growth target identities disagree')
    return value


def same_target(reviewed, fresh):
    keys = ('device', 'stable_id', 'bytes', 'devnum', 'logical_sector', 'model')
    if any(reviewed[key] != fresh[key] for key in keys):
        raise Refused('Target hardware model, capacity or identity changed since review')


def open_clone_target(device, target):
    descriptor = os.open(device, os.O_RDWR | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        actual = os.fstat(descriptor)
        if not stat.S_ISBLK(actual.st_mode) or f'{os.major(actual.st_rdev)}:{os.minor(actual.st_rdev)}' != target['devnum']:
            raise Refused('Target block node changed before cloning')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def parse_growth_json(value):
    if len(value) > 256 * 1024:
        raise Refused('DATA growth response is too large')
    try:
        result = json.loads(value)
    except ValueError as error:
        raise Refused('DATA growth returned invalid JSON') from error
    if not isinstance(result, dict):
        raise Refused('DATA growth returned an invalid response object')
    return result


def validate_growth_plan(value, target, source, plan):
    layout = source['data_layout']
    token = value.get('review_token')
    expected = dict(event='plan', profile='physical-internal', target=target['device'],
                    target_id=target['stable_id'], target_devnum=target['devnum'],
                    target_bytes=target['bytes'], disk_guid=layout['disk_guid'],
                    gpt_sha256=layout['gpt_sha256'], filesystem_uuid=layout['filesystem_uuid'],
                    filesystem_superblock_sha256=layout['filesystem_superblock_sha256'],
                    data_partition=3, data_partuuid=DATA_UUID, data_start_lba=plan['data_start_lba'],
                    old_data_end_lba=layout['end_lba'], new_data_end_lba=plan['data_end_lba'],
                    old_data_bytes=plan['data_original_bytes'], new_data_bytes=plan['data_partition_bytes'])
    if any(value.get(key) != wanted for key, wanted in expected.items()):
        raise Refused('Post-clone DATA growth plan differs from the reviewed target or source layout')
    if (value.get('partition_starts_and_uuids_preserved') is not True or value.get('unused_space_only') is not True or
        value.get('requires_unmounted') is not True or value.get('automatic_on_boot') is not False or
        not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token)):
        raise Refused('DATA growth plan lacks its safety conditions or internal review token')
    return token


def validate_growth_completion(output, target, source, plan):
    if len(output) > 512 * 1024:
        raise Refused('DATA growth output exceeds its expected bound')
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise Refused('DATA growth returned no completion receipt')
    complete = parse_growth_json(lines[-1])
    expected = dict(event='grow-complete', target=target['device'], target_id=target['stable_id'],
                    data_partuuid=DATA_UUID, filesystem_uuid=source['data_layout']['filesystem_uuid'],
                    old_bytes=plan['data_original_bytes'], new_bytes=plan['data_proposed_bytes'],
                    data_start_lba=plan['data_start_lba'], data_end_lba=plan['data_end_lba'])
    if any(complete.get(key) != wanted for key, wanted in expected.items()) or complete.get('efi_root_payload_preserved') is not True or complete.get('old_data_payload_preserved_before_resize') is not True or complete.get('mounted') is not False:
        raise Refused('DATA growth completion is missing, unexpanded or inconsistent with the reviewed plan')
    return complete


def verify_final_growth(source_descriptor, target, source, plan):
    helper = growth_helper()
    original = growth_call(helper, 'read_gpt', source_descriptor, source['bytes'])
    if original['fingerprint'] != source['data_layout']['gpt_sha256']:
        raise Refused('Source GPT changed after cloning')
    strict = growth_call(helper, 'current_target', target['device'], 'physical-internal', target['stable_id'])
    descriptor = growth_call(helper, 'open_readonly_target', strict)
    try:
        read_gpt(descriptor, target['bytes'])
        final = growth_call(helper, 'read_gpt', descriptor, target['bytes'])
        growth_call(helper, 'verify_preserved', original, final, plan['data_end_lba'])
        partition = final['parts'][2]
        device = growth_call(helper, 'data_device', strict, partition)
        filesystem = growth_call(helper, 'read_data_filesystem', strict, partition, device)
        if (filesystem['uuid'], filesystem['bytes']) != (plan['data_filesystem_uuid'], plan['data_proposed_bytes']):
            raise Refused('Final DATA filesystem identity or capacity differs from the reviewed installation')
        return filesystem
    finally:
        os.close(descriptor)


def execute_install(arguments):
    require_guest()
    helper=seeding_helper()
    with helper['library_lock']():
        return execute_install_locked(arguments,helper)


def execute_install_locked(arguments,seed_helper):
    require_guest()  # Recheck inside the only function that can open writable.
    if not arguments.target or not arguments.source or not arguments.expected_target_id or not arguments.expected_source_sha256:
        raise Refused('Execution requires explicit target, source, expected target ID, and expected source SHA256')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', arguments.expected_source_sha256):
        raise Refused('Expected source SHA256 must contain exactly 64 hexadecimal digits')
    if arguments.confirm != 'ERASE ' + arguments.expected_target_id:
        raise Refused('Exact typed ERASE <stable-id> confirmation is required')
    boot_devices = boot_partition_devnums()
    target = current_target(arguments.target, arguments.expected_target_id, boot_devices)
    source = source_info(arguments.source)
    plan = plan_install(target, source, arguments.expected_target_id, arguments.expected_source_sha256)
    preflight_utilities()
    migration=migration_plan(seed_helper,plan)
    source_descriptor = os.open(source['path'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    target_descriptor = None
    try:
        opened = os.fstat(source_descriptor)
        if (opened.st_ino, opened.st_size, opened.st_mtime_ns) != (source['inode'], source['bytes'], source['mtime_ns']) or f'{os.major(opened.st_dev)}:{os.minor(opened.st_dev)}' != source['filesystem_devnum']:
            raise Refused('Source changed after planning')
        emit('progress', phase='verify-source', percent=0, total=source['bytes'])
        checksum = progress_reader(source_descriptor, source['bytes'], 'verify-source')
        after = os.fstat(source_descriptor)
        if checksum != plan['source_sha256'] or after.st_mtime_ns != opened.st_mtime_ns or after.st_size != opened.st_size:
            raise Refused('Source SHA256 verification failed; target was not opened writable')
        # No writable open occurs above this point. Re-read stable hardware ID
        # and every mounted descendant after the potentially long checksum pass.
        fresh = current_target(arguments.target, arguments.expected_target_id, boot_devices)
        same_target(target, fresh)
        plan_install(fresh, source, arguments.expected_target_id, arguments.expected_source_sha256)
        target_descriptor = open_clone_target(arguments.target, fresh)
        newest = current_target(arguments.target, arguments.expected_target_id, boot_devices)
        same_target(target, newest)
        emit('progress', phase='copy-image', percent=0, total=source['bytes'])
        copied = progress_reader(source_descriptor, source['bytes'], 'copy-image', target_descriptor)
        os.fsync(target_descriptor)
        if copied != plan['source_sha256']:
            raise Refused('Source changed during copying; installation is incomplete')
        emit('progress', phase='verify-target', percent=0, total=source['bytes'])
        if progress_reader(target_descriptor, source['bytes'], 'verify-target') != plan['source_sha256']:
            raise Refused('Target readback checksum failed; installation is incomplete')
        os.close(target_descriptor)
        target_descriptor = None
        same_target(target, current_target(arguments.target, arguments.expected_target_id, boot_devices))
        common = ['--target', arguments.target, '--profile', 'physical-internal', '--expected-target-id', arguments.expected_target_id]
        emit('progress', phase='plan-data-growth', percent=0)
        proposed = parse_growth_json(command([str(GROW_HELPER), '--plan', *common], 60))
        token = validate_growth_plan(proposed, target, source, plan)
        same_target(target, current_target(arguments.target, arguments.expected_target_id, boot_devices))
        emit('progress', phase='grow-unmounted-data', percent=0,
             original_bytes=plan['data_original_bytes'], proposed_bytes=plan['data_proposed_bytes'])
        output = command([str(GROW_HELPER), '--execute', *common, '--review-token', token], 900)
        completed = validate_growth_completion(output, target, source, plan)
        emit('data-growth', **{key: value for key, value in completed.items() if key != 'event'})
        os.sync()
        final = current_target(arguments.target, arguments.expected_target_id, boot_devices)
        same_target(target, final)
        filesystem = verify_final_growth(source_descriptor, final, source, plan)
        emit('progress',phase='preserve-user-data',percent=0,bytes=0,total=migration['bytes'])
        seeded=seed_call(seed_helper,'apply_seed',arguments.target,arguments.expected_target_id,
                         filesystem['uuid'],filesystem['bytes'],migration)
        if (seeded.get('event')!='seed-complete' or seeded.get('target')!=arguments.target or
            seeded.get('target_id')!=arguments.expected_target_id or seeded.get('verified') is not True or
            any(seeded.get(key)!=migration[key] for key in ('files','bytes','rom_files','root_auth_preserved'))):
            raise Refused('Live DATA migration did not return a complete verified receipt')
        same_target(target,current_target(arguments.target,arguments.expected_target_id,boot_devices))
        emit('complete', target=arguments.target, target_id=arguments.expected_target_id,
             source_sha256=plan['source_sha256'], data_expansion=plan['data_expansion'],
             data_expansion_verified=True, data_actual_bytes=filesystem['bytes'],
             data_filesystem_uuid=filesystem['uuid'], data_partuuid=DATA_UUID,
             user_data_verified=True,user_data_files=seeded['files'],user_data_bytes=seeded['bytes'],
             rom_files=seeded['rom_files'],root_auth_preserved=seeded['root_auth_preserved'],
             message='Installation and live games/profiles verified. '+('Your existing password is retained. ' if seeded['root_auth_preserved'] else '')+'Power off first, remove USB boot media after shutdown, then power on to boot internal storage.', automatic_restart=False)
    finally:
        os.close(source_descriptor)
        if target_descriptor is not None:
            os.close(target_descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list', action='store_true')
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--target')
    parser.add_argument('--source')
    parser.add_argument('--expected-target-id')
    parser.add_argument('--expected-source-sha256')
    parser.add_argument('--confirm')
    arguments = parser.parse_args(argv)
    try:
        require_guest()  # Before discovery mounts, directory creation, or writes.
        if arguments.execute:
            execute_install(arguments)
        elif arguments.list:
            boot_devices = boot_partition_devnums()
            disks = inventory(boot_devices)
            sources, errors = discover_sources(disks)
            # Discovery may mount an eligible USB source read-only; refresh the
            # mount inventory before presenting any target to the controller UI.
            emit('list', targets=inventory(boot_devices), sources=sources, errors=errors,
                 minimum_target_bytes=MINIMUM_TARGET)
        else:
            if not arguments.target or not arguments.source:
                raise Refused('Planning requires an explicit target and source image')
            validate_target_node(Path(arguments.target))
            target = disk_info(Path(arguments.target).name, Path('/proc/self/mountinfo').read_text(), text('/proc/swaps'), boot_devnums=boot_partition_devnums())
            target = current_target(arguments.target, arguments.expected_target_id or target['stable_id'], boot_partition_devnums())
            source = source_info(arguments.source)
            preflight_utilities()
            plan=plan_install(target, source, arguments.expected_target_id, arguments.expected_source_sha256)
            helper=seeding_helper()
            with helper['library_lock']():migration_plan(helper,plan)
            print(json.dumps(plan, ensure_ascii=False), flush=True)
        return 0
    except (OSError, Refused, UnicodeError, subprocess.TimeoutExpired) as error:
        emit('error', message=str(error), target_written=False if not arguments.execute else 'unknown; inspect progress phase')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
