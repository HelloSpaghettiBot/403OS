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
# DATA stays unmounted throughout. No growth runs during later installed boots.
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
SOURCE, TARGET = '/dev/vda', '/dev/vdb'
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

    def source_identity():
        node = Path('/sys/class/block/vda')
        if (node / 'serial').read_text().strip() != 'SURFACE_MEDIA':
            raise ValueError('Installer media serial changed')
        if (node / 'queue/logical_block_size').read_text().strip() != '512':
            raise ValueError('Installer media must have 512-byte sectors')
        grow['no_symlinks'](Path(SOURCE))
        info = Path(SOURCE).lstat()
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError('Installer media is not a direct block device')
        devnum = f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        if (node / 'dev').read_text().strip() != devnum:
            raise ValueError('Installer media kernel identity changed')
        size = int((node / 'size').read_text().strip()) * 512
        if not 0 < size < TARGET_BYTES:
            raise ValueError('Invalid installer media size')
        return info.st_rdev, size

    source = source_identity()
    unchanged()
    emit('SURFACE_VM_INSTALL_BEGIN target=/dev/vdb serial=SURFACE_TEST_TARGET')
    subprocess.run(['dd', 'if=' + SOURCE, 'of=' + TARGET, 'bs=4M', 'conv=fsync'],
                   stdin=subprocess.DEVNULL, timeout=600, check=True)
    unchanged()
    if source_identity() != source:
        raise ValueError('Installer media changed while cloning')
    emit('SURFACE_VM_CLONE_VERIFY_BEGIN')
    source_fd = target_fd = None
    try:
        source_fd = os.open(SOURCE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        target_fd = grow['open_readonly_target'](reviewed)
        digest = hashlib.sha256()
        offset = 0
        while offset < source[1]:
            count = min(4 * 1024 * 1024, source[1] - offset)
            original = os.pread(source_fd, count, offset)
            copied = os.pread(target_fd, count, offset)
            if len(original) != count or copied != original:
                raise ValueError('Whole-image clone readback failed; DATA growth refused')
            digest.update(original)
            offset += count
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)
    unchanged()
    if source_identity() != source:
        raise ValueError('Installer media changed during readback')
    emit(f'SURFACE_VM_CLONE_VERIFIED bytes={source[1]} sha256={digest.hexdigest()}')

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
