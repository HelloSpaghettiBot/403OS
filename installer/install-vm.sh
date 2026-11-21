#!/bin/sh
# Deliberately restricted to a disposable QEMU target with an explicit serial.
# This is NOT a hardware installer. No automatic real-disk erase path exists.
set -eu
test "$#" -eq 1 && test "${1:-}" = --confirm-disposable-vm || { echo 'Explicit VM confirmation required'; exit 2; }
grep -Eq '(^|[[:space:]])surface\.vm-install=1([[:space:]]|$)' /proc/cmdline || { echo 'Missing VM boot guard'; exit 2; }
test "$(cat /sys/class/dmi/id/product_name)" = 'Surface Go OS Disposable Test VM' || { echo 'Wrong disposable VM identity'; exit 2; }
test "$(cat /sys/class/block/vdb/serial)" = 'SURFACE_TEST_TARGET' || { echo 'Wrong disposable target serial'; exit 2; }
test "$(cat /sys/class/block/vda/serial)" = 'SURFACE_MEDIA' || { echo 'Wrong installer media serial'; exit 2; }
test "$(cat /sys/class/block/vdb/size)" -eq 134217728 || { echo 'Wrong disposable target capacity'; exit 2; }
test -x /usr/sbin/surface-grow-data || { echo 'DATA growth helper is missing'; exit 2; }
# Python performs read-only device-number/mount/holder/swap checks before dd,
# verifies the whole clone, and consumes the guarded helper's internal review.
# Target DATA stays unmounted throughout. Installer media DATA is mounted read-only.
# No growth runs during later installed boots.
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys

HELPER = '/usr/sbin/surface-grow-data'
MEDIA, MEDIA_DATA, TARGET = '/dev/vda', '/dev/vda3', '/dev/vdb'
MOUNTPOINT = Path('/run/surface-install-media')
SOURCE = MOUNTPOINT / 'SurfaceGoGamingOS/surface-go.img'
TARGET_ID, TARGET_BYTES = 'SURFACE_TEST_TARGET', 64 * 1024 ** 3

def emit(message):
    print(message, flush=True)

def parse_json(value):
    if len(value) > 65536:
        raise ValueError('Grow helper response is too large')
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError('Grow helper did not return a JSON object')
    return parsed

try:
    grow = runpy.run_path(HELPER, run_name='surface_grow_installer_library')
    grow['require_guest']()
    reviewed = grow['current_target'](TARGET, 'vm-install', TARGET_ID)
    if reviewed['bytes'] != TARGET_BYTES:
        raise ValueError('Disposable target capacity changed')

    def unchanged():
        if grow['current_target'](TARGET, 'vm-install', TARGET_ID) != reviewed:
            raise ValueError('Target identity, capacity or mount state changed')

    def media_identity():
        node = Path('/sys/class/block/vda')
        if (node / 'serial').read_text().strip() != 'SURFACE_MEDIA':
            raise ValueError('Installer media serial changed')
        if (node / 'queue/logical_block_size').read_text().strip() != '512' or (node / 'ro').read_text().strip() != '1':
            raise ValueError('Installer media requires read-only 512-byte block geometry')
        grow['no_symlinks'](Path(MEDIA))
        info = Path(MEDIA).lstat()
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError('Installer media is not a direct block device')
        devnum = f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        if (node / 'dev').read_text().strip() != devnum:
            raise ValueError('Installer media kernel identity changed')
        size = int((node / 'size').read_text().strip()) * 512
        if not 0 < size < TARGET_BYTES:
            raise ValueError('Invalid installer media size')
        return dict(target=MEDIA, devnum=devnum, bytes=size)

    def mount_record():
        matches=[]
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            columns=line.split()
            if len(columns)<10 or '-' not in columns:continue
            point=re.sub(r'\\([0-7]{3})',lambda match:chr(int(match[1],8)),columns[4])
            if point==str(MOUNTPOINT):matches.append(columns)
        if len(matches)>1:raise ValueError('Installer mountpoint has ambiguous mount records')
        return matches[0] if matches else None

    media=media_identity()
    media_fd=grow['open_readonly_target'](media)
    try:outer=grow['read_gpt'](media_fd,media['bytes'])
    finally:os.close(media_fd)
    media_partition=outer['parts'][2]
    partition_node=Path('/sys/class/block/vda3')
    def media_partition_identity():
        grow['no_symlinks'](Path(MEDIA_DATA))
        info=Path(MEDIA_DATA).lstat()
        if not stat.S_ISBLK(info.st_mode) or partition_node.resolve(strict=True).parent!=Path('/sys/class/block/vda').resolve(strict=True):
            raise ValueError('Installer DATA is not a partition of the serialized media')
        number=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        if ((partition_node/'partition').read_text().strip()!='3' or
            (partition_node/'dev').read_text().strip()!=number or
            (partition_node/'start').read_text().strip()!=str(media_partition['first']) or
            (partition_node/'size').read_text().strip()!=str(media_partition['bytes']//512)):
            raise ValueError('Installer DATA kernel identity or geometry changed')
        return info.st_rdev
    data_rdev=media_partition_identity()
    if mount_record() is not None:raise ValueError('Installer media mountpoint was already mounted')
    if MOUNTPOINT.exists():
        grow['no_symlinks'](MOUNTPOINT)
        if not MOUNTPOINT.is_dir() or any(MOUNTPOINT.iterdir()):raise ValueError('Installer media mountpoint is not an empty directory')
    else:MOUNTPOINT.mkdir(mode=0o700)
    mounted=False;source_fd=target_fd=None
    try:
        subprocess.run(['mount','-t','ext4','-o','ro,noload,nosuid,nodev,noexec',MEDIA_DATA,str(MOUNTPOINT)],
                       stdin=subprocess.DEVNULL,timeout=20,check=True)
        mounted=True
        def source_mount_unchanged():
            if media_identity()!=media or media_partition_identity()!=data_rdev:
                raise ValueError('Installer media changed during payload verification')
            columns=mount_record()
            if columns is None:raise ValueError('Installer media mount disappeared')
            split=columns.index('-')
            if (columns[2]!=f'{os.major(data_rdev)}:{os.minor(data_rdev)}' or columns[3]!='/' or
                not {'ro','nosuid','nodev','noexec'}<=set(columns[5].split(',')) or
                columns[split+1]!='ext4' or 'ro' not in columns[split+3].split(',')):
                raise ValueError('Installer DATA mount is not the exact read-only restricted source')
        source_mount_unchanged()
        def regular_source(path,bound=None):
            grow['no_symlinks'](path)
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or (bound is not None and info.st_size>bound):
                raise ValueError('Packaged installer payload must be a bounded root-owned regular file without shared write access')
            return info
        def metadata(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        initial=regular_source(SOURCE)
        if initial.st_dev!=data_rdev or not 0<initial.st_size<=media['bytes']:
            raise ValueError('Packaged payload is not stored on installer DATA or exceeds the media')
        source_fd=os.open(SOURCE,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        if metadata(os.fstat(source_fd))!=metadata(initial) or not os.fstatvfs(source_fd).f_flag&os.ST_RDONLY:
            raise ValueError('Packaged source changed while opening or its filesystem is writable')
        checksum=Path(str(SOURCE)+'.sha256');checksum_info=regular_source(checksum,4096)
        checksum_fd=os.open(checksum,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        with os.fdopen(checksum_fd,'rb') as manifest:
            if metadata(os.fstat(manifest.fileno()))!=metadata(checksum_info):raise ValueError('Payload checksum changed while opening')
            raw=manifest.read(4097)
            if len(raw)!=checksum_info.st_size or metadata(os.fstat(manifest.fileno()))!=metadata(checksum_info):
                raise ValueError('Payload checksum changed during reading')
        match=re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?surface-go\.img[ \t]*\n?',raw.decode('ascii'))
        if not match:raise ValueError('Packaged checksum must identify surface-go.img')
        expected_sha=match[1].lower()
        payload_gpt=grow['read_gpt'](source_fd,initial.st_size)
        grow['read_ext4'](source_fd,payload_gpt['parts'][2],pristine=True)
        digest=hashlib.sha256();offset=0
        while offset<initial.st_size:
            block=os.pread(source_fd,min(4*1024*1024,initial.st_size-offset),offset)
            if not block:raise ValueError('Packaged source ended during checksum verification')
            digest.update(block);offset+=len(block)
        def source_unchanged():
            source_mount_unchanged()
            if metadata(os.fstat(source_fd))!=metadata(initial) or metadata(regular_source(SOURCE))!=metadata(initial):
                raise ValueError('Packaged source metadata changed during installation')
            if metadata(regular_source(checksum,4096))!=metadata(checksum_info):
                raise ValueError('Packaged checksum metadata changed during installation')
        source_unchanged()
        if digest.hexdigest()!=expected_sha:raise ValueError('Packaged source SHA256 does not match its checksum; target remains untouched')
        emit(f'SURFACE_VM_EMBEDDED_PAYLOAD_VERIFIED sha256={expected_sha} bytes={initial.st_size}')
        unchanged()
        emit('SURFACE_VM_INSTALL_BEGIN target=/dev/vdb serial=SURFACE_TEST_TARGET')
        # The inherited read-only descriptor binds dd to the verified inode.
        subprocess.run(['dd','if=/proc/self/fd/'+str(source_fd),'of='+TARGET,'bs=4M','conv=fsync'],
                       stdin=subprocess.DEVNULL,pass_fds=(source_fd,),timeout=600,check=True)
        unchanged();source_unchanged()
        emit('SURFACE_VM_CLONE_VERIFY_BEGIN')
        target_fd=grow['open_readonly_target'](reviewed)
        digest=hashlib.sha256();offset=0
        while offset<initial.st_size:
            count=min(4*1024*1024,initial.st_size-offset)
            original=os.pread(source_fd,count,offset);copied=os.pread(target_fd,count,offset)
            if len(original)!=count or copied!=original:raise ValueError('Whole-image clone readback failed; DATA growth refused')
            digest.update(original);offset+=count
        unchanged();source_unchanged()
        if digest.hexdigest()!=expected_sha:raise ValueError('Packaged source SHA256 changed during readback')
        emit(f'SURFACE_VM_CLONE_VERIFIED bytes={initial.st_size} sha256={digest.hexdigest()}')
    finally:
        if source_fd is not None:os.close(source_fd)
        if target_fd is not None:os.close(target_fd)
        if mounted:
            subprocess.run(['umount',str(MOUNTPOINT)],stdin=subprocess.DEVNULL,timeout=20,check=True)

    common = ['--target', TARGET, '--profile', 'vm-install', '--expected-target-id', TARGET_ID]
    planned = subprocess.run([HELPER, '--plan', *common], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=60, check=False)
    if planned.returncode != 0:
        emit((planned.stdout + planned.stderr).strip()[-8192:])
        raise ValueError(f'Guarded DATA planning failed with exit {planned.returncode}')
    plan = parse_json(planned.stdout)
    token = plan.get('review_token', '')
    if (plan.get('event') != 'plan' or plan.get('profile') != 'vm-install' or
        plan.get('target') != TARGET or plan.get('target_id') != TARGET_ID or
        plan.get('target_bytes') != TARGET_BYTES or plan.get('data_partition') != 3 or
        plan.get('data_partuuid') != '8b4a530e-d6d4-4e69-9307-310000000002' or
        plan.get('old_data_bytes') != 4 * 1024 ** 3 or
        plan.get('new_data_end_lba') != TARGET_BYTES // 512 - 34 or
        type(plan.get('new_data_bytes')) is not int or plan['new_data_bytes'] <= 6 * 1024 ** 3 or
        plan.get('partition_starts_and_uuids_preserved') is not True or
        plan.get('unused_space_only') is not True or plan.get('requires_unmounted') is not True or
        plan.get('automatic_on_boot') is not False or not isinstance(token, str) or
        not re.fullmatch(r'[0-9a-f]{64}', token)):
        raise ValueError('DATA growth plan does not match this disposable installation')
    emit('SURFACE_VM_DATA_GROW_PLAN ' + json.dumps(plan, sort_keys=True))
    unchanged()
    executed = subprocess.run([HELPER, '--execute', *common, '--review-token', token],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=900, check=False)
    emit(executed.stdout.rstrip())
    if executed.returncode != 0:
        raise ValueError(f'Guarded DATA growth failed with exit {executed.returncode}')
    lines = [line for line in executed.stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError('DATA growth returned no verified completion receipt')
    complete = parse_json(lines[-1])
    new_bytes = complete.get('new_bytes')
    if (complete.get('event') != 'grow-complete' or complete.get('target') != TARGET or
        complete.get('target_id') != TARGET_ID or complete.get('data_partuuid') != plan['data_partuuid'] or
        complete.get('filesystem_uuid') != plan.get('filesystem_uuid') or
        complete.get('old_bytes') != plan['old_data_bytes'] or
        type(new_bytes) is not int or new_bytes <= 6 * 1024 ** 3 or
        not 0 <= plan['new_data_bytes'] - new_bytes < 65536 or
        complete.get('data_start_lba') != plan.get('data_start_lba') or
        complete.get('data_end_lba') != plan['new_data_end_lba'] or
        complete.get('efi_root_payload_preserved') is not True or
        complete.get('old_data_payload_preserved_before_resize') is not True or
        complete.get('mounted') is not False):
        raise ValueError('DATA growth completion receipt is missing or inconsistent')
    unchanged()
    emit(f'SURFACE_VM_DATA_GROW_PASS bytes={new_bytes} partuuid={plan["data_partuuid"]}')
    emit('SURFACE_VM_INSTALL_PASS')
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    emit('SURFACE_VM_INSTALL_FAIL ' + str(error))
    sys.exit(2)
PY
sync
poweroff
