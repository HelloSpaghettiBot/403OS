#!/usr/bin/env python3
"""Preview or explicitly flash one reviewed external USB; never an internal disk.

Target identities belong in an ignored private plan, not in public source.
Preview never opens a block device. Only --execute can unmount/open/write it.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = Path('/run/media/system/Files')
CHUNK = 4 * 1024**2
SECTOR = 512
TAIL = 33 * SECTOR
SPEC = importlib.util.spec_from_file_location('surface_gpt', Path(__file__).with_name('prepare-usb.py'))
gpt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gpt)
REQUIRED_BOOT_CHECKS = ('uefi_graphical_boot', 'root_and_persistent_data_mount',
                        'sdl_keyboard_gamepad_injection', 'qmp_keyboard_focus_and_framebuffer',
                        'ui_clean_poweroff', 'embedded_installer_payload')


class FlashError(Exception):
    pass


def sha256(fd, size, progress=None):
    value = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(CHUNK, size-offset), offset)
        if not chunk:
            raise FlashError('Short read while hashing image bytes.')
        value.update(chunk)
        offset += len(chunk)
        if progress:
            progress(offset)
    return value.hexdigest()


def regular(path):
    path = Path(path)
    if path.is_symlink():
        raise FlashError('Plan, evidence and image must not be symlinks.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise FlashError('Expected a regular plan, evidence or image file.')
    return fd


def load_json(path):
    fd = regular(path)
    try:
        raw = os.read(fd, 1024*1024+1)
    finally:
        os.close(fd)
    if len(raw) > 1024*1024:
        raise FlashError('JSON exceeds the review limit.')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def validate_plan(plan):
    if plan.get('schema') != 'surface-full-usb-flash-v1':
        raise FlashError('Unsupported private plan schema.')
    expected = plan.get('expected', {})
    for key in ('model', 'serial'):
        if (not isinstance(expected.get(key), str) or not expected[key].strip()
                or len(expected[key]) > 128 or any(ord(c) < 32 for c in expected[key])):
            raise FlashError('A reviewed model and stable serial are required.')
    size = expected.get('bytes')
    if type(size) is not int or size < 64*1024**2 or size % SECTOR:
        raise FlashError('Expected capacity must be exact positive 512-byte geometry.')
    if not re.fullmatch(r'[0-9a-f]{64}', str(plan.get('image_sha256', ''))):
        raise FlashError('Plan requires the exact compiled image SHA-256.')
    device = Path(plan.get('device', ''))
    if device.parent != Path('/dev/disk/by-id') or not device.name.startswith('usb-'):
        raise FlashError('Target must be a stable USB by-id whole-device path.')
    if re.search(r'-part[0-9]+$', device.name):
        raise FlashError('A partition cannot be the flash target.')
    image = Path(plan.get('image', ''))
    if not image.is_absolute() or not image.resolve().is_relative_to(FILES):
        raise FlashError('Image must be on the mounted FILES drive.')
    return expected


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=30).stdout


def tree_records(disk):
    yield disk
    for child in disk.get('children', []):
        yield from tree_records(child)


def validate_disk(disk, expected, resolved):
    if (disk.get('type') != 'disk' or disk.get('tran') != 'usb' or disk.get('ro')
            or disk.get('log-sec') != SECTOR or disk.get('size') != expected['bytes']
            or str(disk.get('model') or '').strip() != expected['model']
            or str(disk.get('serial') or '').strip() != expected['serial']
            or disk.get('path') != str(resolved)):
        raise FlashError('Whole writable USB model/serial/capacity/sector identity did not match.')
    if any(record.get('type') not in ('disk', 'part') for record in tree_records(disk)):
        raise FlashError('Mapped/stacked descendants are not supported for destructive USB preparation.')
    for record in tree_records(disk):
        if any(m in ('/', '/boot', '/boot/efi', '/usr', str(FILES)) for m in record.get('mountpoints') or []):
            raise FlashError('Target contains protected host storage.')
    return disk


def inspect(plan):
    expected = validate_plan(plan)
    target = Path(plan['device']).resolve(strict=True)
    info = target.stat()
    if not target.is_relative_to('/dev') or not stat.S_ISBLK(info.st_mode):
        raise FlashError('The reviewed stable target is not a block device.')
    disk_name = target.name
    sys_device = (Path('/sys/class/block')/disk_name/'device').resolve(strict=True)
    if not any(re.fullmatch(r'usb[0-9]+', part) for part in sys_device.parts):
        raise FlashError('Kernel ancestry does not identify a USB device.')
    records = json.loads(command(['lsblk', '--json', '--tree', '--bytes', '-o',
        'PATH,MODEL,SERIAL,SIZE,TYPE,TRAN,LOG-SEC,RO,MAJ:MIN,MOUNTPOINTS', str(target)]))['blockdevices']
    if len(records) != 1:
        raise FlashError('Expected one whole-disk metadata record.')
    disk = validate_disk(records[0], expected, target)
    devices = {record['maj:min'] for record in tree_records(disk)}
    # A source file on any target filesystem would be destroyed by this operation.
    source = Path(plan['image']).stat()
    if f'{os.major(source.st_dev)}:{os.minor(source.st_dev)}' in devices:
        raise FlashError('Source image is on the target USB.')
    for line in Path('/proc/swaps').read_text().splitlines()[1:]:
        swap = line.split()[0]
        try:
            swap_info = os.stat(swap)
        except OSError as error:
            raise FlashError('Cannot identify an active swap source.') from error
        swap_dev = swap_info.st_rdev if stat.S_ISBLK(swap_info.st_mode) else swap_info.st_dev
        if f'{os.major(swap_dev)}:{os.minor(swap_dev)}' in devices:
            raise FlashError('Target or descendant is active swap.')
    return disk, (info.st_rdev, info.st_ino)


def require_unmounted(disk):
    devices = {record['maj:min'] for record in tree_records(disk)}
    if any(any(record.get('mountpoints') or []) for record in tree_records(disk)):
        raise FlashError('A target filesystem remains mounted.')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        if len(fields) > 2 and fields[2] in devices:
            raise FlashError('Target remains mounted according to mountinfo.')


def check_boot(path, image_sha):
    result, result_sha = load_json(path)
    checks = result.get('checks', {})
    if (result.get('image_sha256') != image_sha or result.get('mode') != 'live'
            or result.get('direct_kernel_boot') is not False
            or result.get('status') not in ('PASS', 'PARTIAL')
            or any(checks.get(name) != 'PASS' for name in REQUIRED_BOOT_CHECKS)
            or 'FAIL' in checks.values()):
        raise FlashError('Matching completed live UEFI boot/input/clean-power evidence is required.')
    return result_sha


def udisks_object(path):
    # UDisks encodes bytes outside ASCII letters/digits as _xx.
    name = ''.join(c if c.isascii() and c.isalnum() else f'_{ord(c):02x}' for c in Path(path).name)
    return '/org/freedesktop/UDisks2/block_devices/' + name


class UDisks:
    def __init__(self, interactive=False):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.options = {'auth.no_user_interaction': dbus.Boolean(not interactive)}

    def interface(self, path, interface):
        obj = self.bus.get_object('org.freedesktop.UDisks2', udisks_object(path))
        return self.dbus.Interface(obj, interface)

    def unmount(self, path):
        self.interface(path, 'org.freedesktop.UDisks2.Filesystem').Unmount(self.options, timeout=60)

    def open_exclusive(self, path):
        options = dict(self.options)
        options['flags'] = self.dbus.Int32(os.O_EXCL | os.O_SYNC | os.O_CLOEXEC)
        # Desktop authentication can take longer than the device-open itself.
        # Identity and capacity are checked again after this returns a handle.
        return self.interface(path, 'org.freedesktop.UDisks2.Block').OpenDevice('rw', options, timeout=600).take()


def validate_fd(fd, disk):
    info = os.fstat(fd)
    expected_dev = tuple(map(int, disk['maj:min'].split(':')))
    if not stat.S_ISBLK(info.st_mode) or (os.major(info.st_rdev), os.minor(info.st_rdev)) != expected_dev:
        raise FlashError('Writable descriptor is not the reviewed whole USB.')
    size = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
    sector = struct.unpack('I', fcntl.ioctl(fd, 0x1268, bytes(4)))[0]
    if size != disk['size'] or sector != SECTOR:
        raise FlashError('Descriptor capacity/sector geometry changed.')
    if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR:
        raise FlashError('Read/write descriptor is required for readback verification.')


def source_stamp(fd):
    value = os.fstat(fd)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def pwrite_all(fd, data, offset):
    while data:
        count = os.pwrite(fd, data, offset)
        if count <= 0:
            raise FlashError('Short USB write.')
        data = data[count:]
        offset += count


def copy_and_verify(source_fd, target_fd, source_size, target_size, expected_sha, progress=None):
    if source_size > target_size or (source_size < target_size and source_size > target_size-TAIL):
        raise FlashError('Image cannot fit alongside obsolete end-GPT clearing range.')
    if progress:
        progress('write_start', 0)
    offset = 0
    while offset < source_size:
        value = os.pread(source_fd, min(CHUNK, source_size-offset), offset)
        if not value:
            raise FlashError('Source became shorter during USB copy.')
        pwrite_all(target_fd, value, offset)
        offset += len(value)
        if progress:
            progress('write', offset)
    if source_size < target_size:
        pwrite_all(target_fd, bytes(TAIL), target_size-TAIL)
    os.fsync(target_fd)
    if progress:
        progress('write_complete', source_size)
    if hasattr(os, 'posix_fadvise'):
        os.posix_fadvise(target_fd, 0, 0, os.POSIX_FADV_DONTNEED)
    if progress:
        progress('readback_start', 0)
    readback_progress = (lambda offset: progress('readback', offset)) if progress else None
    actual = sha256(target_fd, source_size, readback_progress)
    if actual != expected_sha:
        raise FlashError('Written image readback SHA-256 does not match.')
    if source_size < target_size and os.pread(target_fd, TAIL, target_size-TAIL) != bytes(TAIL):
        raise FlashError('Obsolete physical end-GPT clearing did not verify.')
    # The image backup GPT stays at the image's own end; unused USB space is not resized.
    gpt.read_gpt(target_fd, source_size)
    if progress:
        progress('readback_complete', source_size)
    return actual


def durable(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def execute(plan, plan_sha, args, source_fd, disk, identity):
    if plan.get('status') != 'READY_FOR_EXECUTION':
        raise FlashError('Private plan is not marked READY_FOR_EXECUTION after final image validation.')
    phrase = 'ERASE ENTIRE USB ' + plan['expected']['serial']
    if args.expected_plan_sha256 != plan_sha or args.confirm != phrase:
        raise FlashError('Execution requires the reviewed plan SHA and exact serial-specific erase phrase.')
    if not args.boot_result:
        raise FlashError('--boot-result is required for execution.')
    evidence_sha = check_boot(args.boot_result, plan['image_sha256'])
    stamp = source_stamp(source_fd)
    size = stamp[2]
    if sha256(source_fd, size) != plan['image_sha256']:
        raise FlashError('Compiled image SHA-256 differs from reviewed plan.')
    gpt.read_gpt(source_fd, size)
    if size > disk['size']-TAIL:
        raise FlashError('Image is too large for this USB profile.')
    # No unmount, writable FD or modification happens before all checks above.
    backend = UDisks(args.allow_auth_dialog)
    for record in reversed(list(tree_records(disk))):
        current, current_id = inspect(plan)
        if current_id != identity:
            raise FlashError('USB identity changed before unmount.')
        current_record = next((r for r in tree_records(current) if r['path'] == record['path']), None)
        if current_record and any(current_record.get('mountpoints') or []):
            backend.unmount(record['path'])
    current, current_id = inspect(plan)
    require_unmounted(current)
    if current_id != identity or source_stamp(source_fd) != stamp:
        raise FlashError('USB or source identity changed before exclusive open.')
    fd = backend.open_exclusive(current['path'])
    fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.fcntl(fd, fcntl.F_GETFD) | fcntl.FD_CLOEXEC)
    started = False
    receipt_dir = None
    try:
        validate_fd(fd, current)
        checked, checked_id = inspect(plan)
        require_unmounted(checked)
        if checked_id != identity or source_stamp(source_fd) != stamp:
            raise FlashError('Identity changed after exclusive open.')
        receipt_dir = ROOT/'artifacts/usb-execution'/('full-flash-'+str(time.time_ns()))
        receipt_dir.mkdir(parents=True, mode=0o700)
        if not receipt_dir.resolve().is_relative_to((ROOT/'artifacts').resolve()):
            raise FlashError('Receipt directory escaped project artifacts.')
        durable(receipt_dir/'plan.json', (json.dumps(plan, indent=2)+'\n').encode())
        for name, offset, length in [('old-start.bin', 0, 34*SECTOR), ('old-end.bin', disk['size']-TAIL, TAIL)]:
            value = os.pread(fd, length, offset)
            if len(value) != length:
                raise FlashError('Cannot back up old partition metadata.')
            durable(receipt_dir/name, value)
        durable(receipt_dir/'started.json', (json.dumps(dict(status='WRITE_STARTING', image_sha256=plan['image_sha256'],
            plan_sha256=plan_sha, boot_result_sha256=evidence_sha, old_data_preserved=False))+'\n').encode())
        started = True
        def interrupted(_signum, _frame):
            raise InterruptedError('USB flash interrupted; target may be partially overwritten.')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        reported = {}
        def progress(phase, offset):
            if phase in ('write', 'readback') and offset-reported.get(phase, 0) < 256*1024**2:
                return
            reported[phase] = offset
            print(json.dumps(dict(event='usb_flash_progress', phase=phase, bytes=offset,
                                  total_bytes=size, percent=round(100*offset/size, 1))), flush=True)
        actual = copy_and_verify(source_fd, fd, size, disk['size'], plan['image_sha256'], progress)
        if source_stamp(source_fd) != stamp:
            raise FlashError('Source metadata changed during copy; do not trust this USB.')
        result = dict(status='PASS', image_sha256=actual, image_bytes=size, usb_bytes=disk['size'],
            full_image_readback='PASS', image_gpt_crc32='PASS', obsolete_end_gpt='CLEARED',
            old_data_preserved=False, secure_full_media_erasure=False, data_expanded=False,
            receipt_directory=str(receipt_dir))
        durable(receipt_dir/'result.json', (json.dumps(result, indent=2)+'\n').encode())
        return result
    except Exception as error:
        if receipt_dir:
            durable(receipt_dir/'failure.json', (json.dumps(dict(status='FAIL', error=str(error),
                partial_target_overwrite_possible=started, automatic_rollback=False))+'\n').encode())
        raise
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--confirm')
    parser.add_argument('--boot-result', type=Path)
    parser.add_argument('--allow-auth-dialog', action='store_true', help='allow UDisks polkit authentication in execution only')
    args = parser.parse_args(argv)
    try:
        if not os.path.ismount(FILES):
            raise FlashError('The FILES drive is not mounted.')
        plan, plan_sha = load_json(args.plan)
        disk, identity = inspect(plan)
        source_fd = regular(plan['image'])
        try:
            size = os.fstat(source_fd).st_size
            if size > disk['size']-TAIL:
                raise FlashError('Image does not fit on the reviewed target.')
            if args.execute:
                result = execute(plan, plan_sha, args, source_fd, disk, identity)
            else:
                result = dict(status='READ_ONLY_PREVIEW', plan_sha256=plan_sha, identity=disk,
                    image_bytes=size, expected_image_sha256=plan['image_sha256'],
                    erase_scope='ENTIRE_USB_ALL_PARTITIONS', confirmation='ERASE ENTIRE USB '+plan['expected']['serial'],
                    source_hash_checked=False, physical_writes=False)
        finally:
            os.close(source_fd)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print('USB flash refused/failed: '+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
