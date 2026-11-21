#!/usr/bin/env python3
"""Guest-only, read-only USB/removable-SD game-volume discovery.

--list prints UTF-8 JSON. --mount-readonly mounts supported unmounted volumes
below /media/library/<filesystem-UUID>, then returns actual verified paths.
The caller must provide a writable /media/library (normally guest tmpfs).
No unmount, repair, formatting, filesystem mutation or credential handling.

ext4 ro alone may replay its journal: noload is mandatory here.
https://docs.kernel.org/admin-guide/ext4.html
Probing uses util-linux's uncached blkid -p -o export. Mounting uses internal
mount with canonicalization disabled and a held read-only device descriptor.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import time

GUEST_MARKER = Path("/etc/surface-image-id")
SYS_ROOT = Path("/sys")
SYS_BLOCK = SYS_ROOT/"class/block"
DEV_ROOT = Path("/dev")
MOUNTINFO = Path("/proc/self/mountinfo")
SWAPS = Path("/proc/swaps")
FILESYSTEMS = Path("/proc/filesystems")
LIBRARY_ROOT = Path("/media/library")
SUPPORTED = {"ext4": "ext4", "ntfs": "ntfs3", "ntfs3": "ntfs3", "vfat": "vfat", "exfat": "exfat"}
UUID_PATTERN = re.compile(r"[A-Fa-f0-9][A-Fa-f0-9-]{3,63}\Z")
MAX_VOLUMES = 64
PROBE_TIMEOUT = 3
MOUNT_TIMEOUT = 8
ACTION_SECONDS = 30


class VolumeError(Exception):
    pass


def require_guest():
    # Keep this before every sysfs, mount, block-device or subprocess operation.
    if (not GUEST_MARKER.is_file() or GUEST_MARKER.is_symlink()
            or not GUEST_MARKER.read_text().strip().startswith("surface-go-")):
        raise VolumeError("Library volume discovery runs only inside the Surface OS image.")
    if os.geteuid() != 0:
        raise VolumeError("Library volume discovery requires the guest's system service privileges.")


def text(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def read_mounts():
    mounts = []
    for line in MOUNTINFO.read_text().splitlines():
        left, separator, right = line.partition(" - ")
        fields, extra = left.split(), right.split()
        if not separator or len(fields) < 6 or len(extra) < 3:
            raise VolumeError("The guest mount table has an unexpected format.")
        mounts.append(dict(dev=fields[2], path=unescape(fields[4]), options=set(fields[5].split(",")),
                           filesystem=extra[0], source=unescape(extra[1]), super_options=set(extra[2].split(","))))
    return mounts


def block_records():
    records = {}
    for item in sorted(SYS_BLOCK.iterdir()):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", item.name):
            continue
        actual = item.resolve(strict=True)
        if not actual.is_relative_to(SYS_ROOT.resolve(strict=True)):
            raise VolumeError("A sysfs block entry points outside sysfs.")
        dev = text(item/"dev")
        size = text(item/"size")
        if not re.fullmatch(r"\d+:\d+", dev) or not size.isdecimal():
            continue
        partition = (item/"partition").is_file()
        records[item.name] = dict(name=item.name, path=str(DEV_ROOT/item.name), sysfs=str(actual),
            dev=dev, bytes=int(size)*512, partition=partition,
            parent=actual.parent.name if partition else item.name,
            slaves=sorted(value.name for value in (item/"slaves").iterdir()) if (item/"slaves").is_dir() else [],
            removable=text(item/"removable") == "1")
    return records


def ancestors(name, records, seen=None):
    seen = set() if seen is None else seen
    if name in seen or name not in records:
        return seen
    seen.add(name)
    record = records[name]
    if record["partition"]:
        ancestors(record["parent"], records, seen)
    for slave in record["slaves"]:
        ancestors(slave, records, seen)
    return seen


def protected_disks(records, mounts):
    by_dev = {record["dev"]: name for name, record in records.items()}
    protected = set()
    root_found = False
    for mount in mounts:
        if mount["path"] in ("/", "/boot", "/boot/efi", "/efi", "/data"):
            name = by_dev.get(mount["dev"])
            if name:
                protected.update(ancestors(name, records))
                root_found |= mount["path"] == "/"
    if not root_found:
        raise VolumeError("Cannot identify the guest's mounted root block device; discovery refused.")
    for line in text(SWAPS).splitlines()[1:]:
        if not line.split():
            continue
        path = unescape(line.split()[0])
        name = Path(path).name
        if name in records:
            protected.update(ancestors(name, records))
            continue
        # A swap file protects the entire underlying disk too.
        covering = [mount for mount in mounts
                    if path == mount["path"] or path.startswith(mount["path"].rstrip("/")+"/")]
        if covering:
            mount = max(covering, key=lambda value: len(value["path"]))
            if (name := by_dev.get(mount["dev"])):
                protected.update(ancestors(name, records))
    return protected


def transport(disk):
    actual = Path(disk["sysfs"])
    if any(re.fullmatch(r"usb\d+", part) for part in actual.parts):
        return "usb"
    if (re.fullmatch(r"mmcblk\d+", disk["name"]) and disk["removable"]
            and text(SYS_BLOCK/disk["name"]/"device/type") == "SD"):
        return "removable_sd"
    return None


def stable_identity(record, disk):
    serial = text(SYS_BLOCK/disk["name"]/"device/serial")
    if not serial:
        for parent in Path(disk["sysfs"]).parents:
            if parent == SYS_ROOT or not parent.is_relative_to(SYS_ROOT):
                break
            if (value := text(parent/"serial")):
                serial = value
                break
    return dict(dev=record["dev"], disk_dev=disk["dev"], bytes=record["bytes"],
                sysfs=record["sysfs"], disk_sysfs=disk["sysfs"], serial=serial,
                model=text(SYS_BLOCK/disk["name"]/"device/model"))


def run_tool(command, timeout=PROBE_TIMEOUT, pass_fds=()):
    return subprocess.run(command, check=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, pass_fds=pass_fds,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})


def util_linux(name):
    require_guest()
    executable = shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not executable:
        raise VolumeError("The image is missing util-linux "+name+".")
    result = run_tool([executable, "--version"])
    if result.returncode or "util-linux" not in result.stdout:
        raise VolumeError("This action requires util-linux "+name+"; the BusyBox variant is insufficient.")
    return executable


def probe(blkid, source, pass_fds=()):
    result = run_tool([blkid, "-p", "-o", "export", str(source)], pass_fds=pass_fds)
    if result.returncode == 2:
        return {}
    if result.returncode:
        raise VolumeError("Filesystem probe failed or found ambiguous signatures: "+str(source))
    if len(result.stdout) > 65536:
        raise VolumeError("Filesystem probe output is unexpectedly large.")
    fields = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in ("UUID", "TYPE", "LABEL"):
                # export encodes unsafe bytes as \xHH, rather than shell words.
                chunks = re.split(r"(\\x[0-9a-fA-F]{2})", value)
                raw = b"".join(bytes([int(chunk[2:], 16)]) if re.fullmatch(r"\\x[0-9a-fA-F]{2}", chunk)
                               else chunk.encode("utf-8") for chunk in chunks)
                fields[key] = raw.decode("utf-8", errors="replace")
    return fields


def public_volume(volume):
    return {key: value for key, value in volume.items() if not key.startswith("_")}


def verify_device_node(record):
    info = Path(record["path"]).lstat()
    expected_rdev = os.makedev(*map(int, record["dev"].split(":")))
    if not stat.S_ISBLK(info.st_mode) or info.st_rdev != expected_rdev:
        raise VolumeError("Device path is not the expected ordinary block node.")


def inventory(blkid, deadline=None):
    require_guest()
    deadline = time.monotonic()+ACTION_SECONDS if deadline is None else deadline
    mounts = read_mounts()
    records = block_records()
    protected = protected_disks(records, mounts)
    partitioned = {record["parent"] for record in records.values() if record["partition"]}
    available_drivers = set(text(FILESYSTEMS).split())
    volumes, errors = [], []
    for name, record in records.items():
        disk = records.get(record["parent"])
        if not disk or protected.intersection(ancestors(name, records)):
            continue
        kind = transport(disk)
        if not kind or (not record["partition"] and name in partitioned):
            continue
        if len(volumes) >= MAX_VOLUMES or time.monotonic() >= deadline:
            errors.append("External-volume discovery reached its count or time limit.")
            break
        volume = dict(device=record["path"], disk=disk["name"], transport=kind,
                      bytes=record["bytes"], uuid=None, filesystem=None, driver=None, label="",
                      mount_paths=[], mount_path=None, read_only=False, eligible=False, status="unprobed",
                      _identity=stable_identity(record, disk))
        try:
            verify_device_node(record)
            fields = probe(blkid, record["path"])
            filesystem, identity = fields.get("TYPE"), fields.get("UUID", "")
            volume.update(filesystem=filesystem, uuid=identity or None, label=fields.get("LABEL", ""),
                          driver=SUPPORTED.get(filesystem))
            if filesystem not in SUPPORTED:
                volume["status"] = "unsupported_filesystem" if filesystem else "no_filesystem"
            elif not UUID_PATTERN.fullmatch(identity):
                volume["status"] = "missing_or_unsafe_uuid"
            elif SUPPORTED[filesystem] not in available_drivers:
                volume["status"] = "kernel_driver_unavailable"
            else:
                mounted = [mount for mount in mounts if mount["dev"] == record["dev"]]
                paths = sorted({mount["path"] for mount in mounted})
                readonly = bool(mounted) and all("ro" in mount["options"] for mount in mounted)
                volume.update(mount_paths=paths, mount_path=paths[0] if paths else None,
                              read_only=readonly, eligible=not mounted,
                              status="already_mounted_readonly" if readonly else "already_mounted_readwrite" if mounted else "unmounted")
        except (VolumeError, OSError, subprocess.TimeoutExpired) as error:
            volume.update(status="probe_error", error=str(error))
            errors.append(record["path"]+": "+str(error))
        volumes.append(volume)
    counts = {}
    for volume in volumes:
        if volume["uuid"]:
            identity = volume["uuid"].lower()
            counts[identity] = counts.get(identity, 0)+1
    for volume in volumes:
        if volume["uuid"] and counts[volume["uuid"].lower()] > 1:
            volume.update(eligible=False, status="duplicate_filesystem_uuid")
    return volumes, errors


def secure_directory(path):
    """Create/open a mountpoint without accepting any symbolic path component."""
    path = Path(path)
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise VolumeError("The library mountpoint must be an absolute ordinary directory.")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            try:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, mode=0o755, dir_fd=fd)
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def open_block(volume):
    fd = os.open(volume["device"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        expected = os.makedev(*map(int, volume["_identity"]["dev"].split(":")))
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != expected:
            raise VolumeError("Volume block descriptor identity changed.")
        size = struct.unpack("Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
        if size != volume["bytes"]:
            raise VolumeError("Opened volume capacity changed.")
        return fd
    except Exception:
        os.close(fd)
        raise


def mount_one(volume, blkid, mount_tool, deadline):
    require_guest()
    latest, _ = inventory(blkid, deadline)
    candidates = [value for value in latest if value["device"] == volume["device"]]
    if (len(candidates) != 1 or not candidates[0]["eligible"]
            or any(candidates[0][key] != volume[key] for key in ("_identity", "uuid", "filesystem", "driver"))):
        raise VolumeError("Volume identity, eligibility or mount state changed before mounting.")
    if time.monotonic() >= deadline:
        raise VolumeError("The library mount action reached its time limit.")
    block_fd = open_block(volume)
    directory_fd = None
    try:
        source = f"/proc/self/fd/{block_fd}"
        fields = probe(blkid, source, pass_fds=(block_fd,))
        if fields.get("UUID") != volume["uuid"] or fields.get("TYPE") != volume["filesystem"]:
            raise VolumeError("Opened volume filesystem identity changed.")
        # Final mount-state check uses the held device major/minor identity.
        mounts = read_mounts()
        if any(mount["dev"] == volume["_identity"]["dev"] for mount in mounts):
            raise VolumeError("The volume was mounted by another service; it was left untouched.")
        target = LIBRARY_ROOT/volume["uuid"].lower()
        if any(mount["path"] == str(target) or mount["path"].startswith(str(target)+"/") for mount in mounts):
            raise VolumeError("The library target already contains a mount; it was left untouched.")
        directory_fd = secure_directory(target)
        if os.listdir(directory_fd):
            raise VolumeError("The library target directory is not empty; it was left untouched.")
        # Keeping both descriptors avoids substituted source paths or target
        # symlinks. mount -c preserves these /proc/self/fd paths in its child.
        target_fd_path = f"/proc/self/fd/{directory_fd}"
        if any(mount["dev"] == volume["_identity"]["dev"] or mount["path"] == str(target)
               or mount["path"].startswith(str(target)+"/") for mount in read_mounts()):
            raise VolumeError("Volume or target mount state changed; it was left untouched.")
        options = "ro,nosuid,nodev,noexec"+(",noload" if volume["driver"] == "ext4" else "")
        result = run_tool([mount_tool, "-n", "-i", "-c", "-t", volume["driver"], "-o", options,
                           source, target_fd_path], timeout=min(MOUNT_TIMEOUT, max(1, deadline-time.monotonic())),
                          pass_fds=(block_fd, directory_fd))
        checked = [mount for mount in read_mounts() if mount["path"] == str(target)]
        if (result.returncode or len(checked) != 1 or checked[0]["dev"] != volume["_identity"]["dev"]
                or checked[0]["filesystem"] != volume["driver"]
                or not {"ro", "nosuid", "nodev", "noexec"}.issubset(checked[0]["options"])):
            raise VolumeError("Read-only mount was not confirmed: "+result.stderr.strip()[:512])
        volume.update(mount_paths=[str(target)], mount_path=str(target), read_only=True,
                      eligible=False, status="mounted_readonly")
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(block_fd)


def result_json(volumes, errors):
    paths = sorted({path for volume in volumes if volume["read_only"]
                    and volume["status"] in ("mounted_readonly", "already_mounted_readonly")
                    for path in volume["mount_paths"]})
    return dict(status="PARTIAL" if errors else "OK", volumes=[public_volume(value) for value in volumes],
                mount_paths=paths, errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true")
    action.add_argument("--mount-readonly", action="store_true")
    args = parser.parse_args()
    try:
        require_guest()
        blkid = util_linux("blkid")
        deadline = time.monotonic()+ACTION_SECONDS
        volumes, errors = inventory(blkid, deadline)
        if args.mount_readonly:
            mount_tool = util_linux("mount")
            for volume in volumes:
                if volume["eligible"]:
                    try:
                        mount_one(volume, blkid, mount_tool, deadline)
                    except (VolumeError, OSError, subprocess.TimeoutExpired) as error:
                        volume.update(status="mount_error", error=str(error), eligible=False)
                        errors.append(volume["device"]+": "+str(error))
        print(json.dumps(result_json(volumes, errors), ensure_ascii=False))
        return 0 if not errors else 1
    except (VolumeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps(dict(status="ERROR", volumes=[], mount_paths=[], errors=[str(error)]), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
