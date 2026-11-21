#!/usr/bin/env python3
"""Read-only USB planning and disposable regular-file GPT tests.

There is deliberately no block-device write implementation. Real USB paths
are opened O_RDONLY only, and mounted partitions block readiness. The sole
writable path is --apply-fixture beneath artifacts/usb-tests/.
"""
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "artifacts/usb-tests"
SECTOR = 512
MIB = 1024 ** 2
# Synthetic geometry for the superseded preservation fixture; never owner metadata.
EXPECTED_MODEL = "EXAMPLE_USB_FIXTURE"
EXPECTED_SERIAL = "EXAMPLE-USB-0001"
EXPECTED_BYTES = 256 * 1024**3
OLD_BOOT_GUID = "11111111-1111-4111-8111-111111111111"
H_GUID = "22222222-2222-4222-8222-222222222222"
H_FS_UUID = "0123456789ABCDEF"
H_START = 20 * 1024**2
H_BYTES = EXPECTED_BYTES - H_START*SECTOR - 34*SECTOR
ROOT_GUID = "8b4a530e-d6d4-4e69-9307-310000000001"
DATA_GUID = "8b4a530e-d6d4-4e69-9307-310000000002"
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


class PlanError(Exception):
    pass


def read_exact(fd, size, offset):
    value = os.pread(fd, size, offset)
    if len(value) != size:
        raise PlanError("A disk read was shorter than the declared geometry.")
    return value


def guid(raw):
    return str(uuid.UUID(bytes_le=raw))


def header(raw):
    if raw[:8] != b"EFI PART":
        raise PlanError("GPT signature missing.")
    revision, length, checksum = struct.unpack_from("<III", raw, 8)
    if revision != 0x10000 or not 92 <= length <= SECTOR:
        raise PlanError("Unsupported GPT header format.")
    checked = bytearray(raw[:length])
    struct.pack_into("<I", checked, 16, 0)
    if zlib.crc32(checked) != checksum:
        raise PlanError("GPT header CRC32 mismatch.")
    current, backup, first, last = struct.unpack_from("<QQQQ", raw, 24)
    entries_lba, count, width, entries_crc = struct.unpack_from("<QIII", raw, 72)
    if count != 128 or width != 128:
        raise PlanError("Only the reviewed 128-entry, 128-byte GPT layout is supported.")
    return dict(raw=raw, current=current, backup=backup, first=first, last=last,
                disk_guid=guid(raw[56:72]), entries_lba=entries_lba,
                count=count, width=width, entries_crc=entries_crc)


def entry(raw, number):
    if raw[:16] == bytes(16):
        return None
    start, end, attributes = struct.unpack_from("<QQQ", raw, 32)
    return dict(number=number, type=guid(raw[:16]), guid=guid(raw[16:32]),
                start=start, end=end, sectors=end-start+1, bytes=(end-start+1)*SECTOR,
                attributes=attributes, name=raw[56:128].decode("utf-16-le").rstrip("\0"))


def read_gpt(fd, size):
    if size % SECTOR or size < 68 * SECTOR:
        raise PlanError("Disk size is not a supported 512-byte-sector GPT geometry.")
    primary = header(read_exact(fd, SECTOR, SECTOR))
    backup = header(read_exact(fd, SECTOR, size-SECTOR))
    if (primary["current"] != 1 or primary["backup"] != size//SECTOR-1
            or backup["current"] != primary["backup"] or backup["backup"] != 1
            or primary["disk_guid"] != backup["disk_guid"]
            or primary["first"] != backup["first"] or primary["last"] != backup["last"]):
        raise PlanError("Primary/backup GPT geometry disagrees.")
    length = primary["count"] * primary["width"]
    if (primary["entries_lba"] != 2 or backup["entries_lba"] != size//SECTOR-33
            or primary["first"] < 34 or primary["last"] >= backup["entries_lba"]):
        raise PlanError("GPT tables are outside the reviewed disk locations.")
    table = read_exact(fd, length, primary["entries_lba"]*SECTOR)
    other = read_exact(fd, length, backup["entries_lba"]*SECTOR)
    if table != other or zlib.crc32(table) != primary["entries_crc"] or zlib.crc32(other) != backup["entries_crc"]:
        raise PlanError("Primary/backup GPT entry table or CRC32 disagrees.")
    entries = [entry(table[i:i+128], i//128+1) for i in range(0, length, 128)]
    used = sorted((value for value in entries if value), key=lambda value: value["start"])
    for value in used:
        if value["start"] < primary["first"] or value["end"] > primary["last"] or value["start"] > value["end"]:
            raise PlanError("A GPT partition falls outside usable sectors.")
    if any(left["end"] >= right["start"] for left, right in zip(used, used[1:])):
        raise PlanError("GPT partitions overlap.")
    return dict(primary=primary, backup=backup, table=table, entries=entries, size=size)


def make_entry(source, start, name):
    raw = bytearray(128)
    raw[:16] = uuid.UUID(source["type"]).bytes_le
    raw[16:32] = uuid.UUID(source["guid"]).bytes_le
    struct.pack_into("<QQQ", raw, 32, start, start+source["sectors"]-1, source["attributes"])
    raw[56:128] = name.encode("utf-16-le").ljust(72, b"\0")
    return bytes(raw)


def make_header(original, table):
    raw = bytearray(original)
    struct.pack_into("<I", raw, 88, zlib.crc32(table))
    struct.pack_into("<I", raw, 16, 0)
    length = struct.unpack_from("<I", raw, 12)[0]
    struct.pack_into("<I", raw, 16, zlib.crc32(raw[:length]))
    return bytes(raw)


def build_plan(target, source):
    old_boot, preserved = target["entries"][:2]
    if target["size"] != EXPECTED_BYTES or not old_boot or not preserved:
        raise PlanError("Target geometry does not match the reviewed USB.")
    if old_boot["guid"] != OLD_BOOT_GUID or old_boot["start"] != 2048 or old_boot["end"] != H_START-1:
        raise PlanError("The old Windows boot region changed.")
    if (preserved["guid"] != H_GUID or preserved["start"] != H_START
            or preserved["bytes"] != H_BYTES):
        raise PlanError("The H partition identity, start or size changed.")
    if any(target["entries"][2:]):
        raise PlanError("Unexpected extra target partitions; refusing to repurpose entries.")
    by_guid = {value["guid"]: value for value in source["entries"] if value}
    esps = [value for value in source["entries"] if value and value["type"] == ESP_TYPE]
    if len(esps) != 1 or ROOT_GUID not in by_guid or DATA_GUID not in by_guid:
        raise PlanError("Source image must contain one EFI partition and the project's root/data GUIDs.")
    parts = [(1, esps[0], "Surface EFI", 64*MIB),
             (3, by_guid[ROOT_GUID], "Surface root", 2048*MIB),
             (4, by_guid[DATA_GUID], "Surface DATA", 4096*MIB)]
    cursor = old_boot["start"]
    table = bytearray(target["table"])
    copies = []
    for number, payload, name, expected_size in parts:
        if payload["bytes"] != expected_size:
            raise PlanError("Source EFI/root/data sizes differ from the reviewed 64MiB/2GiB/4GiB profile.")
        cursor = (cursor+2047)//2048*2048
        last = cursor+payload["sectors"]-1
        if last > old_boot["end"]:
            raise PlanError("The OS partitions do not fit wholly inside the old boot region.")
        table[(number-1)*128:number*128] = make_entry(payload, cursor, name)
        copies.append(dict(partition=number, name=name, source_offset=payload["start"]*SECTOR,
                           target_offset=cursor*SECTOR, bytes=payload["bytes"], start=cursor, end=last))
        cursor = last+1
    if bytes(table[128:256]) != target["table"][128:256]:
        raise PlanError("Internal error: the preserved H GPT entry was modified.")
    return dict(table=bytes(table), copies=copies, preserved_h=preserved,
                h_entry_sha256=hashlib.sha256(target["table"][128:256]).hexdigest(),
                old_boot_start=old_boot["start"], old_boot_end=old_boot["end"],
                unused_old_boot_bytes=(old_boot["end"]-cursor+1)*SECTOR)


def metadata(device):
    result = subprocess.run(["lsblk", "--json", "--tree", "--bytes", "-o",
                             "PATH,MODEL,SERIAL,SIZE,TYPE,TRAN,LOG-SEC,FSTYPE,LABEL,UUID,PARTUUID,START,MOUNTPOINTS",
                             str(device)], check=True, text=True, capture_output=True)
    records = json.loads(result.stdout)["blockdevices"]
    if len(records) != 1:
        raise PlanError("Expected exactly one whole-disk lsblk record.")
    disk = records[0]
    if (disk["type"] != "disk" or disk.get("tran") != "usb" or disk.get("log-sec") != SECTOR
            or (disk.get("model") or "").strip() != EXPECTED_MODEL
            or (disk.get("serial") or "").strip() != EXPECTED_SERIAL or disk["size"] != EXPECTED_BYTES):
        raise PlanError("USB model, stable serial or exact capacity did not match.")
    children = disk.get("children", [])
    h = [child for child in children if child.get("partuuid") == H_GUID]
    boot = [child for child in children if child.get("partuuid") == OLD_BOOT_GUID]
    if (len(children) != 2 or len(h) != 1 or len(boot) != 1
            or h[0]["start"] != H_START or h[0]["size"] != H_BYTES
            or h[0]["uuid"] != H_FS_UUID or h[0]["fstype"] != "ntfs"):
        raise PlanError("The expected H NTFS identity/start/size was not found unchanged.")
    return disk


def public_plan(plan):
    return {key: value for key, value in plan.items() if key != "table"}


def preview(device, image):
    device = Path(device)
    if not stat.S_ISBLK(device.stat().st_mode):
        raise PlanError("Real-device preview requires a whole block device.")
    disk = metadata(device)
    mounted = [mount for child in disk.get("children", []) for mount in child.get("mountpoints", []) if mount]
    result = dict(mode="READ_ONLY_PREVIEW", device=str(device), identity=disk,
                  host_writes_implemented=False, ready=False, blockers=[])
    if mounted:
        result["blockers"].append("All USB partitions must be unmounted before any future write: "+", ".join(mounted))
    try:
        target_fd = os.open(device, os.O_RDONLY | os.O_CLOEXEC)
    except PermissionError:
        result["blockers"].append("Read-only raw GPT permission is required to validate exact preserved entry bytes.")
        return result
    try:
        with image.open("rb") as source:
            plan = build_plan(read_gpt(target_fd, disk["size"]), read_gpt(source.fileno(), image.stat().st_size))
        result["plan"] = public_plan(plan)
    finally:
        os.close(target_fd)
    result["blockers"].append("Host block-device writing is deliberately absent from this preview/test utility.")
    return result


def regular_fd(path, write=False):
    path = Path(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PlanError("Fixture path does not resolve to an existing regular file.") from error
    if path.is_symlink() or not resolved.is_relative_to(FIXTURES.resolve()) or not path.is_file():
        raise PlanError("Writable fixtures must be ordinary files below artifacts/usb-tests/.")
    # Open read-only first, then verify the opened inode. Reopening this exact
    # held inode through /proc avoids a rename race that could otherwise open
    # a substituted block device with O_RDWR before checking its file type.
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PlanError("Fixture descriptor is not a regular file.")
    if write:
        try:
            writable = os.open(f"/proc/self/fd/{fd}", os.O_RDWR | os.O_CLOEXEC)
        finally:
            os.close(fd)
        return writable
    return fd


def fixture_write(fd, value, offset):
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise PlanError("Block-device writes are not implemented.")
    if os.pwrite(fd, value, offset) != len(value):
        raise PlanError("Fixture write was incomplete.")


def copy_sparse_fixture(source_fd, target_fd, source_offset, target_offset, length):
    if not stat.S_ISREG(os.fstat(target_fd).st_mode):
        raise PlanError("Sparse payload copies only support regular fixture files.")
    # Clear the complete destination range first. Skipping sparse source holes
    # without this step could leave old Windows bytes inside new filesystems.
    libc = ctypes.CDLL(None, use_errno=True)
    fallocate = libc.fallocate
    fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong]
    if fallocate(target_fd, 3, target_offset, length) != 0:  # PUNCH_HOLE | KEEP_SIZE
        raise OSError(ctypes.get_errno(), "Fixture filesystem must support sparse hole punching")
    cursor, end = source_offset, source_offset+length
    while cursor < end:
        try:
            begin = os.lseek(source_fd, cursor, os.SEEK_DATA)
        except OSError as error:
            if error.errno == errno.ENXIO:
                break
            raise
        if begin >= end:
            break
        stop = min(os.lseek(source_fd, begin, os.SEEK_HOLE), end)
        while begin < stop:
            chunk = read_exact(source_fd, min(MIB, stop-begin), begin)
            fixture_write(target_fd, chunk, target_offset+begin-source_offset)
            begin += len(chunk)
        cursor = stop


def apply_fixture(path, image, inject_failure=False):
    """Fixture-only mutation. No device-path, mount or format helper is called."""
    target_fd = regular_fd(path, write=True)
    try:
        with image.open("rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise PlanError("Source image must be a regular file.")
            if (os.fstat(source.fileno()).st_dev, os.fstat(source.fileno()).st_ino) == (os.fstat(target_fd).st_dev, os.fstat(target_fd).st_ino):
                raise PlanError("Source and fixture must be separate files.")
            target = read_gpt(target_fd, os.fstat(target_fd).st_size)
            plan = build_plan(target, read_gpt(source.fileno(), image.stat().st_size))
            backup_dir = FIXTURES / ("gpt-backup-"+str(time.time_ns()))
            backup_dir.mkdir(mode=0o700)
            snapshots = [(0, read_exact(target_fd, 34*SECTOR, 0)),
                         (target["size"]-33*SECTOR, read_exact(target_fd, 33*SECTOR, target["size"]-33*SECTOR))]
            manifest = []

            def durable_backup(name, contents):
                with (backup_dir/name).open("xb") as output:
                    output.write(contents)
                    output.flush()
                    os.fsync(output.fileno())

            for index, (offset, value) in enumerate(snapshots):
                name = f"gpt-{index}.bin"
                durable_backup(name, value)
                manifest.append(dict(file=name, offset=offset, bytes=len(value), sha256=hashlib.sha256(value).hexdigest()))
            durable_backup("manifest.json", (json.dumps(manifest, indent=2)+"\n").encode())
            durable_backup("plan.json", (json.dumps(public_plan(plan), indent=2)+"\n").encode())
            directory_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            try:
                for copy in plan["copies"]:
                    copy_sparse_fixture(source.fileno(), target_fd, copy["source_offset"], copy["target_offset"], copy["bytes"])
                backup = target["backup"]
                fixture_write(target_fd, plan["table"], backup["entries_lba"]*SECTOR)
                fixture_write(target_fd, make_header(backup["raw"], plan["table"]), backup["current"]*SECTOR)
                os.fsync(target_fd)
                if inject_failure:
                    raise PlanError("Injected fixture failure before primary GPT update.")
                primary = target["primary"]
                fixture_write(target_fd, plan["table"], primary["entries_lba"]*SECTOR)
                fixture_write(target_fd, make_header(primary["raw"], plan["table"]), SECTOR)
                os.fsync(target_fd)
                checked = read_gpt(target_fd, target["size"])
                if checked["table"][128:256] != target["table"][128:256]:
                    raise PlanError("H's exact partition entry changed after fixture update.")
            except Exception:
                for offset, value in reversed(snapshots):
                    fixture_write(target_fd, value, offset)
                os.fsync(target_fd)
                raise
            return dict(mode="DISPOSABLE_REGULAR_FILE_TEST", fixture=str(Path(path).resolve()),
                        backup=str(backup_dir), plan=public_plan(plan), gpt_crc32="PASS",
                        exact_h_entry_preservation="PASS", host_block_device_writes=False)
    finally:
        os.close(target_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", metavar="DEVICE", help="inspect stable USB identity and GPT read-only")
    action.add_argument("--apply-fixture", metavar="FILE", help="test writes to an existing disposable regular file only")
    parser.add_argument("--image", type=Path, default=ROOT/"output/images/surface-go.img")
    args = parser.parse_args()
    try:
        result = preview(args.preview, args.image) if args.preview else apply_fixture(args.apply_fixture, args.image)
        print(json.dumps(result, indent=2))
        return 0
    except (PlanError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print("USB plan failed: "+str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
