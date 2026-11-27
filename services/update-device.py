#!/usr/bin/env python3
"""Offline 403 OS update from live USB; only the existing ESP and root change.

The CLI always requires the booted image, an unmounted identified internal
disk, reviewed geometry and exact confirmation. There is no host override,
regular-file execution option or automatic update. DATA and GPT are never
write destinations. Verified old system partitions are retained on live DATA
and can be restored explicitly if an update is interrupted.
"""
import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys

INSTALLER = Path('/usr/sbin/surface-install-device')
UPDATES = Path('/data/updates')
DATA = Path('/data')
CMDLINE = Path('/proc/cmdline')
LOCK = Path('/run/surface-system-update.lock')
BLOCK_BYTES = 4 * 1024 * 1024
RESERVE = 64 * 1024 * 1024


class Refused(ValueError):
    pass


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


@functools.lru_cache(maxsize=1)
def installer_helper():
    path = INSTALLER
    current = Path('/')
    for component in path.parts[1:]:
        current /= component
        if stat.S_ISLNK(current.lstat().st_mode):
            raise Refused('Installed installer helper must not contain symlinks')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o022 or not os.access(path, os.X_OK):
        raise Refused('The installed installer helper must be executable and owned by root')
    helper = runpy.run_path(str(path), run_name='surface_offline_update_installer')
    required = ('require_guest', 'growth_helper', 'seeding_helper', 'source_info', 'current_target',
                'same_target', 'open_clone_target', 'inventory', 'discover_sources', 'boot_partition_devnums')
    if any(not callable(helper.get(name)) for name in required):
        raise Refused('The installed installer helper has an incompatible interface')
    return helper


def call(helper, name, *args, **kwargs):
    try:
        return helper[name](*args, **kwargs)
    except ValueError as error:
        raise Refused(str(error)) from error


@contextlib.contextmanager
def update_lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077:
            raise Refused('Unsafe system update lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as error:
        raise Refused('Another system update is already running') from error
    finally:
        os.close(fd)


def strict_directory(path, create=False):
    """No-follow root-owned directory traversal; returned FD pins the endpoint."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('An absolute trusted update directory is required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=fd)
                os.fsync(fd)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid or info.st_mode & 0o022:
                raise Refused('Update directories must be owned by root without shared write access')
        return fd
    except BaseException:
        os.close(fd)
        raise


def live_data(installer, target):
    require_persistent_media()
    seed = call(installer, 'seeding_helper')
    call(seed, 'mounted_data')
    fd = strict_directory(DATA)
    try:
        info = os.fstat(fd)
        number = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
        if number in target['family_devnums']:
            raise Refused('The update backup must be on live USB DATA, never the target disk')
        return fd
    except BaseException:
        os.close(fd)
        raise


def require_persistent_media():
    if 'surface.boot=iso' in CMDLINE.read_text().split():
        raise Refused('ISO media supports a fresh installation. To update an existing system, '
                      'boot the USB disk image (.img.xz), which retains the recovery backup '
                      'after shutdown. ISO temporary storage cannot retain that backup.')


def available_space(fd, needed):
    value = os.fstatvfs(fd)
    if value.f_bavail * value.f_frsize < needed + RESERVE:
        raise Refused('Live USB DATA needs enough free space for the old system backup plus 64 MiB')


def open_partition(target, partition):
    """Read-only root probe via a validated sibling partition block node."""
    parent = (Path('/sys/class/block') / target['name']).resolve(strict=True)
    candidates = [node for node in parent.iterdir() if (node / 'partition').is_file()
                  and (node / 'partition').read_text().strip() == str(partition['number'])]
    if len(candidates) != 1:
        raise Refused('The installed root partition has no unique kernel block node')
    node = candidates[0]
    if (node.parent != parent or int((node / 'start').read_text()) != partition['first']
            or int((node / 'size').read_text()) * 512 != partition['bytes']):
        raise Refused('The root partition kernel geometry differs from GPT')
    number = (node / 'dev').read_text().strip()
    if not re.fullmatch(r'\d+:\d+', number):
        raise Refused('Invalid root partition kernel identity')
    fd = os.open(Path('/dev') / node.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != number:
        os.close(fd)
        raise Refused('Opened root partition differs from the reviewed disk')
    return fd


def installed_marker(target, partition):
    debugfs = shutil.which('debugfs')
    if not debugfs:
        raise Refused('The read-only root filesystem inspection utility is missing')
    fd = open_partition(target, partition)
    try:
        common = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
                      pass_fds=(fd,), env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        detail = subprocess.run([debugfs, '-R', 'stat /etc/surface-image-id', f'/proc/self/fd/{fd}'], **common)
        value = detail.stdout
        mode = re.search(r'Type:\s+regular\s+Mode:\s+([0-7]+)', value)
        owner = re.search(r'User:\s+(\d+)\s+Group:\s+\d+\s+(?:Project:\s+\d+\s+)?Size:\s+(\d+)', value)
        if (detail.returncode or not mode or int(mode[1], 8) & 0o022 or not owner
                or int(owner[1]) or not 1 <= int(owner[2]) <= 256):
            raise Refused('The selected disk does not have a trusted 403 OS root marker')
        result = subprocess.run([debugfs, '-R', 'cat /etc/surface-image-id', f'/proc/self/fd/{fd}'], **common)
        marker = result.stdout.strip()
        if result.returncode or not re.fullmatch(r'surface-go-[A-Za-z0-9_.+-]{1,200}', marker):
            raise Refused('The selected disk is not an existing 403 OS installation')
        return marker
    finally:
        os.close(fd)


def inspect_existing(installer, target, require_marker=True):
    grow = call(installer, 'growth_helper')
    strict = call(grow, 'current_target', target['device'], 'physical-internal', target['stable_id'])
    fd = call(grow, 'open_readonly_target', strict)
    try:
        gpt = call(grow, 'read_gpt', fd, target['bytes'])
        if gpt['primary']['backup'] != target['bytes'] // 512 - 1:
            raise Refused('The installed backup GPT must be at the end of the target disk')
        filesystem = call(grow, 'read_ext4', fd, gpt['parts'][2], allow_journal_recovery=True)
        marker = installed_marker(target, gpt['parts'][1]) if require_marker else None
        return dict(gpt=gpt, filesystem=filesystem, marker=marker)
    finally:
        os.close(fd)


def plan_update(target, source, existing, source_gpt, restore=False, backup=None):
    if not target.get('eligible'):
        raise Refused('Unsafe update target: ' + target.get('reason', 'not eligible'))
    if source.get('filesystem_devnum') in target['family_devnums']:
        raise Refused('Update source is stored on the target disk')
    old = existing['gpt']
    for before, after in zip(old['parts'][:2], source_gpt['parts'][:2]):
        if any(before[key] != after[key] for key in ('number', 'type', 'first', 'last', 'bytes', 'attributes', 'name')):
            raise Refused('System partition geometry changed; an in-place update is not supported')
    parts = [dict(number=part['number'], name=part['name'], offset=part['first'] * 512, bytes=part['bytes'])
             for part in old['parts'][:2]]
    size = sum(part['bytes'] for part in parts)
    value = dict(event='plan', operation='restore' if restore else 'update', target=target['device'],
                 target_id=target['stable_id'], target_model=target['model'], target_bytes=target['bytes'],
                 target_devnum=target['devnum'], source=source['path'], source_sha256=source['sha256'],
                 source_bytes=source['bytes'], source_inode=source.get('inode'), source_mtime_ns=source.get('mtime_ns'),
                 confirmation=('RESTORE ' if restore else 'UPDATE ') + target['stable_id'],
                 data_preserved=True, data_partuuid=old['parts'][2]['uuid'],
                 data_filesystem_uuid=existing['filesystem']['uuid'], gpt_sha256=old['fingerprint'],
                 disk_guid=old['disk_guid'], partitions=parts, backup_bytes=size,
                 backup_required_bytes=size + RESERVE, automatic_restart=False,
                 current_version=existing['marker'],
                 warning='Replace system software; games, saves, profiles and settings remain in DATA')
    if backup:
        value['backup'] = str(backup)
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    value['review_token'] = hashlib.sha256(b'surface-offline-system-update-v1\0' + encoded).hexdigest()
    return value


def source_descriptor(installer, source):
    fd = os.open(source['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        check_source(fd, source)
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_source(fd, source):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_ino != source['inode'] or info.st_size != source['bytes']
            or info.st_mtime_ns != source['mtime_ns']
            or f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' != source['filesystem_devnum']):
        raise Refused('Update source changed after review')


def transfer(source, offset, size, phase, destination=None, destination_offset=0, progress_base=0, total=None):
    digest = hashlib.sha256()
    done = 0
    previous = -1
    total = size if total is None else total
    while done < size:
        data = os.pread(source, min(BLOCK_BYTES, size - done), offset + done)
        if not data:
            raise Refused('Unexpected end while reading system partition data')
        digest.update(data)
        if destination is not None:
            written = 0
            while written < len(data):
                count = os.pwrite(destination, data[written:], destination_offset + done + written)
                if count <= 0:
                    raise Refused('System partition write made no progress')
                written += count
        done += len(data)
        percent = (progress_base + done) * 100 // total
        if percent != previous:
            emit('progress', phase=phase, percent=percent, bytes=progress_base + done, total=total)
            previous = percent
    return digest.hexdigest()


def verify_source(fd, source, partitions):
    """Pin partition hashes to the same bytes as the verified whole image.

    Metadata checks cannot detect an in-place rewrite whose timestamp is
    restored. Never derive expected system hashes from a later copy pass.
    """
    full = hashlib.sha256()
    ranges = [(part['offset'], part['offset'] + part['bytes'], hashlib.sha256())
              for part in partitions]
    done, previous = 0, -1
    while done < source['bytes']:
        data = os.pread(fd, min(BLOCK_BYTES, source['bytes'] - done), done)
        if not data:
            raise Refused('Unexpected end during source SHA256 verification')
        full.update(data)
        end = done + len(data)
        for start, stop, digest in ranges:
            left, right = max(start, done), min(stop, end)
            if left < right:
                digest.update(data[left - done:right - done])
        done = end
        percent = done * 100 // source['bytes']
        if percent != previous:
            emit('progress', phase='verify-source', percent=percent, bytes=done, total=source['bytes'])
            previous = percent
    if full.hexdigest() != source['sha256']:
        raise Refused('Source SHA256 verification failed; target was not opened writable')
    return [digest.hexdigest() for _, _, digest in ranges]


def protected_metadata(fd, gpt):
    """Hash GPT, unused prefix, and DATA superblock without copying user data."""
    prefix = os.pread(fd, gpt['parts'][0]['first'] * 512, 0)
    tail_start = gpt['primary']['backup'] - 32
    tail = os.pread(fd, 33 * 512, tail_start * 512)
    data = gpt['parts'][2]
    superblock = os.pread(fd, 1024, data['first'] * 512 + 1024)
    if len(prefix) != gpt['parts'][0]['first'] * 512 or len(tail) != 33 * 512 or len(superblock) != 1024:
        raise Refused('Could not read complete protected target metadata')
    return hashlib.sha256(prefix + tail + superblock).hexdigest()


def verify_opened_layout(grow, fd, target, existing):
    actual = call(grow, 'read_gpt', fd, target['bytes'])
    filesystem = call(grow, 'read_ext4', fd, actual['parts'][2], allow_journal_recovery=True)
    if actual['fingerprint'] != existing['gpt']['fingerprint'] or filesystem != existing['filesystem']:
        raise Refused('Opened target GPT or DATA filesystem changed since review')


def backup_system(target_fd, plan):
    directory = strict_directory(UPDATES, create=True)
    try:
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise Refused('The update backup directory must have root-only 0700 permissions')
        available_space(directory, plan['backup_bytes'])
        # tempfile chooses a new exclusive directory; the already validated
        # parent is held through /proc, preventing a pathname replacement.
        name = 'update-' + os.urandom(12).hex()
        os.mkdir(name, 0o700, dir_fd=directory)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        path = UPDATES / name
        try:
            manifest = dict(format='surface-system-backup-v1', target_id=plan['target_id'],
                            target_bytes=plan['target_bytes'], gpt_sha256=plan['gpt_sha256'],
                            disk_guid=plan['disk_guid'], source_sha256=plan['source_sha256'], parts=[])
            done = 0
            for part in plan['partitions']:
                filename = str(part['number']) + '-' + part['name'] + '.img'
                fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=child)
                try:
                    checksum = transfer(target_fd, part['offset'], part['bytes'], 'backup-system', fd,
                                        progress_base=done, total=plan['backup_bytes'])
                    os.fsync(fd)
                    if transfer(fd, 0, part['bytes'], 'verify-backup', progress_base=done,
                                total=plan['backup_bytes']) != checksum:
                        raise Refused('Old system backup readback failed; target was not opened writable')
                finally:
                    os.close(fd)
                manifest['parts'].append(dict(part, file=filename, sha256=checksum))
                done += part['bytes']
            fd = os.open('manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=child)
            with os.fdopen(fd, 'w') as stream:
                json.dump(manifest, stream, sort_keys=True)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.fsync(child); os.fsync(directory)
            return path, manifest
        finally:
            os.close(child)
    finally:
        os.close(directory)


def selected_plan(installer, args, boot):
    target = call(installer, 'current_target', args.target, args.expected_target_id, boot)
    source = call(installer, 'source_info', args.source)
    if args.expected_source_sha256 and source['sha256'] != args.expected_source_sha256.lower():
        raise Refused('Expected update SHA256 differs from the staged checksum')
    existing = inspect_existing(installer, target)
    fd = source_descriptor(installer, source)
    try:
        grow = call(installer, 'growth_helper')
        source_gpt = call(grow, 'read_gpt', fd, source['bytes'])
        check_source(fd, source)
    finally:
        os.close(fd)
    data_fd = live_data(installer, target)
    try:
        plan = plan_update(target, source, existing, source_gpt)
        available_space(data_fd, plan['backup_bytes'])
    finally:
        os.close(data_fd)
    return target, source, existing, plan


def execute_update(installer, args):
    if not all((args.target, args.source, args.expected_target_id, args.expected_source_sha256, args.review_token)):
        raise Refused('Update requires reviewed target, source SHA256 and review token')
    if args.confirm != 'UPDATE ' + args.expected_target_id:
        raise Refused('Exact typed UPDATE <stable-id> confirmation is required')
    boot = call(installer, 'boot_partition_devnums')
    target, source, existing, plan = selected_plan(installer, args, boot)
    if plan['review_token'] != args.review_token:
        raise Refused('Update plan changed since review; review the target and source again')
    source_fd = source_descriptor(installer, source)
    target_fd = None
    backup = None
    try:
        expected = verify_source(source_fd, source, plan['partitions'])
        check_source(source_fd, source)
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        current = inspect_existing(installer, target)
        if (current['gpt']['fingerprint'] != existing['gpt']['fingerprint']
                or current['filesystem'] != existing['filesystem'] or current['marker'] != existing['marker']):
            raise Refused('Installed system changed since review')
        grow = call(installer, 'growth_helper')
        strict = call(grow, 'current_target', target['device'], 'physical-internal', target['stable_id'])
        old_fd = call(grow, 'open_readonly_target', strict)
        try:
            verify_opened_layout(grow, old_fd, target, existing)
            protection = protected_metadata(old_fd, existing['gpt'])
            backup, _ = backup_system(old_fd, plan)
        finally:
            os.close(old_fd)
        emit('backup-ready', backup=str(backup), verified=True, bytes=plan['backup_bytes'])
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        check_source(source_fd, source)
        target_fd = call(installer, 'open_clone_target', args.target, target)
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        verify_opened_layout(grow, target_fd, target, existing)
        if protected_metadata(target_fd, existing['gpt']) != protection:
            raise Refused('Target protected metadata changed before the update')
        done = 0
        for part, checksum in zip(plan['partitions'], expected):
            copied = transfer(source_fd, part['offset'], part['bytes'], 'write-system', target_fd,
                              part['offset'], done, plan['backup_bytes'])
            if copied != checksum:
                raise Refused('Source system partition changed after verification; restore the retained backup before booting')
            done += part['bytes']
        os.fsync(target_fd)
        check_source(source_fd, source)
        done = 0
        for part, checksum in zip(plan['partitions'], expected):
            if transfer(target_fd, part['offset'], part['bytes'], 'verify-system', progress_base=done,
                        total=plan['backup_bytes']) != checksum:
                raise Refused('Updated system readback failed; restore the retained backup before booting')
            done += part['bytes']
        if protected_metadata(target_fd, existing['gpt']) != protection:
            raise Refused('Protected target metadata changed during the update')
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        emit('complete', operation='update', target=args.target, target_id=args.expected_target_id,
             source_sha256=source['sha256'], verified=True, data_untouched=True, gpt_untouched=True,
             backup=str(backup), gpt_unchanged=True, automatic_restart=False,
             message='System update verified. Games, saves, profiles and settings are retained. Power off, remove the USB and start the device.')
    except BaseException:
        if backup is not None:
            emit('recovery', backup=str(backup), message='A verified old system backup remains on the USB. Use explicit restore if the update did not complete.')
        raise
    finally:
        os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)


def read_backup(path):
    path = Path(path)
    if path.parent != UPDATES or not re.fullmatch(r'update-[0-9a-f]{24}', path.name):
        raise Refused('Restore requires a retained backup directly under /data/updates')
    directory = strict_directory(path)
    try:
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise Refused('Restore backup directory must have root-only 0700 permissions')
        fd = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        with os.fdopen(fd, 'r') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077 or info.st_size > 16384:
                raise Refused('Unsafe update backup manifest')
            value = json.load(stream)
        if (not isinstance(value, dict) or value.get('format') != 'surface-system-backup-v1'
                or not isinstance(value.get('parts'), list) or len(value['parts']) != 2
                or not all(isinstance(part, dict) for part in value['parts'])):
            raise Refused('Unsupported update backup manifest')
        return directory, value
    except BaseException:
        os.close(directory)
        raise


def restore_update(installer, args):
    require_persistent_media()
    if not args.target or not args.expected_target_id or not args.backup or args.confirm != 'RESTORE ' + args.expected_target_id:
        raise Refused('Restore requires explicit target identity, backup and exact RESTORE <stable-id> confirmation')
    boot = call(installer, 'boot_partition_devnums')
    target = call(installer, 'current_target', args.target, args.expected_target_id, boot)
    existing = inspect_existing(installer, target, require_marker=False)
    directory, backup = read_backup(args.backup)
    sources = []
    target_fd = None
    try:
        if (backup.get('target_id') != target['stable_id'] or backup.get('target_bytes') != target['bytes']
                or backup.get('gpt_sha256') != existing['gpt']['fingerprint'] or backup.get('disk_guid') != existing['gpt']['disk_guid']):
            raise Refused('Backup target identity, capacity or GPT differs from the existing disk')
        done = 0
        total = sum(part['bytes'] for part in existing['gpt']['parts'][:2])
        for part, entry in zip(existing['gpt']['parts'][:2], backup['parts']):
            expected = dict(number=part['number'], name=part['name'], offset=part['first'] * 512, bytes=part['bytes'],
                            file=str(part['number']) + '-' + part['name'] + '.img')
            if any(entry.get(key) != value for key, value in expected.items()) or not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')):
                raise Refused('Backup partition geometry or checksum is invalid')
            fd = os.open(entry['file'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            sources.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077 or info.st_size != part['bytes']:
                raise Refused('Backup partition file ownership, permissions or size is unsafe')
            if transfer(fd, 0, part['bytes'], 'verify-backup', progress_base=done, total=total) != entry['sha256']:
                raise Refused('Backup checksum verification failed; target was not opened writable')
            done += part['bytes']
        data_fd = live_data(installer, target)
        os.close(data_fd)
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        grow = call(installer, 'growth_helper')
        strict = call(grow, 'current_target', target['device'], 'physical-internal', target['stable_id'])
        old_fd = call(grow, 'open_readonly_target', strict)
        try:
            verify_opened_layout(grow, old_fd, target, existing)
            protection = protected_metadata(old_fd, existing['gpt'])
        finally:
            os.close(old_fd)
        target_fd = call(installer, 'open_clone_target', args.target, target)
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        verify_opened_layout(grow, target_fd, target, existing)
        if protected_metadata(target_fd, existing['gpt']) != protection:
            raise Refused('Target protected metadata changed before restore')
        done = 0
        for fd, entry in zip(sources, backup['parts']):
            checksum = transfer(fd, 0, entry['bytes'], 'restore-system', target_fd, entry['offset'], done, total)
            if checksum != entry['sha256']:
                raise Refused('Backup changed during restore; restore is incomplete')
            done += entry['bytes']
        os.fsync(target_fd)
        done = 0
        for entry in backup['parts']:
            if transfer(target_fd, entry['offset'], entry['bytes'], 'verify-system', progress_base=done, total=total) != entry['sha256']:
                raise Refused('Restored system readback failed')
            done += entry['bytes']
        if protected_metadata(target_fd, existing['gpt']) != protection:
            raise Refused('Protected target metadata changed during restore')
        call(installer, 'same_target', target, call(installer, 'current_target', args.target, args.expected_target_id, boot))
        emit('complete', operation='restore', target=args.target, target_id=args.expected_target_id, verified=True,
             data_untouched=True, gpt_untouched=True, gpt_unchanged=True, backup=str(args.backup), automatic_restart=False,
             message='Previous system restored and verified. Games, saves, profiles and settings remain in DATA.')
    finally:
        for fd in sources:
            os.close(fd)
        if target_fd is not None:
            os.close(target_fd)
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('list', 'plan', 'execute', 'restore'):
        mode.add_argument('--' + name, action='store_true')
    for name in ('target', 'source', 'expected-target-id', 'expected-source-sha256', 'review-token', 'confirm', 'backup'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    try:
        installer = installer_helper()
        call(installer, 'require_guest')  # Before probing, directory creation or writes.
        if args.list:
            boot = call(installer, 'boot_partition_devnums')
            disks = call(installer, 'inventory', boot)
            sources, errors = call(installer, 'discover_sources', disks)
            targets = call(installer, 'inventory', boot)
            for target in targets:
                if target['eligible']:
                    try:
                        inspected = inspect_existing(installer, target)
                        target['current_version'] = inspected['marker']
                        target['data_preserved'] = True
                    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                        target.update(eligible=False, reason=str(error), reasons=[str(error)])
            emit('list', targets=targets, sources=sources, errors=errors, operation='update')
        elif args.plan:
            if not args.target or not args.source or not args.expected_target_id:
                raise Refused('Update planning requires an explicit target identity and source')
            _, _, _, plan = selected_plan(installer, args, call(installer, 'boot_partition_devnums'))
            print(json.dumps(plan, ensure_ascii=False), flush=True)
        else:
            with update_lock():
                if args.restore:
                    restore_update(installer, args)
                else:
                    execute_update(installer, args)
        return 0
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as error:
        emit('error', message=str(error), target_written=False if args.list or args.plan else 'unknown; inspect progress and recovery event')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
