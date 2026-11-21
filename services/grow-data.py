#!/usr/bin/env python3
"""Grow the freshly cloned Surface internal DATA partition before its first mount.

The CLI has no regular-file execution mode, alternate device/sysfs roots, host
override, boot-time auto-grow path, or shrinking operation. --plan is read-only.
The installer consumes its review_token only after its existing erase review.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid
import zlib

MARKER = Path('/etc/surface-image-id')
SYS_BLOCK = Path('/sys/class/block')
VM_DMI = 'Surface Go OS Disposable Test VM'
VM_TARGET_ID = 'SURFACE_TEST_TARGET'
SECTOR = 512
MINIMUM_TARGET = 60_000_000_000
ROOT_UUID = '8b4a530e-d6d4-4e69-9307-310000000001'
DATA_UUID = '8b4a530e-d6d4-4e69-9307-310000000002'
EFI_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
ROOT_TYPE = '44479540-f297-41b2-9af7-d131d5f0458a'
DATA_TYPE = '0fc63daf-8483-4772-8e79-3d69d8477de4'
ORIGINAL_DATA_BYTES = 4 * 1024 ** 3
BLKRRPART = 0x125f
BLKGETSIZE64 = 0x80081272


class Refused(ValueError):
    pass


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), sort_keys=True), flush=True)


def text(path, fallback=''):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return fallback


def require_guest():
    try:
        info = MARKER.lstat()
        valid = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        valid = valid and MARKER.read_text().strip().startswith('surface-go-')
    except (OSError, UnicodeError):
        valid = False
    if not valid:
        raise Refused('Refusing host execution: the booted Surface OS image is required')
    if os.geteuid() != 0:
        raise Refused('DATA growth requires the privileged image installer')


def no_symlinks(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('An absolute target without parent traversal is required')
    current = Path('/')
    for component in path.parts[1:]:
        current /= component
        if stat.S_ISLNK(current.lstat().st_mode):
            raise Refused('Symlink target paths are forbidden')
    return path


def family(name, sys_block=SYS_BLOCK):
    result, pending = set(), [name]
    while pending:
        member = pending.pop()
        if member in result:
            continue
        result.add(member)
        node = sys_block / member
        actual = node.resolve(strict=True)
        for candidate in sys_block.iterdir():
            if (candidate / 'partition').exists() and candidate.resolve().parent == actual:
                pending.append(candidate.name)
        holders = node / 'holders'
        if holders.exists():
            pending.extend(item.name for item in holders.iterdir())
    return result


def inspect_target(name, profile, expected_id, mountinfo, swaps, dmi, cmdline,
                   sys_block=SYS_BLOCK, dev_root=Path('/dev')):
    """Read-only inventory; fixture roots are function arguments, never CLI flags."""
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name) or name.startswith(('loop', 'ram', 'zram', 'dm-', 'md', 'sr', 'fd')):
        raise Refused('A whole internal disk is required')
    node = sys_block / name
    if (node / 'partition').exists():
        raise Refused('A partition cannot be the whole-disk growth target')
    members = family(name, sys_block)
    numbers = {text(sys_block / member / 'dev') for member in members}
    numbers.discard('')
    stable = text(node / 'device/serial') or text(node / 'serial') or text(node / 'wwid') or text(node / 'device/wwid')
    model = ' '.join((text(node / 'device/vendor') + ' ' + text(node / 'device/model')).split()) or text(node / 'device/name')
    if not stable or len(stable) > 128 or not all(32 <= ord(char) < 127 for char in stable) or stable != expected_id:
        raise Refused('Target stable hardware identity is missing or changed since review')
    try:
        size = int(text(node / 'size', '0')) * SECTOR
        logical = int(text(node / 'queue/logical_block_size', '0'))
    except ValueError as error:
        raise Refused('Invalid kernel disk geometry') from error
    real_parts = node.resolve(strict=True).parts
    usb = any(re.fullmatch(r'usb\d+', part, re.I) for part in real_parts)
    if profile == 'physical-internal':
        if usb:
            raise Refused('USB transport is never an internal installation target')
        if not model or any(part.startswith('virtio') for part in real_parts):
            raise Refused('Physical mode requires an identified physical internal disk')
        # PCI/NVMe, ATA/SCSI on PCI, and soldered eMMC are supported. Unknown
        # virtual/network platform devices do not become eligible by name alone.
        if not any(re.fullmatch(r'pci[0-9a-fA-F:]+', part) or part.startswith('mmc_host') for part in real_parts):
            raise Refused('Unknown internal hardware transport')
    elif profile == 'vm-install':
        if dmi != VM_DMI or not re.search(r'(?:^|\s)surface\.vm-install=1(?:\s|$)', cmdline):
            raise Refused('Disposable VM profile requires exact DMI and installer boot opt-in')
        if stable != VM_TARGET_ID or not any(re.fullmatch(r'virtio\d+', part) for part in real_parts):
            raise Refused('Disposable VM target must be the explicitly serialized virtio test disk')
        if usb:
            raise Refused('USB installer media is never grown by this profile')
        model = model or 'QEMU virtio disposable installation target'
    else:
        raise Refused('An explicit supported installer profile is required')
    if text(node / 'removable', '1') != '0' or text(node / 'ro', '1') != '0':
        raise Refused('Removable or read-only targets are excluded')
    if logical != SECTOR or size < MINIMUM_TARGET:
        raise Refused('Target requires 512-byte sectors and at least 60,000,000,000 bytes')
    number = text(node / 'dev')
    if not re.fullmatch(r'\d+:\d+', number):
        raise Refused('Invalid whole-disk kernel device identity')
    for line in mountinfo.splitlines():
        columns = line.split()
        if len(columns) > 4 and columns[2] in numbers:
            raise Refused('Target or descendant is mounted, including the running boot disk')
    for member in members:
        holders = sys_block / member / 'holders'
        if holders.exists() and any(holders.iterdir()):
            raise Refused('Target has active device-mapper or other block holders')
    for line in swaps.splitlines()[1:]:
        columns = line.split()
        if columns and Path(columns[0]).name in members:
            raise Refused('Target contains active swap')
        if columns:
            try:
                swap = Path(columns[0]).stat()
                dev = swap.st_rdev if stat.S_ISBLK(swap.st_mode) else swap.st_dev
                if f'{os.major(dev)}:{os.minor(dev)}' in numbers:
                    raise Refused('Target contains active swap')
            except OSError:
                pass
    return dict(target=str(dev_root / name), name=name, stable_id=stable, model=model,
                bytes=size, logical_sector=logical, devnum=number, profile=profile)


def current_target(path, profile, expected_id):
    path = no_symlinks(path)
    if path.parent != Path('/dev'):
        raise Refused('Target must be a direct /dev kernel block node')
    info = path.lstat()
    if not stat.S_ISBLK(info.st_mode):
        raise Refused('Execution and CLI planning require a real block disk; regular files are fixtures only')
    value = inspect_target(path.name, profile, expected_id, Path('/proc/self/mountinfo').read_text(),
                           text('/proc/swaps'), text('/sys/class/dmi/id/product_name'), text('/proc/cmdline'))
    if value['devnum'] != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}':
        raise Refused('Device node differs from its kernel identity')
    return value


def exact_read(descriptor, count, offset):
    data = os.pread(descriptor, count, offset)
    if len(data) != count:
        raise Refused('Truncated GPT or filesystem metadata')
    return data


def checked_header(header, expected_current, disk_sectors):
    if header[:8] != b'EFI PART':
        raise Refused('Missing GPT header')
    revision, size, checksum, reserved = struct.unpack_from('<IIII', header, 8)
    if revision != 0x10000 or size != 92 or reserved != 0:
        raise Refused('Unknown GPT revision or header format')
    checked = bytearray(header[:size]);checked[16:20] = b'\0' * 4
    if zlib.crc32(checked) & 0xffffffff != checksum:
        raise Refused('GPT header CRC failed')
    current, backup, first, last = struct.unpack_from('<QQQQ', header, 24)
    table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', header, 72)
    if current != expected_current or not 1 <= backup < disk_sectors or current == backup:
        raise Refused('Invalid GPT header locations')
    if count != 128 or entry_size != 128 or not 34 <= first <= last < disk_sectors:
        raise Refused('Unknown GPT partition table geometry')
    if header[56:72] == b'\0' * 16:
        raise Refused('Missing GPT disk GUID')
    return dict(current=current, backup=backup, first=first, last=last, table_lba=table_lba,
                count=count, entry_size=entry_size, table_crc=table_crc)


def read_gpt(descriptor, disk_bytes):
    """Validate both GPT copies, even before the old backup is relocated."""
    if disk_bytes % SECTOR or disk_bytes < 1024 * 1024:
        raise Refused('Invalid sector-aligned disk size')
    sectors = disk_bytes // SECTOR
    mbr = exact_read(descriptor, SECTOR, 0)
    mbr_entries = [mbr[446 + index * 16:462 + index * 16] for index in range(4)]
    protective = [entry for entry in mbr_entries if entry[4] == 0xEE]
    if mbr[510:] != b'\x55\xaa' or len(protective) != 1 or any(entry != b'\0' * 16 for entry in mbr_entries if entry[4] != 0xEE):
        raise Refused('A protective GPT MBR without a hybrid/USB layout is required')
    if struct.unpack_from('<I', protective[0], 8)[0] != 1:
        raise Refused('Invalid protective MBR start')
    primary_raw = exact_read(descriptor, SECTOR, SECTOR)
    primary = checked_header(primary_raw, 1, sectors)
    if primary['table_lba'] != 2 or primary['first'] != 34 or primary['last'] >= primary['backup'] - 32:
        raise Refused('Primary GPT metadata overlaps usable space')
    backup_raw = exact_read(descriptor, SECTOR, primary['backup'] * SECTOR)
    backup = checked_header(backup_raw, primary['backup'], sectors)
    if backup['backup'] != 1 or backup['table_lba'] != primary['backup'] - 32:
        raise Refused('Invalid backup GPT partition table location')
    if (backup['first'], backup['last'], backup['table_crc'], backup_raw[56:72]) != (primary['first'], primary['last'], primary['table_crc'], primary_raw[56:72]):
        raise Refused('Primary and backup GPT identities or geometry disagree')
    table = exact_read(descriptor, 128 * 128, 2 * SECTOR)
    secondary = exact_read(descriptor, len(table), backup['table_lba'] * SECTOR)
    if zlib.crc32(table) & 0xffffffff != primary['table_crc'] or table != secondary:
        raise Refused('GPT partition table CRC or backup comparison failed')
    parts, identities = [], set()
    for index in range(128):
        raw = table[index * 128:(index + 1) * 128]
        if raw[:16] == b'\0' * 16:
            if raw != b'\0' * 128:
                raise Refused('Unknown nonzero unused GPT entry')
            continue
        identity = str(uuid.UUID(bytes_le=raw[16:32]))
        first, last, attributes = struct.unpack_from('<QQQ', raw, 32)
        if identity in identities or identity == str(uuid.UUID(int=0)):
            raise Refused('Missing or duplicate partition identity')
        identities.add(identity)
        if not primary['first'] <= first <= last <= primary['last']:
            raise Refused('Partition lies outside validated GPT usable space')
        try:
            name = raw[56:128].decode('utf-16le').rstrip('\0')
        except UnicodeError as error:
            raise Refused('Invalid GPT partition name') from error
        parts.append(dict(number=index + 1, type=str(uuid.UUID(bytes_le=raw[:16])), uuid=identity,
                          first=first, last=last, bytes=(last - first + 1) * SECTOR,
                          attributes=attributes, name=name))
    if [part['number'] for part in parts] != [1, 2, 3]:
        raise Refused('Only the exact three-partition installed image is supported; USB/four-partition layouts are refused')
    if any(parts[index]['first'] <= parts[index - 1]['last'] for index in range(1, len(parts))):
        raise Refused('GPT partitions overlap or DATA is not last')
    expected = [(EFI_TYPE, 'boot', 64 * 1024 ** 2, 4), (ROOT_TYPE, 'root', 2 * 1024 ** 3, 0), (DATA_TYPE, 'data', None, 0)]
    for part, (kind, name, size, attributes) in zip(parts, expected):
        if part['type'] != kind or part['name'] != name or part['attributes'] != attributes or (size is not None and part['bytes'] != size):
            raise Refused('Unknown EFI/root/DATA partition type, size, name or attributes')
    if parts[0]['first'] != 2048 or parts[1]['first'] != parts[0]['last'] + 1 or parts[2]['first'] != parts[1]['last'] + 1:
        raise Refused('Installed image partition starts differ from the reviewed layout')
    if parts[1]['uuid'] != ROOT_UUID or parts[2]['uuid'] != DATA_UUID:
        raise Refused('Surface root/DATA partition UUIDs do not match')
    return dict(primary=primary, disk_guid=str(uuid.UUID(bytes_le=primary_raw[56:72])),
                table=table, primary_raw=primary_raw, backup_raw=backup_raw, mbr=mbr,
                parts=parts, fingerprint=hashlib.sha256(mbr + primary_raw + table + backup_raw).hexdigest())


def read_ext4(descriptor, partition, pristine=False):
    data = exact_read(descriptor, 1024, partition['first'] * SECTOR + 1024)
    magic, state = struct.unpack_from('<HH', data, 0x38)
    exponent = struct.unpack_from('<I', data, 0x18)[0]
    incompat = struct.unpack_from('<I', data, 0x60)[0]
    if magic != 0xEF53 or exponent > 6 or state != 1 or incompat & 4:
        raise Refused('DATA must contain a clean ext4 filesystem without journal recovery pending')
    blocks = struct.unpack_from('<I', data, 4)[0]
    if incompat & 0x80:
        blocks |= struct.unpack_from('<I', data, 0x150)[0] << 32
    block_bytes = 1024 << exponent
    size = blocks * block_bytes
    label = data[0x78:0x88].split(b'\0', 1)[0]
    if label != b'SURFACE_DATA' or size <= 0 or size > partition['bytes'] or data[0x68:0x78] == b'\0' * 16:
        raise Refused('DATA ext4 label, filesystem UUID or size does not match the image')
    mount_time = struct.unpack_from('<I', data, 0x2c)[0]
    mount_count = struct.unpack_from('<H', data, 0x34)[0]
    if pristine and (size != ORIGINAL_DATA_BYTES or mount_time != 0 or mount_count != 0 or any(data[0x88:0xc8])):
        raise Refused('Only the original never-mounted 4 GiB cloned DATA filesystem can be grown')
    return dict(uuid=str(uuid.UUID(bytes=data[0x68:0x78])), bytes=size, block_bytes=block_bytes,
                mount_time=mount_time, mount_count=mount_count,
                superblock_sha256=hashlib.sha256(data).hexdigest())


def plan_grow(target, gpt, filesystem):
    data = gpt['parts'][2]
    if data['bytes'] != ORIGINAL_DATA_BYTES or filesystem['bytes'] != ORIGINAL_DATA_BYTES or filesystem['mount_time'] or filesystem['mount_count']:
        raise Refused('Growth is restricted to the original freshly cloned 4 GiB DATA partition')
    final_last = target['bytes'] // SECTOR - 34
    if final_last <= data['last']:
        raise Refused('There is no unused space after the last DATA partition')
    result = dict(event='plan', profile=target['profile'], target=target['target'],
                  target_id=target['stable_id'], target_model=target['model'], target_devnum=target['devnum'],
                  target_bytes=target['bytes'], disk_guid=gpt['disk_guid'],
                  gpt_sha256=gpt['fingerprint'], filesystem_uuid=filesystem['uuid'],
                  filesystem_superblock_sha256=filesystem['superblock_sha256'],
                  data_partition=3, data_partuuid=DATA_UUID, data_start_lba=data['first'],
                  old_data_end_lba=data['last'], new_data_end_lba=final_last,
                  old_data_bytes=data['bytes'], new_data_bytes=(final_last - data['first'] + 1) * SECTOR,
                  partition_starts_and_uuids_preserved=True, unused_space_only=True,
                  requires_unmounted=True, automatic_on_boot=False,
                  sfdisk_input=f"start={data['first']}, size={final_last - data['first'] + 1}\n")
    encoded = json.dumps(result, sort_keys=True, separators=(',', ':')).encode()
    result['review_token'] = hashlib.sha256(b'surface-installed-data-grow-v1\0' + encoded).hexdigest()
    return result


def open_readonly_target(target):
    descriptor = os.open(target['target'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != target['devnum']:
        os.close(descriptor)
        raise Refused('Target block device changed while being opened')
    actual_bytes = bytearray(8)
    try:
        fcntl.ioctl(descriptor, BLKGETSIZE64, actual_bytes)
        if struct.unpack('<Q', actual_bytes)[0] != target['bytes']:
            raise Refused('Opened target capacity differs from the reviewed kernel geometry')
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def unchanged_target(arguments, original):
    current = current_target(arguments.target, arguments.profile, arguments.expected_target_id)
    if current != original:
        raise Refused('Target model, capacity or kernel identity changed since review')
    return current


def inspect_plan(arguments):
    target = current_target(arguments.target, arguments.profile, arguments.expected_target_id)
    descriptor = open_readonly_target(target)
    try:
        gpt = read_gpt(descriptor, target['bytes'])
        filesystem = read_ext4(descriptor, gpt['parts'][2], pristine=True)
        return target, gpt, filesystem, plan_grow(target, gpt, filesystem)
    finally:
        os.close(descriptor)


def verify_preserved(before, after, new_end=None):
    """Only the end LBA of GPT entry 3 may change; every other entry byte stays."""
    if before['disk_guid'] != after['disk_guid'] or before['primary']['first'] != after['primary']['first']:
        raise Refused('GPT disk identity or first usable sector changed')
    expected = bytearray(before['table'])
    if new_end is not None:
        struct.pack_into('<Q', expected, 2 * 128 + 40, new_end)
    if after['table'] != expected:
        raise Refused('GPT changed a preserved partition entry, start, UUID, type, attribute or name')
    if after['primary']['backup'] != after['primary']['last'] + 33:
        raise Refused('GPT backup is not immediately after its usable range')


def payload_hashes(descriptor, partitions):
    output = []
    for part in partitions:
        digest = hashlib.sha256();at = part['first'] * SECTOR;remaining = part['bytes']
        while remaining:
            data = os.pread(descriptor, min(4 * 1024 * 1024, remaining), at)
            if not data:
                raise Refused('Unexpected end of partition payload during preservation verification')
            digest.update(data);remaining -= len(data);at += len(data)
        output.append(dict(partition=part['number'], bytes=part['bytes'], sha256=digest.hexdigest()))
        emit('payload-hash', partition=part['number'], bytes=part['bytes'])
    return output


def run(arguments, input_text=None, accepted=(0,), seconds=600):
    program = shutil.which(arguments[0])
    if not program:
        raise Refused('Required guest installer utility is missing: ' + arguments[0])
    value = subprocess.run([program, *arguments[1:]], input=input_text, capture_output=True,
                           text=True, timeout=seconds, check=False)
    if value.returncode not in accepted:
        raise Refused(f"{arguments[0]} exited {value.returncode}: {(value.stderr or value.stdout).strip()[-1000:]}")
    emit('utility-complete', utility=arguments[0], exit=value.returncode,
         diagnostic=(value.stdout + value.stderr).strip()[-2000:])


def sfdisk_arguments(target, relocate=False):
    common = ['sfdisk', '--lock=yes', '--wipe', 'never', '--wipe-partitions', 'never']
    return common + (['--relocate', 'gpt-bak-std', target] if relocate else ['-N', '3', target])


def data_device(target, partition):
    matches = []
    parent = (SYS_BLOCK / target['name']).resolve(strict=True)
    for node in SYS_BLOCK.iterdir():
        if text(node / 'partition') != '3' or node.resolve(strict=True).parent != parent:
            continue
        if text(node / 'start') != str(partition['first']) or text(node / 'size') != str(partition['bytes'] // SECTOR):
            raise Refused('Kernel DATA start or size differs from the verified GPT')
        device = no_symlinks(Path('/dev') / node.name)
        info = device.lstat()
        if not stat.S_ISBLK(info.st_mode) or text(node / 'dev') != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}':
            raise Refused('Kernel DATA partition device identity changed')
        matches.append(str(device))
    if len(matches) != 1:
        raise Refused('The kernel did not expose exactly one matching DATA partition')
    return matches[0]


def read_data_filesystem(target, partition, device):
    """Verify through the same block-device cache alias used by resize2fs.

    The whole disk has already been read for preservation hashing. Linux may
    retain those cached pages after writes through its partition block node;
    sync writes dirty pages but does not invalidate that separate read cache.
    Bind a fresh read-only partition FD to its sysfs identity and capacity.
    """
    if data_device(target, partition) != device:
        raise Refused('DATA device changed before final filesystem verification')
    identity = dict(target=device, devnum=text(SYS_BLOCK / Path(device).name / 'dev'), bytes=partition['bytes'])
    descriptor = open_readonly_target(identity)
    try:
        if data_device(target, partition) != device or text(SYS_BLOCK / Path(device).name / 'dev') != identity['devnum']:
            raise Refused('DATA kernel identity changed while its verification descriptor was opened')
        # A partition descriptor starts at its own LBA zero. Preserve its
        # reviewed capacity for the existing strict ext4 metadata checks.
        return read_ext4(descriptor, dict(partition, first=0))
    finally:
        os.close(descriptor)


def execute(arguments):
    require_guest()  # First instruction in the only block-mutating entrypoint.
    target, original, filesystem, plan = inspect_plan(arguments)
    if not re.fullmatch(r'[0-9a-f]{64}', arguments.review_token or '') or arguments.review_token != plan['review_token']:
        raise Refused('Exact internal review token is required and the target/GPT must remain unchanged')
    for utility in ('sfdisk', 'e2fsck', 'resize2fs'):
        if not shutil.which(utility):
            raise Refused('Required guest installer utility is missing: ' + utility)
    # A private process lock serializes this helper. sfdisk additionally takes
    # its own whole-device lock and retains its normal in-use/reread checks.
    lock_path = Path('/run') / ('surface-grow-data-' + target['devnum'].replace(':', '-') + '.lock')
    lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    descriptor = None
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise Refused('Unsafe installer operation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor = open_readonly_target(target)
        emit('grow-begin', target=target['target'], target_id=target['stable_id'], old_bytes=plan['old_data_bytes'], new_bytes=plan['new_data_bytes'])
        emit('progress', phase='hash-original-payload')
        original_payload = payload_hashes(descriptor, original['parts'])
        # Hashing may take time: repeat every identity/mount check and rebind the
        # original review token before allowing a utility to write GPT metadata.
        fresh, fresh_gpt, _, fresh_plan = inspect_plan(arguments)
        if fresh != target or fresh_plan['review_token'] != arguments.review_token:
            raise Refused('Target changed while its payload was being verified')
        if original['primary']['backup'] != target['bytes'] // SECTOR - 1:
            emit('progress', phase='relocate-backup-gpt')
            run(sfdisk_arguments(target['target'], relocate=True), seconds=120)
            moved = read_gpt(descriptor, target['bytes']);verify_preserved(fresh_gpt, moved)
            if moved['primary']['backup'] != target['bytes'] // SECTOR - 1:
                raise Refused('Backup GPT was not relocated to the target disk end')
        unchanged_target(arguments, target)
        emit('progress', phase='grow-last-data-partition')
        run(sfdisk_arguments(target['target']), plan['sfdisk_input'], seconds=120)
        os.fsync(descriptor)
        final_gpt = read_gpt(descriptor, target['bytes'])
        verify_preserved(original, final_gpt, plan['new_data_end_lba'])
        if final_gpt['primary']['backup'] != target['bytes'] // SECTOR - 1 or final_gpt['primary']['last'] != plan['new_data_end_lba']:
            raise Refused('Final GPT does not use the target disk end')
        emit('progress', phase='verify-partition-payload-preserved')
        if payload_hashes(descriptor, original['parts']) != original_payload:
            raise Refused('Partition payload changed during GPT growth; filesystem resizing refused')
        unchanged_target(arguments, target)
        # This is a real kernel reread, never --no-reread/--no-tell-kernel.
        fcntl.ioctl(descriptor, BLKRRPART)
        partition = final_gpt['parts'][2]
        device = data_device(target, partition)
        read_ext4(descriptor, partition, pristine=True)
        unchanged_target(arguments, target)
        emit('progress', phase='check-unmounted-data-filesystem')
        run(['e2fsck', '-f', '-p', device], accepted=(0, 1))
        unchanged_target(arguments, target)
        if data_device(target, partition) != device:
            raise Refused('DATA device changed before filesystem resizing')
        emit('progress', phase='resize-unmounted-data-filesystem')
        run(['resize2fs', '-p', device])
        run(['e2fsck', '-f', '-n', device])
        os.sync()
        final = read_data_filesystem(target, partition, device)
        expected_bytes = partition['bytes'] // final['block_bytes'] * final['block_bytes']
        if final['uuid'] != filesystem['uuid'] or final['bytes'] != expected_bytes or final['bytes'] <= filesystem['bytes']:
            raise Refused(f"Filesystem did not expand to the verified DATA partition or its identity changed "
                          f"(observed_bytes={final['bytes']}, expected_bytes={expected_bytes}, "
                          f"uuid_matches={final['uuid'] == filesystem['uuid']})")
        # DATA metadata necessarily changes during resize; EFI and root remain
        # byte-identical, and the old DATA range was checked before resize.
        if payload_hashes(descriptor, original['parts'][:2]) != original_payload[:2]:
            raise Refused('EFI/root payload changed during filesystem resizing')
        newest = current_target(arguments.target, arguments.profile, arguments.expected_target_id)
        if newest != target:
            raise Refused('Target identity changed after filesystem resizing')
        reread = read_gpt(descriptor, target['bytes']);verify_preserved(original, reread, plan['new_data_end_lba'])
        emit('grow-complete', target=target['target'], target_id=target['stable_id'],
             data_partuuid=DATA_UUID, filesystem_uuid=final['uuid'], old_bytes=filesystem['bytes'],
             new_bytes=final['bytes'], data_start_lba=partition['first'], data_end_lba=partition['last'],
             efi_root_payload_preserved=True, old_data_payload_preserved_before_resize=True,
             mounted=False, automatic_restart=False)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--target', required=True)
    parser.add_argument('--profile', required=True, choices=('physical-internal', 'vm-install'))
    parser.add_argument('--expected-target-id', required=True)
    parser.add_argument('--review-token')
    arguments = parser.parse_args(argv)
    try:
        require_guest()
        if arguments.execute:
            execute(arguments)
        else:
            print(json.dumps(inspect_plan(arguments)[3], sort_keys=True), flush=True)
        return 0
    except (OSError, Refused, UnicodeError, subprocess.TimeoutExpired) as error:
        emit('grow-error', message=str(error), target_written=False if arguments.plan else 'inspect last emitted progress phase')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
