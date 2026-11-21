#!/usr/bin/env python3
"""Explicit first-run download of the unmodified official PokeMMO client.

No network request occurs when importing this module or querying status.
No ROMs, accounts, credentials or client code are supplied by this project.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import struct
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

CLIENT_URL = "https://dl.pokemmo.com/download/PokeMMO-Client.zip"
CLIENT_HOSTS = {"dl.pokemmo.com", "dl.pokemmo.eu", "pokemmo.com"}
JRE_VERSION = "17.0.21.1+2"
JRE_URL = (
    "https://github.com/adoptium/temurin17-binaries/releases/download/"
    "jdk-17.0.21.1%2B2/OpenJDK17U-jre_x64_linux_hotspot_17.0.21.1_2.tar.gz"
)
JRE_SHA256 = "5a092a0bc21de4c3d855480edd7945be9cfe157b38cdb84dac280511893bb009"
JRE_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
APPS = Path("/data/apps")
DESTINATION = APPS / "pokemmo"
IMAGE_MARKER = Path('/etc/surface-image-id')
GIB = 1024 ** 3
ROM_ROOTS = (Path('/library/roms'), Path('/media'), Path('/run/media'), Path('/data/imports'))
# Classification uses cartridge headers, never the owner's chosen filename.
# This is a supported-title filter, not proof of an unmodified retail dump.
ROM_GAMES = {
    'BPR': ('FireRed', 'POKEMON FIRE', 'gba', False),
    'BPE': ('Emerald', 'POKEMON EMER', 'gba', False),
    'IRB': ('Black', 'POKEMON B', 'nds', True),
    'IRA': ('White', 'POKEMON W', 'nds', True),
    'CPU': ('Platinum', 'POKEMON PL', 'nds', False),
    'IPK': ('HeartGold', 'POKEMON HG', 'nds', False),
    'IPG': ('SoulSilver', 'POKEMON SS', 'nds', False),
}


class SetupError(Exception):
    pass


def cancelled(_number, _frame):
    # Let staging-directory/file finally blocks run when HOME cancels a job.
    raise SetupError('Operation cancelled; existing installed files were preserved.')


def trusted_url(url, hosts):
    value = urllib.parse.urlsplit(url)
    if (value.scheme != "https" or value.hostname not in hosts or value.username
            or value.password or value.port not in (None, 443)):
        raise SetupError("The download redirected outside its trusted HTTPS source.")


class TrustedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2

    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, hosts, limit, expected_hash=None):
    trusted_url(url, hosts)
    opener = urllib.request.build_opener(TrustedRedirects(hosts))
    request = urllib.request.Request(url, headers={"User-Agent": "SurfaceGoGamingOS first-run setup"})
    digest = hashlib.sha256()
    size = 0
    started = time.monotonic()
    with opener.open(request, timeout=30) as response, destination.open("xb") as output:
        trusted_url(response.url, hosts)
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            raise SetupError("The download exceeds the supported archive size.")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > limit or time.monotonic() - started > 1200:
                raise SetupError("The download exceeded its size or time limit.")
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    if size < 1024:
        raise SetupError("The download was too small to be an application archive.")
    actual = digest.hexdigest()
    if expected_hash and actual != expected_hash:
        raise SetupError("The Java runtime failed its pinned SHA-256 check.")
    return actual


def checked_member(name):
    value = PurePosixPath(name)
    if "\\" in name or value.is_absolute() or ".." in value.parts or not value.parts:
        raise SetupError("The archive contains an unsafe path.")
    return value


def extract_client(archive, destination):
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 50000 or sum(member.file_size for member in members) > 2 * GIB:
            raise SetupError("The client archive expands beyond the supported size.")
        names = set()
        for member in members:
            normalized = str(checked_member(member.filename))
            if normalized in names or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
                raise SetupError("The client archive has duplicate, linked or encrypted entries.")
            names.add(normalized)
        source.extractall(destination)
    if (destination / "PokeMMO.sh").is_file():
        return destination
    folders = list(destination.iterdir())
    if len(folders) == 1 and folders[0].is_dir() and (folders[0] / "PokeMMO.sh").is_file():
        return folders[0]
    raise SetupError("The official archive no longer contains the expected Linux launcher.")


def extract_runtime(archive, destination):
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 50000 or sum(member.size for member in members) > GIB:
            raise SetupError("The Java archive expands beyond the supported size.")
        for member in members:
            checked_member(member.name)
            if member.isdev() or member.isfifo():
                raise SetupError("The Java archive contains a special device entry.")
        source.extractall(destination, filter="data")
    folders = list(destination.iterdir())
    if len(folders) != 1 or not (folders[0] / "bin/java").is_file():
        raise SetupError("The Java archive has an unexpected layout.")
    return folders[0]


def guest_guard():
    marker = IMAGE_MARKER
    try:
        info = marker.lstat()
        valid = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        valid = valid and marker.read_text().strip().startswith('surface-go-')
    except (OSError, UnicodeError):
        valid = False
    if not valid:
        raise SetupError("Setup runs only inside the Surface OS image.")
    mounts = Path("/proc/mounts").read_text().splitlines()
    if not any(len(fields := line.split()) > 1 and fields[1] == "/data" for line in mounts):
        raise SetupError("The persistent DATA filesystem is not mounted.")
    if APPS.is_symlink() or DESTINATION.is_symlink():
        raise SetupError("The application install path must not be a symbolic link.")


def install():
    guest_guard()
    APPS.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(APPS / ".pokemmo-setup.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SetupError("Another PokeMMO setup is already running.") from error
        if DESTINATION.exists():
            raise SetupError("PokeMMO already has an install directory; existing files were preserved.")
        if shutil.disk_usage(APPS).free < 2 * GIB:
            raise SetupError("PokeMMO setup needs at least 2 GiB free on DATA for downloads and extraction.")
        with tempfile.TemporaryDirectory(prefix=".pokemmo-install-", dir=APPS) as work:
            work = Path(work)
            print("Downloading the official PokeMMO client...", flush=True)
            client_hash = download(CLIENT_URL, work / "client.zip", CLIENT_HOSTS, GIB)
            print("Downloading the pinned Java runtime...", flush=True)
            download(JRE_URL, work / "jre.tar.gz", JRE_HOSTS, 128 * 1024 ** 2, JRE_SHA256)
            payload = work / "payload"
            payload.mkdir()
            print("Checking and extracting application files...", flush=True)
            client = extract_client(work / "client.zip", work / "client-unpacked")
            runtime = extract_runtime(work / "jre.tar.gz", work / "jre-unpacked")
            client.rename(payload / "client")
            runtime.rename(payload / "jre")
            receipt = {"client_url": CLIENT_URL, "client_sha256": client_hash,
                       "jre_version": JRE_VERSION, "jre_sha256": JRE_SHA256,
                       "terms": "https://pokemmo.com/en/tos/"}
            (payload / "install.json").write_text(json.dumps(receipt, indent=2) + "\n")
            payload.rename(DESTINATION)
        print("PokeMMO installed. Launch it from HOME, then sign in and select your own game files.", flush=True)


def no_symlink_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise SetupError('Use an absolute ROM path without parent traversal.')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise SetupError('ROM and client paths must not contain symbolic links.')
    return path


def source_path(path):
    path = no_symlink_path(path)
    if not any(path == root or root in path.parents for root in ROM_ROOTS):
        raise SetupError('ROM sources must be below /library/roms, /media, /run/media or /data/imports.')
    return path


def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value


def rom_header(descriptor, size):
    """Read at most 512 bytes; reject unrelated games and malformed headers."""
    header = os.pread(descriptor, 512, 0)
    if len(header) < 512:
        raise SetupError('The selected file is too small to be a supported cartridge image.')
    gba = header[0xb2] == 0x96 and header[0xb0:0xb2] == b'01'
    code_bytes = header[0xac:0xb0] if gba else header[12:16]
    try:
        code = code_bytes.decode('ascii')
        title = (header[0xa0:0xac] if gba else header[:12]).rstrip(b'\0 ').decode('ascii')
    except UnicodeError as error:
        raise SetupError('The selected file has no supported cartridge header.') from error
    game = ROM_GAMES.get(code[:3])
    if not game or len(code) != 4 or code[3] not in 'EJPDFISKO' or title != game[1] or (game[2] == 'gba') != gba:
        raise SetupError('Only original Black/White 1, FireRed, Emerald, Platinum, HeartGold or SoulSilver headers are accepted.')
    if gba:
        if size != 16 * 1024 ** 2 or header[0xbd] != (-sum(header[0xa0:0xbd]) - 0x19) & 255:
            raise SetupError('The GBA image size or cartridge header checksum is invalid.')
        version = header[0xbc]
    else:
        if header[16:18] != b'01' or header[18] not in (0, 2) or not 64 * 1024 ** 2 <= size <= 512 * 1024 ** 2:
            raise SetupError('The DS image has an invalid maker, platform or size.')
        if crc16(header[:0x15e]) != struct.unpack_from('<H', header, 0x15e)[0]:
            raise SetupError('The DS cartridge header checksum is invalid.')
        used, header_size = struct.unpack_from('<II', header, 0x80)
        if not 512 <= header_size <= 1024 ** 2 or not header_size <= used <= size:
            raise SetupError('The DS cartridge geometry exceeds the selected file.')
        for offset in (0x20, 0x30):
            start, length = struct.unpack_from('<I', header, offset)[0], struct.unpack_from('<I', header, offset + 12)[0]
            if not length or start < header_size or start + length > used:
                raise SetupError('A DS executable region is outside the selected file.')
        version = header[0x1e]
    return dict(title='Pokemon ' + game[0], game=game[0], code=code, system=game[2],
                required=game[3], revision=version, bytes=size,
                compatibility='Header recognized; the official client must validate the ROM.')


def inspect_rom(path, source=True):
    path = source_path(path) if source else no_symlink_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SetupError('A ROM must be a regular file, never a device or pipe.')
        result = rom_header(descriptor, info.st_size)
        result['path'] = str(path)
        return result
    finally:
        os.close(descriptor)


def scan_roms(path):
    path = source_path(path)
    if not path.is_dir():
        raise SetupError('Choose a mounted folder to scan for owner-provided ROMs.')
    found, warnings, seen = [], [], set()
    started, checked = time.monotonic(), 0
    for folder, directories, names in os.walk(path, followlinks=False):
        directories[:] = sorted(name for name in directories if name.lower() not in {'bios', 'firmware', 'keys', 'lost+found'} and not (Path(folder) / name).is_symlink())
        for name in sorted(names):
            checked += 1
            if checked > 20000 or time.monotonic() - started > 30:
                warnings.append('Scan limit reached; choose a more specific source folder.')
                return dict(event='rom-scan', roms=found, warnings=warnings, warning_count=len(warnings))
            item = Path(folder) / name
            if item.suffix.lower() not in {'.gba', '.nds'} or item.is_symlink():
                continue
            try:
                info = item.stat()
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                found.append(inspect_rom(item))
                if len(found) >= 256:
                    warnings.append('ROM result limit reached; choose a more specific source folder.')
                    return dict(event='rom-scan', roms=found, warnings=warnings, warning_count=len(warnings))
            except (OSError, SetupError) as error:
                if len(warnings) < 16:
                    warnings.append(item.name + ': ' + str(error))
    return dict(event='rom-scan', roms=found, warnings=warnings, warning_count=len(warnings))


def rom_status():
    directory = DESTINATION / 'client/roms'
    found, warnings = [], []
    if directory.is_dir() and not directory.is_symlink():
        for item in sorted(directory.iterdir()):
            if item.suffix.lower() not in {'.gba', '.nds'}:
                continue
            try:
                found.append(inspect_rom(item, source=False))
            except (OSError, SetupError) as error:
                if len(warnings) < 8:
                    warnings.append(item.name + ': ' + str(error))
            if len(found) >= 64:
                break
    return dict(event='rom-status', roms=found, warnings=warnings, warning_count=len(warnings),
                black_white_present=any(item['required'] for item in found))


def hash_descriptor(descriptor, size, phase, output=None):
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest, done, previous = hashlib.sha256(), 0, -1
    while done < size:
        chunk = os.read(descriptor, min(1024 * 1024, size - done))
        if not chunk:
            raise SetupError('ROM source or staged copy ended unexpectedly.')
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
        done += len(chunk)
        percent = done * 100 // size
        if percent != previous:
            print(f'{phase}: {percent}%', flush=True)
            previous = percent
    return digest.hexdigest()


def import_rom(path):
    guest_guard()  # Before creating directories, lock files or writable opens.
    path = source_path(path)
    directory = no_symlink_path(DESTINATION / 'client/roms')
    if not directory.is_dir() or not (DESTINATION / 'client/PokeMMO.sh').is_file():
        raise SetupError('Install the official client first; its verified roms directory is required.')
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SetupError('Only a regular owner-provided ROM file can be imported.')
        info = rom_header(descriptor, before.st_size)
        if shutil.disk_usage(directory).free < before.st_size + 16 * 1024 ** 2:
            raise SetupError('DATA has insufficient free space for this ROM copy.')
        lock_fd = os.open(DESTINATION / '.rom-import.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SetupError('Another ROM import is already running.') from error
            checksum = hash_descriptor(descriptor, before.st_size, 'Verifying selected ROM')
            target = directory / f'{info["game"]}-{info["code"]}-rev{info["revision"]}.{info["system"]}'
            if target.exists() or target.is_symlink():
                old = no_symlink_path(target)
                if not stat.S_ISREG(old.lstat().st_mode):
                    raise SetupError('The existing ROM path is not a regular file. It was preserved.')
                old_fd = os.open(old, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(old_fd, 'rb') as existing:
                    existing_info = os.fstat(existing.fileno())
                    same = stat.S_ISREG(existing_info.st_mode) and existing_info.st_size == before.st_size
                    same = same and hash_descriptor(existing.fileno(), before.st_size, 'Checking existing ROM') == checksum
                if same:
                    print('This exact ROM is already imported. Existing files were preserved.', flush=True)
                    return
                raise SetupError('A different ROM already occupies this game/code/revision. Existing files were preserved; use official Client Management to review it.')
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix='.rom-import-', dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    os.fchmod(output.fileno(), 0o600)
                    copied = hash_descriptor(descriptor, before.st_size, 'Copying selected ROM', output)
                    output.flush()
                    os.fsync(output.fileno())
                after = os.fstat(descriptor)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or copied != checksum:
                    raise SetupError('The source changed during import. No ROM was installed.')
                with temporary.open('rb') as copy:
                    if hash_descriptor(copy.fileno(), before.st_size, 'Verifying ROM copy') != checksum:
                        raise SetupError('Staged ROM checksum verification failed.')
                # A hard link is an atomic no-replace publish on the DATA
                # filesystem. A concurrent/new owner file can never be erased.
                os.link(temporary, target, follow_symlinks=False)
                temporary.unlink()
                temporary = None
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            print('Imported ' + info['title'] + ' (' + info['code'] + '), SHA256 ' + checksum + '. The official client must validate compatibility.', flush=True)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the official PokeMMO client after an explicit HOME action.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="download and install now; requires the guest DATA mount")
    action.add_argument("--status", action="store_true", help="report install state without downloading")
    action.add_argument('--scan-roms', metavar='FOLDER', help='read cartridge headers in an approved owner source folder')
    action.add_argument('--rom-status', action='store_true', help='report locally imported ROM headers without network access')
    action.add_argument('--import-rom', metavar='FILE', help='explicitly copy one approved owner ROM into the official client roms directory')
    args = parser.parse_args(argv)
    if args.status:
        ready = (DESTINATION / "client/PokeMMO.sh").is_file() and os.access(DESTINATION / "jre/bin/java", os.X_OK)
        print("INSTALLED" if ready else "NOT_INSTALLED")
        return 0 if ready else 1
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    os.umask(0o077)
    try:
        if args.scan_roms:
            print(json.dumps(scan_roms(args.scan_roms), ensure_ascii=False), flush=True)
        elif args.rom_status:
            print(json.dumps(rom_status(), ensure_ascii=False), flush=True)
        elif args.import_rom:
            import_rom(args.import_rom)
        else:
            install()
    except (SetupError, OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
        print("PokeMMO setup failed: " + str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
