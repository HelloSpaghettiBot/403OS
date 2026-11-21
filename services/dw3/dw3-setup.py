#!/usr/bin/env python3
"""Import an owner-provided, unchanged DW3 Windows x86-64 installation.

Opaque game data is copied as bytes; nothing is decrypted or unpacked, and
no account, client internals or network protocol is inspected or modified.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile

APPS = Path("/data/apps")
DESTINATION = APPS / "dw3"
IMPORT_ROOTS = [Path("/library/roms"), Path("/media"), Path("/run/media"), Path("/data/imports")]
REQUIRED_FILES = ["DW3 Online.exe", "app/client.jar", "app/dwcofre.dll",
                  "runtime/bin/java.dll", "data/data.pak"]
GAMING_UID = 1000
GAMING_GID = 1000


class ImportError(Exception):
    pass


def guest_guard():
    marker = Path("/etc/surface-image-id")
    if not marker.is_file() or not marker.read_text().strip().startswith("surface-go-"):
        raise ImportError("Import runs only inside the Surface OS image.")
    mounts = Path("/proc/mounts").read_text().splitlines()
    if not any(len(parts := line.split()) > 1 and parts[1] == "/data" for line in mounts):
        raise ImportError("Persistent DATA is not mounted.")
    if APPS.is_symlink() or DESTINATION.is_symlink():
        raise ImportError("Application directories must not be symbolic links.")


def open_source_file(source, relative):
    """Open every component without following links, including renamed parents."""
    relative = Path(relative)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ImportError("An import file has an invalid relative path.")
    directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            os.close(directory)
            directory = following
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ImportError("Client imports must contain ordinary files only.")
        return fd
    finally:
        os.close(directory)


def pe_amd64(source, relative):
    """Read only the standard executable architecture header."""
    with os.fdopen(open_source_file(source, relative), "rb") as reader:
        if reader.read(2) != b"MZ":
            return False
        reader.seek(0x3c)
        pointer = reader.read(4)
        if len(pointer) != 4:
            return False
        offset = struct.unpack("<I", pointer)[0]
        if offset > 4*1024*1024:
            return False
        reader.seek(offset)
        value = reader.read(6)
        return len(value) == 6 and value[:4] == b"PE\0\0" and struct.unpack_from("<H", value, 4)[0] == 0x8664


def inventory(source):
    source = Path(source)
    if source.is_symlink():
        raise ImportError("Select the actual client directory, not a symbolic link.")
    source = source.resolve(strict=True)
    if not source.is_dir() or not any(source.is_relative_to(root.resolve()) for root in IMPORT_ROOTS):
        raise ImportError("Select a client directory from mounted import media or the shared library.")
    roots = [source/"app", source/"runtime", source/"data"]
    if any(parent.is_symlink() or not parent.is_dir() for parent in roots):
        raise ImportError("Client installation directories must be ordinary directories.")
    for name in REQUIRED_FILES:
        path = source/name
        if path.is_symlink() or not path.is_file():
            raise ImportError("The selected directory is missing the official Windows client layout: "+name)
    for name in ["DW3 Online.exe", "app/dwcofre.dll", "runtime/bin/java.dll"]:
        if not pe_amd64(source, name):
            raise ImportError("The imported launcher, native library and Java runtime must be Windows x86-64.")
    files, size = [], 0
    # Only installation assets are imported. Root-level personal logs, saves,
    # unrelated files and account exports are not part of this import list.
    for parent in roots:
        for directory, folders, names in os.walk(parent, followlinks=False):
            for name in folders:
                if (Path(directory)/name).is_symlink():
                    raise ImportError("Linked directories are not accepted in a client import.")
            for name in names:
                path = Path(directory)/name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise ImportError("Client imports must contain ordinary files only.")
                relative = path.relative_to(source)
                files.append((relative, info.st_size))
                size += info.st_size
                if len(files) > 50000 or size > 2*1024**3:
                    raise ImportError("The client exceeds this importer's supported size.")
    for name in ["DW3 Online.exe", "DW3 Online.ico"]:
        path = source/name
        if path.exists():
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ImportError("The client launcher must be an ordinary file.")
            files.append((Path(name), path.stat().st_size))
            size += path.stat().st_size
    if len(files) > 50000 or size > 2*1024**3:
        raise ImportError("The client exceeds this importer's supported size.")
    return source, files, size


def copy_file(source, relative, destination, expected_size):
    fd = open_source_file(source, relative)
    digest = hashlib.sha256()
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
            raise ImportError("An import file changed while preparing the copy.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with os.fdopen(fd, "rb", closefd=False) as reader, destination.open("xb") as writer:
            while chunk := reader.read(1024*1024):
                total += len(chunk)
                if total > expected_size:
                    raise ImportError("An import file grew during the copy.")
                writer.write(chunk)
                digest.update(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if total != expected_size:
            raise ImportError("An import file was truncated during the copy.")
    finally:
        os.close(fd)
    return digest.hexdigest()


def assign_new_installation(staged):
    """Assign only our unpublished tree; its root-owned staging parent is 0700."""
    for _directory, folders, files, directory_fd in os.fwalk(staged, topdown=False, follow_symlinks=False):
        for name in folders:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ImportError("The fresh installation contains an unexpected linked directory.")
        for name in files:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ImportError("The fresh installation contains an unexpected special file.")
                os.fchmod(fd, 0o644)
                os.fchown(fd, GAMING_UID, GAMING_GID)
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fchmod(directory_fd, 0o755)
        os.fchown(directory_fd, GAMING_UID, GAMING_GID)
        os.fsync(directory_fd)


def import_client(source):
    guest_guard()
    source, files, size = inventory(source)
    APPS.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(APPS/".dw3-import.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "w") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ImportError("The import lock must be an ordinary file.")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ImportError("Another DW3 import is running.") from error
        if DESTINATION.exists():
            raise ImportError("An existing DW3 directory was preserved. Launch it or review it before importing again.")
        if shutil.disk_usage(APPS).free < size+512*1024**2:
            raise ImportError("DATA needs enough free space for the client plus 512 MiB for its Wine prefix.")
        with tempfile.TemporaryDirectory(prefix=".dw3-import-", dir=APPS) as temporary:
            staged = Path(temporary)/"payload"
            client = staged/"client"
            client.mkdir(parents=True)
            launcher_hash = None
            for index, (name, length) in enumerate(files, 1):
                value = copy_file(source, name, client/name, length)
                if str(name) == "DW3 Online.exe":
                    launcher_hash = value
                if index % 20 == 0 or index == len(files):
                    print(f"Importing DW3 installation: {index}/{len(files)} files", flush=True)
            receipt = dict(product="Digimon World 3 Online", source="owner-provided Windows x86-64 client",
                           official_url="https://digitalna.com.br/baixar", files=len(files), bytes=size,
                           launcher_sha256=launcher_hash, runtime="surface-wine64 11.0",
                           compatibility="UNTESTED")
            with (staged/"install.json").open("x") as output:
                output.write(json.dumps(receipt, indent=2)+"\n")
                output.flush()
                os.fsync(output.fileno())
            assign_new_installation(staged)
            staged.rename(DESTINATION)
            directory = os.open(APPS, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        print("DW3 client imported unchanged. Launch from HOME; target Wine compatibility still requires testing.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true")
    action.add_argument("--import", dest="source", type=Path)
    args = parser.parse_args()
    if args.status:
        ready = (DESTINATION/"client/DW3 Online.exe").is_file()
        print("IMPORTED" if ready else "IMPORT_REQUIRED")
        return 0 if ready else 1
    os.umask(0o077)
    try:
        import_client(args.source)
    except (ImportError, OSError, ValueError) as error:
        print("DW3 import failed: "+str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
