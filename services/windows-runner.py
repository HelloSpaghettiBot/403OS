#!/usr/bin/env python3
"""Explicit, unprivileged Wine/UMU runner for owner-imported DW3 files.

Status is read only. Setup is the only action which allows UMU to download
Proton/the Steam runtime; it never happens at boot or as a launch side effect.
There is no host-guard override or arbitrary executable/shell CLI.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

MARKER = Path('/etc/surface-image-id')
DATA = Path('/data')
WINDOWS = DATA / 'windows'
RUNTIME = Path('/run/surface-games')
CLIENT = DATA / 'apps/dw3/client'
WINE = Path('/usr/bin/wine')
UMU = Path('/usr/bin/umu-run')
MINIMUM_SETUP_FREE = 6 * 1024 ** 3
RUNTIMES = {'1391110': 'steamrt2', '1628350': 'steamrt3', '4183110': 'steamrt4'}
EXTERNAL_ROOTS = (Path('/media'), Path('/run/media'))
SDL_MAPPING_SOURCE = DATA / 'config/gamecontrollerdb.txt'
SDL_MAPPING_NAME = 'gamecontrollerdb.txt'
SDL_MAPPING_LIMIT = 1024 * 1024
SDL_MAPPING_OWNER = 0
REQUIRED_CLIENT = ('DW3 Online.exe', 'app/client.jar', 'app/dwcofre.dll',
                   'runtime/bin/java.dll', 'data/data.pak')


class Refused(ValueError):
    pass


def emit(event, **values):
    print(json.dumps(dict(event=event, **values)), flush=True)


def no_symlink_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('An absolute path without parent traversal is required')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise Refused('The Windows home, prefix and client paths must not be symbolic links')
    return path


def require_guest():
    try:
        info = MARKER.lstat()
        valid = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        valid = valid and MARKER.read_text().strip().startswith('surface-go-')
    except (OSError, UnicodeError):
        valid = False
    if not valid:
        raise Refused('Windows runners are available only inside the booted Surface OS image')
    no_symlink_path(DATA)
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    if not any(len(fields := line.split()) > 5 and fields[4] == str(DATA) for line in mounts):
        raise Refused('The persistent DATA filesystem must be mounted')


def gaming_account():
    try:
        user = pwd.getpwnam('gaming')
    except KeyError as error:
        raise Refused('The image is missing its unprivileged gaming account') from error
    if user.pw_uid != 1000 or user.pw_gid != 1000:
        raise Refused('The gaming account has an unexpected UID/GID; expected 1000:1000')
    if os.geteuid() not in (0, user.pw_uid):
        raise Refused('Run this image service as root or the gaming account')
    return user


def private_directory(path, uid, gid):
    """Create/open each component relative to no-follow directory descriptors.

    Only the final known service directory is chmod/chowned. No existing tree
    is recursively adopted while root, including the imported client tree.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('Invalid private service directory')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            except FileNotFoundError:
                if not last:
                    raise Refused('A required service parent directory is missing')
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid not in (0, uid):
            raise Refused('The private service directory belongs to an unexpected user')
        if os.geteuid() == 0:
            os.fchown(descriptor, uid, gid)
        elif info.st_uid != uid or info.st_gid != gid:
            raise Refused('The private service directory needs image-service ownership repair')
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def prepare_user():
    user = gaming_account()
    private_directory(WINDOWS, user.pw_uid, user.pw_gid)
    private_directory(RUNTIME, user.pw_uid, user.pw_gid)
    copy_controller_mapping(user.pw_uid, user.pw_gid)
    if os.geteuid() == 0:
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    if os.getuid() != 1000 or os.geteuid() != 1000 or os.getgid() != 1000 or os.getegid() != 1000:
        raise Refused('Failed to permanently drop privileges to the gaming account')
    os.umask(0o077)
    return user


def directory_descriptor(path):
    """Anchor every path component without following symbolic links."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('Invalid service directory path')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def copy_controller_mapping(uid, gid):
    """Copy only the image's known private SDL database before dropping root.

    Never use an inherited environment variable to select a privileged read.
    Publishing relative to held directory descriptors replaces a destination
    symlink itself; it cannot write through a user-created symlink/hard link.
    """
    require_guest()
    if os.geteuid() != 0:
        # Direct invocation as gaming may use the last root-prepared private
        # copy; it cannot read or refresh root's private mapping database.
        return False
    destination = directory_descriptor(WINDOWS)
    source_parent = source = temporary_fd = None
    temporary = None
    try:
        destination_info = os.fstat(destination)
        if destination_info.st_uid != uid or stat.S_IMODE(destination_info.st_mode) != 0o700:
            raise Refused('The Windows mapping destination is not the private gaming directory')
        try:
            source_parent = directory_descriptor(SDL_MAPPING_SOURCE.parent)
            parent_info = os.fstat(source_parent)
            if parent_info.st_uid != SDL_MAPPING_OWNER or parent_info.st_mode & 0o022:
                raise Refused('The known controller mapping directory is not trusted')
            source = os.open(SDL_MAPPING_SOURCE.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent)
        except FileNotFoundError:
            # Resetting/removing the shell mapping must not leave a stale
            # mapping active for Windows applications on the next launch.
            try:
                os.unlink(SDL_MAPPING_NAME, dir_fd=destination)
                os.fsync(destination)
            except FileNotFoundError:
                pass
            return False
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != SDL_MAPPING_OWNER or before.st_mode & 0o022 or before.st_size > SDL_MAPPING_LIMIT:
            raise Refused('The known controller mapping database is not a bounded trusted regular file')
        chunks, size = [], 0
        while size <= SDL_MAPPING_LIMIT:
            chunk = os.read(source, min(65536, SDL_MAPPING_LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > SDL_MAPPING_LIMIT:
            raise Refused('The controller mapping database exceeded its size limit')
        payload = b''.join(chunks)
        try:
            payload.decode('utf-8', errors='strict')
        except UnicodeError as error:
            raise Refused('The controller mapping database is not valid UTF-8') from error
        if b'\0' in payload:
            raise Refused('The controller mapping database contains invalid null bytes')
        temporary = '.controller-mapping-' + secrets.token_hex(16)
        temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=destination)
        os.fchown(temporary_fd, uid, gid)
        os.fchmod(temporary_fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise Refused('The controller mapping copy did not make progress')
            view = view[written:]
        os.fsync(temporary_fd)
        after = os.fstat(source)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Refused('The controller mapping changed while being copied; retry the launch')
        os.rename(temporary, SDL_MAPPING_NAME, src_dir_fd=destination, dst_dir_fd=destination)
        temporary = None
        os.fsync(destination)
        return True
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary is not None:
            os.unlink(temporary, dir_fd=destination)
        if source is not None:
            os.close(source)
        if source_parent is not None:
            os.close(source_parent)
        os.close(destination)


def private_controller_mapping():
    path = WINDOWS / SDL_MAPPING_NAME
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and info.st_uid == 1000 and info.st_gid == 1000 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= SDL_MAPPING_LIMIT:
                return str(path)
        finally:
            os.close(descriptor)
    except OSError:
        pass
    return None


def read_small(path, limit=65536):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return ''
            data = source.read(limit + 1)
        return data.decode('utf-8', errors='replace') if len(data) <= limit else ''
    except OSError:
        return ''


def make_apps_traversable():
    """Allow gaming traversal of the known root-owned parent, never its trees."""
    if os.geteuid() != 0:
        return
    parent = os.open(DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        descriptor = os.open('apps', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise Refused('The application parent is not a private root-owned service directory')
            os.fchmod(descriptor, 0o711)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def manifest(path):
    data = read_small(path / 'toolmanifest.vdf')
    match = re.search(r'"require_tool_appid"\s+"?(\d+)"?', data)
    runtime = RUNTIMES.get(match[1] if match else '')
    if not runtime:
        raise Refused('The installed Proton tool requires an unsupported or unknown Steam runtime')
    return runtime, hashlib.sha256(data.encode()).hexdigest()


def installed_protons():
    result = []
    for folder in (WINDOWS / '.local/share/Steam/compatibilitytools.d', WINDOWS / '.local/share/umu/compatibilitytools'):
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if not item.name.startswith('UMU-Proton'):
                continue
            try:
                real = item.resolve(strict=True)
                if WINDOWS.resolve() not in real.parents or not (real / 'proton').is_file():
                    continue
                runtime, digest = manifest(real)
                if real not in [value['path'] for value in result]:
                    result.append(dict(path=real, runtime=runtime, manifest_sha256=digest))
            except (OSError, Refused):
                continue
    result.sort(key=lambda item: tuple(int(value) for value in re.findall(r'\d+', item['path'].name)), reverse=True)
    return result


def runtime_present(name):
    directory = WINDOWS / '.local/share/umu' / name
    return (directory / '.installed.ok').is_file() and (directory / 'toolmanifest.vdf').is_file()


def proton_state():
    """A successful fixed cmd probe plus matching installed artifacts is needed."""
    try:
        data = json.loads(read_small(WINDOWS / 'proton-setup.json'))
        path = Path(data['proton_path'])
        if data.get('probe_exit') != 0 or data.get('gameplay_verified') is not False:
            return None
        if not path.is_absolute() or WINDOWS.resolve() not in path.resolve(strict=True).parents:
            return None
        runtime, digest = manifest(path)
        if digest != data['manifest_sha256'] or not runtime_present(runtime):
            return None
        if not (path / 'proton').is_file() or not (WINDOWS / 'dw3-proton/pfx/system.reg').is_file():
            return None
        return dict(proton_path=str(path), runtime=runtime,
                    manifest_sha256=digest, setup_time=data.get('setup_time', ''),
                    version=data.get('version', 'unknown'))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def status():
    try:
        free = shutil.disk_usage(DATA).free
    except OSError:
        free = 0
    state = proton_state()
    return dict(event='windows-status', wine_installed=os.access(WINE, os.X_OK),
                umu_installed=os.access(UMU, os.X_OK), proton_ready=bool(state),
                proton=state, free_bytes=free, minimum_setup_free_bytes=MINIMUM_SETUP_FREE,
                dw3_imported=all((CLIENT / name).is_file() for name in REQUIRED_CLIENT),
                gameplay_verified=False)


def environment(runner, setup=False):
    # Do not inherit arbitrary LD_PRELOAD, PYTHONPATH, Wine/Proton or Steam
    # overrides from the root UI. Display variables are the session interface.
    result = {'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'HOME': str(WINDOWS),
              'USER': 'gaming', 'LOGNAME': 'gaming', 'LANG': 'C.UTF-8',
              'XDG_RUNTIME_DIR': str(RUNTIME),
              'XDG_DATA_HOME': str(WINDOWS / '.local/share'),
              'XDG_CACHE_HOME': str(WINDOWS / '.cache'),
              'XDG_CONFIG_HOME': str(WINDOWS / '.config'),
              'TMPDIR': str(WINDOWS / 'tmp'), 'WINEDEBUG': '-all',
              'WINEDLLOVERRIDES': 'mscoree,mshtml=',
              'WINEPREFIX': str(WINDOWS / ('dw3-proton' if runner == 'proton' else 'dw3-wine'))}
    for key in ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY'):
        if os.environ.get(key):
            result[key] = os.environ[key]
    # The default path is Xwayland/OpenGL. A distinct XDG runtime directory
    # deliberately avoids handing the game a root session's private directory.
    result.pop('WAYLAND_DISPLAY', None)
    mapping = private_controller_mapping()
    if mapping:
        result['SDL_GAMECONTROLLERCONFIG_FILE'] = mapping
    if runner == 'proton':
        result.update(GAMEID='umu-default', STORE='none', PROTON_USE_WINED3D='1',
                      PROTONFIXES_DISABLE='1', UMU_HTTP_TIMEOUT='15', UMU_HTTP_RETRIES='2',
                      UMU_RUNTIME_UPDATE='1' if setup else '0',
                      PROTONPATH='UMU-Proton' if setup else '')
    else:
        result['WINEARCH'] = 'win64'
    return result


def approved_executable(path, runner, mountinfo=None):
    raw = str(path)
    try:
        raw.encode('utf-8', errors='strict')
    except UnicodeError as error:
        raise Refused('The executable path is not valid UTF-8') from error
    if len(raw) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise Refused('Executable paths must not contain control characters')
    path = no_symlink_path(Path(raw))
    local = WINDOWS / 'apps'
    if local not in path.parents:
        if not any(root in path.parents for root in EXTERNAL_ROOTS):
            raise Refused('Select an executable on read-only mounted media or below /data/windows/apps')
        mounts = []
        for line in (mountinfo if mountinfo is not None else Path('/proc/self/mountinfo').read_text()).splitlines():
            fields = line.split()
            if len(fields) <= 5:
                continue
            point = Path(re.sub(r'\\([0-7]{3})', lambda value: chr(int(value[1], 8)), fields[4]))
            if point in path.parents:
                mounts.append((len(str(point)), set(fields[5].split(','))))
        if not mounts or 'ro' not in max(mounts, key=lambda item: item[0])[1]:
            raise Refused('External Windows executable media must be mounted read only')
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 256:
            raise Refused('Select a regular Windows PE executable')
        dos = os.pread(descriptor, 64, 0)
        if len(dos) != 64:
            raise Refused('The selected executable changed while its header was read')
        offset = struct.unpack_from('<I', dos, 0x3c)[0]
        if dos[:2] != b'MZ' or offset < 64 or offset > min(info.st_size - 26, 1024 ** 2):
            raise Refused('The file has an invalid DOS/PE header')
        header = os.pread(descriptor, 26, offset)
        if len(header) != 26 or header[:4] != b'PE\0\0':
            raise Refused('The file has no Windows PE signature')
        machine, sections = struct.unpack_from('<HH', header, 4)
        optional_size, characteristics = struct.unpack_from('<HH', header, 20)
        magic = struct.unpack_from('<H', header, 24)[0]
        if not 1 <= sections <= 96 or optional_size < 2 or offset + 24 + optional_size + sections * 40 > info.st_size or not characteristics & 2 or characteristics & 0x2000:
            raise Refused('The PE executable geometry is invalid or describes a DLL')
        if machine == 0x8664 and magic == 0x20b:
            architecture = 'AMD64'
        elif runner == 'proton' and machine == 0x14c and magic == 0x10b:
            architecture = 'I386'
        else:
            raise Refused('Wine64 requires an AMD64 PE executable; Proton accepts AMD64/I386 only')
        identity = hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:24]
        return dict(path=str(path), id=identity, architecture=architecture,
                    prefix=str(WINDOWS / 'prefixes' / (runner + '-' + identity)))
    finally:
        os.close(descriptor)


def registered_executables():
    try:
        value = json.loads(read_small(WINDOWS / 'apps/registry.json'))
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:128]:
        if not isinstance(item, dict) or item.get('runner') not in ('wine', 'proton') or not isinstance(item.get('path'), str) or not isinstance(item.get('title'), str):
            continue
        if len(item['title']) > 100 or any(ord(character) < 32 or ord(character) == 127 for character in item['title']):
            continue
        result.append({key: item[key] for key in ('id', 'path', 'title', 'runner') if key in item})
    return result


def register_executable(path, title, runner):
    require_guest()
    if not title or len(title) > 100 or any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise Refused('Use a 1–100 character application title without control characters')
    approved_executable(path, runner)
    prepare_user()
    private_directory(WINDOWS / 'apps', 1000, 1000)
    item = approved_executable(path, runner)
    registry = [value for value in registered_executables() if value.get('id') != item['id']]
    if len(registry) >= 128:
        raise Refused('The Windows application registry has reached its 128-entry limit')
    registry.append(dict(id=item['id'], path=item['path'], title=title, runner=runner))
    with tempfile.NamedTemporaryFile(mode='w', prefix='.registry-', dir=WINDOWS / 'apps', delete=False) as output:
        temporary = Path(output.name)
        json.dump(registry, output, ensure_ascii=False, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(WINDOWS / 'apps/registry.json')
    emit('registered', title=title, path=item['path'], id=item['id'], runner=runner,
         message='Application reference saved; executable and game compatibility remain unverified')


def launch_executable(path, runner):
    require_guest()
    program = WINE if runner == 'wine' else UMU
    if not os.access(program, os.X_OK):
        raise Refused('The selected Windows runner is not installed')
    state = proton_state() if runner == 'proton' else None
    if runner == 'proton' and not state:
        raise Refused('Select explicit Proton runtime setup before launching a Windows application')
    approved_executable(path, runner)
    prepare_user()
    prepare_directories(runner)
    item = approved_executable(path, runner)
    private_directory(WINDOWS / 'prefixes', 1000, 1000)
    private_directory(Path(item['prefix']), 1000, 1000)
    env = environment(runner)
    env['WINEPREFIX'] = item['prefix']
    if not env.get('DISPLAY'):
        raise Refused('Xwayland DISPLAY is unavailable')
    if runner == 'proton':
        env['PROTONPATH'] = state['proton_path']
    return supervise([str(program), item['path']], env, Path(item['path']).parent)


def prepare_directories(runner, prefix=None):
    for path in (WINDOWS / 'tmp', WINDOWS / '.cache', WINDOWS / '.config',
                 WINDOWS / '.local', WINDOWS / '.local/share',
                 prefix or WINDOWS / ('dw3-proton' if runner == 'proton' else 'dw3-wine')):
        private_directory(path, 1000, 1000)


def save_receipt(state):
    value = dict(proton_path=str(state['path']), runtime=state['runtime'],
                 manifest_sha256=state['manifest_sha256'],
                 version=read_small(state['path'] / 'version', 4096).strip(),
                 setup_time=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                 source='Official UMU-Proton and Steam runtime selected by UMU at explicit setup time; mutable upstream download',
                 probe_exit=0, gameplay_verified=False)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.proton-setup-', dir=WINDOWS, delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(WINDOWS / 'proton-setup.json')


def process_records(proc=Path('/proc')):
    """Read bounded PID/parent/start-time identities; never select by UID alone."""
    records = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        if len(records) >= 8192:
            break
        try:
            value = (entry / 'stat').read_text()
            fields = value[value.rfind(')') + 2:].split()
            if len(fields) >= 20:
                records[int(entry.name)] = dict(parent=int(fields[1]), start=int(fields[19]), uid=entry.stat().st_uid)
        except (OSError, ValueError):
            continue
    return records


def collect_descendants(records, owner_pid, tracked, uid=1000):
    """Include reparented subreaper children, reject reused PID identities."""
    live = {pid: start for pid, start in tracked.items()
            if pid in records and records[pid]['start'] == start and records[pid]['uid'] == uid}
    ancestors = {owner_pid, *live}
    for _ in range(32):
        added = False
        for pid, value in records.items():
            if pid != owner_pid and value['uid'] == uid and value['parent'] in ancestors and pid not in live:
                if len(live) >= 512:
                    return live
                live[pid] = value['start']
                ancestors.add(pid)
                added = True
        if not added:
            break
    return live


def signal_tracked(tracked, number):
    # Re-read start times and UIDs immediately before every kill. A recycled PID
    # or an unrelated process of the gaming user is never a target.
    records = process_records()
    for pid, start in list(tracked.items()):
        value = records.get(pid)
        if not value or value['uid'] != 1000 or value['start'] != start:
            continue
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            pass


def reap_children():
    for _ in range(512):
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                return
        except ChildProcessError:
            return


def cleanup_descendants(tracked):
    started = time.monotonic()
    terminated, killed = set(), False
    while time.monotonic() - started < 3.3:
        tracked = collect_descendants(process_records(), os.getpid(), tracked)
        if not tracked:
            reap_children()
            return
        pending = {pid: stamp for pid, stamp in tracked.items() if (pid, stamp) not in terminated}
        if pending:
            signal_tracked(pending, signal.SIGTERM)
            terminated.update(pending.items())
        if time.monotonic() - started >= 2 and not killed:
            signal_tracked(tracked, signal.SIGKILL)
            killed = True
        elif killed and pending:
            signal_tracked(pending, signal.SIGKILL)
        reap_children()
        time.sleep(0.05)
    signal_tracked(tracked, signal.SIGKILL)
    reap_children()


def supervise(arguments, env, cwd, timeout=None, output=None):
    """Supervise only our descendants, including UMU's detached sessions.

    The child subreaper retains orphaned Wine/pressure-vessel descendants.
    TERM cleanup completes within 3.3 seconds, before HOME's 5-second KILL.
    """
    if os.getuid() != 1000 or os.geteuid() != 1000:
        raise Refused('Windows process supervision requires the unprivileged gaming identity')
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise Refused('The kernel does not support child subreaper supervision')
    stopping = []
    def request_stop(number, _frame):
        stopping.append(number)
    handlers = {number: signal.signal(number, request_stop) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    process = None
    selector = selectors.DefaultSelector()
    tracked = {}
    code = 1
    started = time.monotonic()
    try:
        process = subprocess.Popen(arguments, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if output else None,
                                   stderr=subprocess.STDOUT if output else None)
        if process.stdout is not None:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            tracked = collect_descendants(process_records(), os.getpid(), tracked)
            if stopping:
                code = 128 + stopping[0]
                break
            if timeout is not None and time.monotonic() - started > timeout:
                code = 124
                break
            if output and selector.get_map():
                for key, _ in selector.select(0):
                    try:
                        chunk = os.read(key.fd, 4096)
                    except BlockingIOError:
                        continue
                    if chunk:
                        output(chunk)
                    else:
                        selector.unregister(key.fileobj)
            result = process.poll()
            if result is not None:
                code = result
                break
            time.sleep(0.05)
    finally:
        cleanup_descendants(tracked)
        if process is not None and process.stdout is not None:
            # Bounded final drain; a broken child cannot stall HOME on stdout.
            if output:
                for _ in range(32):
                    try:
                        chunk = os.read(process.stdout.fileno(), 4096)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    output(chunk)
            process.stdout.close()
        selector.close()
        if process is not None:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        for number, handler in handlers.items():
            signal.signal(number, handler)
        libc.prctl(36, previous.value, 0, 0, 0)
    return code


def setup_proton():
    require_guest()  # Before directory creation, ownership changes or execution.
    if not os.access(UMU, os.X_OK):
        raise Refused('The pinned UMU launcher is not installed in this image')
    free = shutil.disk_usage(DATA).free
    if free < MINIMUM_SETUP_FREE:
        raise Refused(f'Proton/Steam runtime setup requires at least {MINIMUM_SETUP_FREE} bytes free on DATA; only {free} bytes are available. No download started')
    prepare_user()  # All runtime/prefix/log writes below happen as gaming.
    prepare_directories('proton')
    env = environment('proton', setup=True)
    if not env.get('DISPLAY'):
        raise Refused('Xwayland DISPLAY is unavailable; no runtime setup was started')
    emit('progress', message='Explicit UMU setup: downloading the selected Proton and Steam runtime, then testing a Windows command')
    # A built-in Windows command initializes the prefix without starting a game
    # or importing third-party content. All arguments are fixed literal argv.
    argv = [str(UMU), 'C:\\windows\\system32\\cmd.exe', '/c', 'exit', '0']
    log_path = WINDOWS / 'proton-setup.log'
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(log_fd, 'wb') as log:
        written = 0
        def capture(chunk):
            nonlocal written
            if written < 2 * 1024 ** 2:
                part = chunk[:2 * 1024 ** 2 - written]
                log.write(part)
                written += len(part)
            for line in chunk.decode('utf-8', errors='replace').replace('\r', '\n').splitlines():
                line = ''.join(character for character in line if ord(character) >= 32)
                if line:
                    emit('progress', message=line[-400:])
        code = supervise(argv, env, WINDOWS, timeout=1200, output=capture)
        log.flush()
        os.fsync(log.fileno())
    if code:
        raise Refused(f'UMU setup Windows probe failed (exit {code}); see /data/windows/proton-setup.log. Gameplay remains unverified')
    candidates = installed_protons()
    if not candidates or not runtime_present(candidates[0]['runtime']) or not (WINDOWS / 'dw3-proton/pfx/system.reg').is_file():
        raise Refused('UMU returned success without a complete Proton/runtime/prefix installation')
    save_receipt(candidates[0])
    emit('complete', message='Proton and Steam runtime installed; Windows command probe succeeded. DW3/gameplay compatibility is still unverified', **proton_state())


def launch_dw3(runner):
    require_guest()
    program = WINE if runner == 'wine' else UMU
    if not os.access(program, os.X_OK):
        raise Refused('The selected Windows runner is not installed in this image')
    state = proton_state() if runner == 'proton' else None
    if runner == 'proton' and not state:
        raise Refused('Select explicit Proton runtime setup first; launching a game never performs first-run setup')
    make_apps_traversable()
    prepare_user()
    prepare_directories(runner)
    no_symlink_path(CLIENT)
    for name in REQUIRED_CLIENT:
        path = no_symlink_path(CLIENT / name)
        if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.R_OK):
            raise Refused('DW3 is not readable by gaming; re-import the owner client through HOME')
    env = environment(runner)
    if not env.get('DISPLAY'):
        raise Refused('Xwayland DISPLAY is unavailable; Windows/OpenGL launch cannot start')
    if runner == 'proton':
        env['PROTONPATH'] = state['proton_path']
    return supervise([str(program), str(CLIENT / 'DW3 Online.exe')], env, CLIENT)


def probe_wine():
    require_guest()
    if not os.access(WINE, os.X_OK):
        raise Refused('Wine64 is not installed in this image')
    prepare_user()
    prefix = WINDOWS / 'wine-probe'
    prepare_directories('wine', prefix)
    env = environment('wine')
    env['WINEPREFIX'] = str(prefix)
    if not env.get('DISPLAY'):
        raise Refused('Xwayland DISPLAY is required for the fixed Wine probe')
    code = supervise([str(WINE), 'C:\\windows\\system32\\cmd.exe', '/c', 'exit', '0'], env, WINDOWS, timeout=60)
    emit('wine-probe', exit=code, success=code == 0, gameplay_verified=False,
         message='Fixed Windows command succeeded; gameplay remains unverified' if code == 0 else 'Fixed Windows command failed; gameplay remains unverified')
    return code if code >= 0 else 128 - code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--status', action='store_true')
    action.add_argument('--setup-proton', action='store_true')
    action.add_argument('--launch-dw3', action='store_true')
    action.add_argument('--launch-exe', metavar='PATH')
    action.add_argument('--register-exe', metavar='PATH')
    action.add_argument('--list-exes', action='store_true')
    action.add_argument('--probe-wine', action='store_true')
    parser.add_argument('--title', default='')
    parser.add_argument('--runner', choices=('wine', 'proton'), default='wine')
    args = parser.parse_args(argv)
    try:
        if args.status:
            emit(**status())
        elif args.list_exes:
            emit('windows-apps', apps=registered_executables())
        elif args.setup_proton:
            setup_proton()
        elif args.register_exe:
            register_executable(args.register_exe, args.title, args.runner)
        elif args.launch_exe:
            return launch_executable(args.launch_exe, args.runner)
        elif args.probe_wine:
            return probe_wine()
        else:
            return launch_dw3(args.runner)
        return 0
    except (OSError, Refused, ValueError, subprocess.SubprocessError) as error:
        emit('error', message=str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
