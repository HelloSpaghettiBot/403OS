#!/usr/bin/env python3
"""Historical H-preserving transaction helpers and disposable-file tests.

The --execute entry point is disabled because whole-USB delivery superseded
this plan. Use flash-usb.py with a new private identity/checksum plan.
"""
import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location("surface_usb_plan", Path(__file__).with_name("prepare-usb.py"))
usb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usb)
ROOT = Path(__file__).resolve().parents[1]
FILES_DRIVE = Path("/run/media/system/Files")
STATE_ROOT = ROOT/"artifacts/usb-execution"
CONFIRMATION = "REPLACE OLD WINDOWS BOOT AREA; PRESERVE H"
CHUNK = 4*1024**2
REQUIRED_CHECKS = (
    "uefi_graphical_boot", "root_and_persistent_data_mount",
    "sdl_keyboard_gamepad_injection", "qmp_keyboard_focus_and_framebuffer",
    "dhcp_dns_https", "bluetooth_service", "browser_runtime_launch_exit", "browser_home_launch_global_return",
    "moonlight_binary_runtime", "tailscale_daemon_runtime", "wine64_windows_command_runtime",
    "real_core_homebrew_input_sram_reload", "all_eleven_emulator_binary_runtimes",
    "audio_playback", "ui_clean_poweroff",
)


class ExecuteError(usb.PlanError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"))+"\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sha_argument(value):
    if len(value) != 64 or any(letter not in "0123456789abcdef" for letter in value):
        raise argparse.ArgumentTypeError("Use a lowercase 64-character SHA-256 digest.")
    return value


def safe_regular(path, allowed_root=None):
    path = Path(path)
    if path.is_symlink():
        raise ExecuteError("A source or evidence file must not be a symbolic link.")
    resolved = path.resolve(strict=True)
    if allowed_root and not resolved.is_relative_to(allowed_root.resolve(strict=True)):
        raise ExecuteError("VM evidence must be inside this project's artifacts/vm-tests directory.")
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ExecuteError("A source or evidence descriptor is not a regular file.")
    return fd, resolved


def read_json(path, allowed_root=None):
    fd, resolved = safe_regular(path, allowed_root)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ExecuteError("Evidence JSON is unexpectedly large.")
    return json.loads(raw), digest(raw), resolved


def validate_readiness(path, image_sha):
    manifest, manifest_sha, manifest_path = read_json(path)
    if manifest.get("schema") != "surface-usb-readiness-v1" or manifest.get("image_sha256") != image_sha:
        raise ExecuteError("Readiness schema or exact current image SHA does not match.")
    live = manifest.get("live_boot")
    boots = manifest.get("installed_cold_boots", [])
    if not isinstance(live, dict) or len(boots) != 2:
        raise ExecuteError("Readiness requires one live boot and two installed cold boots.")
    seen, counts, disks, records, state_hashes = set(), [], [], [], []
    for mode, record in [("live", live)]+[("test", value) for value in boots]:
        if not isinstance(record, dict) or record.get("status") != "PASS":
            raise ExecuteError("Every readiness boot must explicitly be PASS.")
        result_path = Path(record.get("result", ""))
        if not result_path.is_absolute():
            result_path = manifest_path.parent/result_path
        result, result_sha, result_path = read_json(result_path, ROOT/"artifacts/vm-tests")
        command, command_sha, command_path = read_json(result_path.with_name("command.json"), ROOT/"artifacts/vm-tests")
        if result_sha != record.get("result_sha256") or command_sha != record.get("command_sha256"):
            raise ExecuteError("VM result or QEMU command evidence digest changed.")
        if result_path in seen:
            raise ExecuteError("Cold-boot evidence must come from distinct VM runs.")
        seen.add(result_path)
        if (result.get("mode") != mode or result.get("image_sha256") != image_sha
                or result.get("status") not in ("PASS", "PARTIAL")
                or result.get("direct_kernel_boot") is not False
                or result.get("installer_attached") is not (mode == "live")):
            raise ExecuteError("VM evidence is not a completed detached UEFI boot of this image.")
        checks = result.get("checks", {})
        if (not isinstance(checks, dict) or any(checks.get(name) != "PASS" for name in REQUIRED_CHECKS)
                or "FAIL" in checks.values()):
            raise ExecuteError("Required boot, input, network, app, emulator, audio or power checks did not pass.")
        if (not isinstance(command, list) or not command or "qemu-system-x86_64" not in str(command[0])
                or "-kernel" in command or "-incoming" in command or "-loadvm" in command
                or any("id=media" in str(value) or "id=installtarget" in str(value) for value in command)):
            raise ExecuteError("QEMU evidence is not a new UEFI VM without installation media.")
        if mode == "test":
            if record.get("cold_boot") is not True:
                raise ExecuteError("Installed runs must explicitly attest cold boot.")
            count = result.get("data_boot_count")
            if not isinstance(count, int) or count < 1 or not result.get("disk"):
                raise ExecuteError("Installed cold boots need a persistent DATA boot count and disk identity.")
            counts.append(count)
            disks.append(str(Path(result["disk"]).resolve()))
            state_sha = result.get("tailscale_state_sha256", "")
            if (checks.get("tailscale_daemon_persistence") != "PASS" or not isinstance(state_sha, str)
                    or len(state_sha) != 64 or any(letter not in "0123456789abcdef" for letter in state_sha)):
                raise ExecuteError("Installed cold boots must verify Tailscale persistence and record its state SHA-256.")
            state_hashes.append(state_sha)
        records.append(dict(mode=mode, result=str(result_path), result_sha256=result_sha,
                            command_sha256=command_sha))
    if counts[1] <= counts[0] or disks[0] != disks[1]:
        raise ExecuteError("Cold boots must advance DATA on the same installed virtual disk.")
    if state_hashes[0] != state_hashes[1]:
        raise ExecuteError("Tailscale state SHA-256 changed between the installed cold boots.")
    return dict(manifest=str(manifest_path), manifest_sha256=manifest_sha,
                installed_disk=disks[0], data_boot_counts=counts,
                tailscale_state_sha256=state_hashes[0], evidence=records)


def validate_expected(args):
    expected = dict(serial=usb.EXPECTED_SERIAL, model=usb.EXPECTED_MODEL, bytes=usb.EXPECTED_BYTES,
                    h_uuid=usb.H_FS_UUID, h_partuuid=usb.H_GUID, h_start=usb.H_START)
    supplied = {key: getattr(args, "expected_"+key) for key in expected}
    if supplied != expected:
        raise ExecuteError("Explicit target identity arguments differ from the reviewed USB and H partition.")
    return expected


def unmounted(disk):
    pending = [disk]
    records = []
    while pending:
        record = pending.pop()
        records.append(record)
        pending.extend(record.get("children", []))
        if any(record.get("mountpoints") or []):
            raise ExecuteError("Every USB partition must already be unmounted; this tool never unmounts it.")
    devices = set()
    for record in records:
        info = Path(record["path"]).stat()
        if not stat.S_ISBLK(info.st_mode):
            raise ExecuteError("USB metadata contains a non-block path.")
        devices.add(f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}")
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        if len(parts := line.split()) > 2 and parts[2] in devices:
            raise ExecuteError("The disk or a child is mounted according to mountinfo.")


def source_stamp(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def hash_source(fd, size, copies):
    whole = hashlib.sha256()
    partitions = [hashlib.sha256() for _ in copies]
    offset = 0
    while offset < size:
        chunk = usb.read_exact(fd, min(CHUNK, size-offset), offset)
        whole.update(chunk)
        for copy, value in zip(copies, partitions):
            first = max(offset, copy["source_offset"])
            last = min(offset+len(chunk), copy["source_offset"]+copy["bytes"])
            if first < last:
                value.update(chunk[first-offset:last-offset])
        offset += len(chunk)
    return whole.hexdigest(), [value.hexdigest() for value in partitions]


def gpt_snapshots(fd, size):
    return [(0, usb.read_exact(fd, 34*usb.SECTOR, 0)),
            (size-33*usb.SECTOR, usb.read_exact(fd, 33*usb.SECTOR, size-33*usb.SECTOR))]


def snapshots_sha(snapshots):
    return digest(b"".join(value for _, value in snapshots))


def h_sentinels(reader):
    start, size = usb.H_START*usb.SECTOR, usb.H_BYTES
    return [dict(offset=offset, bytes=4096, sha256=digest(reader(4096, offset)))
            for offset in (start, start+size//2, start+size-4096)]


def build_review(args, source_fd, target_fd, disk):
    expected = validate_expected(args)
    unmounted(disk)
    size = os.fstat(source_fd).st_size
    stamp = source_stamp(source_fd)
    target = usb.read_gpt(target_fd, usb.EXPECTED_BYTES)
    source = usb.read_gpt(source_fd, size)
    plan = usb.build_plan(target, source)
    snapshots = gpt_snapshots(target_fd, usb.EXPECTED_BYTES)
    target_sha = snapshots_sha(snapshots)
    if target_sha != args.expected_target_gpt_sha256:
        raise ExecuteError("Target MBR/GPT snapshot SHA does not match the reviewed state.")
    image_sha, part_shas = hash_source(source_fd, size, plan["copies"])
    if source_stamp(source_fd) != stamp or image_sha != args.expected_source_sha256:
        raise ExecuteError("The source image changed or its full SHA-256 does not match.")
    for copy, value in zip(plan["copies"], part_shas):
        copy["sha256"] = value
    readiness = validate_readiness(args.readiness, image_sha)
    review = dict(schema="surface-usb-execution-plan-v1", device=str(Path(args.device).resolve()),
                  identity=expected, source=str(Path(args.image).resolve()), source_bytes=size,
                  source_sha256=image_sha, target_gpt_sha256=target_sha,
                  disk_guid=target["primary"]["disk_guid"], readiness=readiness,
                  h_sentinels=h_sentinels(lambda length, offset: usb.read_exact(target_fd, length, offset)),
                  replacement=usb.public_plan(plan),
                  consequences="Only the old Windows boot area is replaced; H entry and bytes are preserved. GPT rollback cannot restore overwritten boot payloads.")
    return review, target, plan, snapshots, stamp


def files_backup_guard():
    result = subprocess.run(["findmnt", "--json", "--mountpoint", str(FILES_DRIVE),
                             "-o", "TARGET,SOURCE,FSTYPE,LABEL"], check=True, capture_output=True, text=True)
    mounts = json.loads(result.stdout).get("filesystems", [])
    if (len(mounts) != 1 or mounts[0].get("target") != str(FILES_DRIVE)
            or (mounts[0].get("label") or "").lower() != "files"):
        raise ExecuteError("Mount the FILES drive before saving required GPT backups.")
    if not STATE_ROOT.resolve().is_relative_to(FILES_DRIVE):
        raise ExecuteError("USB backup storage must be on the mounted FILES drive.")
    if shutil.disk_usage(FILES_DRIVE).free < 16*1024**2:
        raise ExecuteError("FILES lacks space for durable backup and execution records.")


def durable_file(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_backup(review, snapshots):
    files_backup_guard()
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_ROOT.is_symlink():
        raise ExecuteError("Backup storage must not be a symbolic link.")
    fsync_directory(STATE_ROOT.parent)
    directory = STATE_ROOT/("usb-"+str(time.time_ns()))
    directory.mkdir(mode=0o700)
    files = []
    for index, (offset, value) in enumerate(snapshots):
        name = f"gpt-{index}.bin"
        durable_file(directory/name, value)
        files.append(dict(file=name, offset=offset, bytes=len(value), sha256=digest(value)))
    durable_file(directory/"manifest.json", canonical(dict(schema="surface-usb-gpt-backup-v1", files=files,
                 target_gpt_sha256=review["target_gpt_sha256"], identity=review["identity"])))
    durable_file(directory/"execution-plan.json", canonical(review))
    durable_file(directory/"IN_PROGRESS", b"USB payload replacement may have started; inspect execution-result.json.\n")
    fsync_directory(directory)
    fsync_directory(STATE_ROOT)
    return directory


class DeviceIO:
    """The only block-write boundary; every write is restricted to reviewed spans."""
    def __init__(self, fd, target, plan):
        if not stat.S_ISBLK(os.fstat(fd).st_mode):
            raise ExecuteError("Actual execution requires an exclusively opened block descriptor.")
        self.fd, self.target, self.plan = fd, target, plan

    def allowed(self, offset, length, phase):
        if length <= 0 or offset < 0:
            return False
        h_start = usb.H_START*usb.SECTOR
        if offset < h_start+usb.H_BYTES and offset+length > h_start:
            return False
        if phase == "payload":
            ranges = [(copy["target_offset"], copy["bytes"]) for copy in self.plan["copies"]]
        elif phase == "gpt":
            ranges = [(usb.SECTOR, 33*usb.SECTOR), (self.target["size"]-33*usb.SECTOR, 33*usb.SECTOR)]
        elif phase == "rollback":
            ranges = [(0, 34*usb.SECTOR), (self.target["size"]-33*usb.SECTOR, 33*usb.SECTOR)]
        else:
            return False
        return any(offset >= first and offset+length <= first+size for first, size in ranges)

    def write(self, value, offset, phase):
        if not self.allowed(offset, len(value), phase):
            raise ExecuteError("A requested write is outside reviewed boot/GPT spans or overlaps H.")
        while value:
            count = self._pwrite(value, offset)
            if count <= 0 or count > len(value):
                raise ExecuteError("USB write did not make valid forward progress.")
            value, offset = value[count:], offset+count

    def _pwrite(self, value, offset):
        return os.pwrite(self.fd, value, offset)

    def read(self, length, offset):
        return usb.read_exact(self.fd, length, offset)

    def flush(self):
        os.fsync(self.fd)

    def drop_cache(self, offset, length):
        if hasattr(os, "posix_fadvise"):
            try:
                os.posix_fadvise(self.fd, offset, length, os.POSIX_FADV_DONTNEED)
            except OSError as error:
                if error.errno not in (errno.EINVAL, errno.ENOSYS, errno.ESPIPE):
                    raise
                print("Device cache eviction is unavailable; readback follows fsync.", file=sys.stderr)

    def verify_gpt(self):
        return usb.read_gpt(self.fd, self.target["size"])


def copy_dense(source_fd, io, copy):
    value = hashlib.sha256()
    position = 0
    while position < copy["bytes"]:
        chunk = usb.read_exact(source_fd, min(CHUNK, copy["bytes"]-position), copy["source_offset"]+position)
        # Zero chunks are written too. No sparse-copy or hole-punch operation
        # is used on a real device, so no old boot data can fill source holes.
        io.write(chunk, copy["target_offset"]+position, "payload")
        value.update(chunk)
        position += len(chunk)
    if value.hexdigest() != copy["sha256"]:
        raise ExecuteError("Source partition changed during its payload copy.")


def hash_readback(io, copy):
    value = hashlib.sha256()
    position = 0
    while position < copy["bytes"]:
        chunk = io.read(min(CHUNK, copy["bytes"]-position), copy["target_offset"]+position)
        value.update(chunk)
        position += len(chunk)
    return value.hexdigest()


def run_transaction(source_fd, io, review, snapshots, source_identity, inject_failure=False):
    """Run only after backups and descriptor guards; tests supply regular-file IO."""
    result = dict(status="FAIL", payloads_may_be_partial=False, gpt_rollback="NOT_NEEDED", payloads=[])
    try:
        for copy in io.plan["copies"]:
            result["payloads_may_be_partial"] = True
            print(f"Copying {copy['name']} ({copy['bytes']} bytes)", file=sys.stderr, flush=True)
            copy_dense(source_fd, io, copy)
            io.flush()
            io.drop_cache(copy["target_offset"], copy["bytes"])
            actual = hash_readback(io, copy)
            if actual != copy["sha256"]:
                raise ExecuteError("USB payload readback SHA-256 mismatch: "+copy["name"])
            result["payloads"].append(dict(partition=copy["partition"], sha256=actual, readback="PASS"))
        if source_stamp(source_fd) != source_identity:
            raise ExecuteError("Source image metadata changed during copying.")
        if h_sentinels(io.read) != review["h_sentinels"]:
            raise ExecuteError("H preservation sentinels changed before GPT publication.")
        for index, name in enumerate(("backup", "primary")):
            part = io.target[name]
            io.write(io.plan["table"], part["entries_lba"]*usb.SECTOR, "gpt")
            io.write(usb.make_header(part["raw"], io.plan["table"]), part["current"]*usb.SECTOR, "gpt")
            io.flush()
            if inject_failure and index == 0:
                raise ExecuteError("Injected test failure before primary GPT publication.")
        checked = io.verify_gpt()
        if checked["table"] != io.plan["table"] or checked["table"][128:256] != io.target["table"][128:256]:
            raise ExecuteError("Published GPT or exact preserved H entry differs from the reviewed plan.")
        if h_sentinels(io.read) != review["h_sentinels"]:
            raise ExecuteError("H preservation sentinels changed after GPT publication.")
        result.update(status="PASS", payloads_may_be_partial=False, gpt_crc32="PASS",
                      exact_h_entry_preservation="PASS", h_sentinels="PASS",
                      h_write_exclusion="PASS", reboot_or_replug_required=True)
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = str(error) or type(error).__name__
        try:
            for offset, contents in reversed(snapshots):
                io.write(contents, offset, "rollback")
            io.flush()
            restored = io.verify_gpt()
            if (restored["table"] != io.target["table"]
                    or any(io.read(len(contents), offset) != contents for offset, contents in snapshots)):
                raise ExecuteError("Restored GPT differs from the saved table.")
            result["gpt_rollback"] = "PASS"
            result["h_sentinels"] = "PASS" if h_sentinels(io.read) == review["h_sentinels"] else "FAIL"
        except Exception as rollback_error:
            result["gpt_rollback"] = "FAIL"
            result["rollback_error"] = str(rollback_error)
    return result


def open_exclusive(device, original_rdev):
    # This function is never used by plan mode or by the test suite.
    fd = os.open(device, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != original_rdev:
            raise ExecuteError("The exclusively opened device identity changed.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        size = struct.unpack("Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]  # BLKGETSIZE64
        sector = struct.unpack("I", fcntl.ioctl(fd, 0x1268, bytes(4)))[0]  # BLKSSZGET
        if size != usb.EXPECTED_BYTES or sector != usb.SECTOR:
            raise ExecuteError("Opened USB capacity or logical sector size changed.")
        return fd
    except Exception:
        os.close(fd)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan-only", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--device", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--readiness", required=True, type=Path)
    parser.add_argument("--expected-serial", required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-bytes", required=True, type=int)
    parser.add_argument("--expected-h-uuid", required=True)
    parser.add_argument("--expected-h-partuuid", required=True)
    parser.add_argument("--expected-h-start", required=True, type=int)
    parser.add_argument("--expected-source-sha256", required=True, type=sha_argument)
    parser.add_argument("--expected-target-gpt-sha256", required=True, type=sha_argument)
    parser.add_argument("--reviewed-plan-sha256", type=sha_argument)
    parser.add_argument("--confirm")
    parser.add_argument("--plan-out", type=Path)
    args = parser.parse_args()
    source_fd = target_fd = writable_fd = None
    try:
        if args.execute:
            raise ExecuteError("Legacy preservation execution is disabled; use flash-usb.py with a private whole-USB plan.")
        validate_expected(args)
        if args.execute and (args.confirm != CONFIRMATION or not args.reviewed_plan_sha256):
            raise ExecuteError("Execution requires the exact confirmation phrase and reviewed plan SHA-256.")
        if args.device.is_symlink() or not stat.S_ISBLK(args.device.stat().st_mode):
            raise ExecuteError("Use the actual whole USB block path, not a symbolic link or regular file.")
        disk = usb.metadata(args.device)
        unmounted(disk)
        source_fd, _ = safe_regular(args.image)
        fcntl.flock(source_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        target_fd = os.open(args.device, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        target_info = os.fstat(target_fd)
        if not stat.S_ISBLK(target_info.st_mode) or target_info.st_rdev != args.device.stat().st_rdev:
            raise ExecuteError("Read-only target descriptor identity changed.")
        review, target, plan, snapshots, stamp = build_review(args, source_fd, target_fd, disk)
        review_sha = digest(canonical(review))
        if args.plan_out:
            if not args.plan_out.resolve().is_relative_to((ROOT/"artifacts").resolve()):
                raise ExecuteError("Write the review plan only beneath this project's artifacts directory on FILES.")
            durable_file(args.plan_out, canonical(review))
            fsync_directory(args.plan_out.resolve().parent)
        if args.plan_only:
            print(json.dumps(dict(mode="READ_ONLY_EXECUTION_PLAN", plan_sha256=review_sha,
                                  plan=review, block_writes=False), indent=2))
            return 0
        if args.reviewed_plan_sha256 != review_sha:
            raise ExecuteError("The recomputed plan differs from the reviewed plan SHA-256.")
        files_backup_guard()
        writable_fd = open_exclusive(args.device, target_info.st_rdev)
        # Check stable identity, mount state and raw bytes again while holding
        # the exclusive block claim; no writes have happened at this point.
        unmounted(usb.metadata(args.device))
        if (args.device.stat().st_rdev != target_info.st_rdev
                or snapshots_sha(gpt_snapshots(writable_fd, usb.EXPECTED_BYTES)) != review["target_gpt_sha256"]
                or source_stamp(source_fd) != stamp
                or h_sentinels(lambda length, offset: usb.read_exact(writable_fd, length, offset)) != review["h_sentinels"]):
            raise ExecuteError("Target or source state changed after review; no payload write was started.")
        backup_directory = make_backup(review, snapshots)
        io = DeviceIO(writable_fd, target, plan)
        def interrupted(_signum, _frame):
            raise InterruptedError("USB write was interrupted by a termination signal.")
        signal.signal(signal.SIGTERM, interrupted)
        result = run_transaction(source_fd, io, review, snapshots, stamp)
        result.update(backup=str(backup_directory), reviewed_plan_sha256=review_sha,
                      target=str(args.device), source_sha256=review["source_sha256"])
        durable_file(backup_directory/"execution-result.json", canonical(result))
        fsync_directory(backup_directory)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1
    except (usb.PlanError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print("USB execution refused: "+str(error), file=sys.stderr)
        return 1
    finally:
        for fd in (writable_fd, target_fd, source_fd):
            if fd is not None:
                os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
