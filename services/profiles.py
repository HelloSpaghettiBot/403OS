#!/usr/bin/env python3
"""Device-local HOME authentication and Unix application identities.

The command line has no fixture/host override and never accepts a password.
Requests, including passwords, are read only from stdin. Fixture tests import
Store with disposable roots; only guarded production entry points alter /etc.
"""
import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import stat
import sys
import time

ITERATIONS = 600_000
MAX_REQUEST = 8192
NAME = re.compile(r'[a-z][a-z0-9_-]{0,23}\Z')
DATA = Path('/data')
SESSION = Path('/run/surface-profile-session.json')


class Refused(ValueError):
    pass


def wipe_environment(environment):
    allowed = {'LANG', 'LANGUAGE', 'TZ', 'TERM', 'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR',
               'SDL_VIDEO_DRIVER', 'SDL_VIDEODRIVER', 'SDL_RENDER_DRIVER', 'SDL_AUDIODRIVER',
               'SDL_GAMECONTROLLERCONFIG_FILE', 'LIBGL_ALWAYS_SOFTWARE', 'LIBGL_ALWAYS_INDIRECT',
               'GALLIUM_DRIVER', 'LIBVA_DRIVER_NAME', 'MESA_LOADER_DRIVER_OVERRIDE'}
    return {key: value for key, value in environment.items() if key in allowed or key.startswith('LC_')}


def directory(path, owner=None, mode=0o700):
    """Open every component without following links; never adopt an old tree."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('Invalid profile directory')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, part in enumerate(path.parts[1:]):
            final = index == len(path.parts) - 2
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=descriptor)
            except FileNotFoundError:
                if not final:
                    raise Refused('A required profile parent is missing')
                os.mkdir(part, mode, dir_fd=descriptor)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=descriptor)
                if owner is not None:
                    os.fchown(child, *owner)
                # mkdir applies the init service's private umask. These new
                # container directories still need their requested traversal
                # bits so a dropped application UID can reach its own home.
                # Never change or adopt the permissions of an existing tree.
                os.fchmod(child, mode)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        expected = owner[0] if owner is not None else os.geteuid()
        if info.st_uid != expected or info.st_mode & 0o022:
            raise Refused('Profile directory ownership or permissions are unsafe')
        if owner is not None and mode == 0o700 and info.st_mode & 0o077:
            raise Refused('User profile directories must be private')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_private(path, limit=128 * 1024, owner=None):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != (os.geteuid() if owner is None else owner)
                or info.st_mode & 0o077 or info.st_size > limit):
            raise Refused('Unsafe profile state file')
        value = os.read(descriptor, limit + 1)
        if len(value) > limit:
            raise Refused('Profile state is too large')
        return json.loads(value)
    finally:
        os.close(descriptor)


def atomic(path, content, mode=0o600):
    descriptor = directory(Path(path).parent)
    temporary = '.profile-' + secrets.token_hex(12)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=descriptor)
        try:
            data = content if isinstance(content, bytes) else json.dumps(content).encode()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, Path(path).name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=descriptor)
        os.close(descriptor)


def password_hash(password):
    if not isinstance(password, str) or not 10 <= len(password.encode('utf-8')) <= 128 or '\x00' in password:
        raise Refused('Use a new password of 10–128 UTF-8 bytes')
    salt = secrets.token_bytes(32)
    return dict(algorithm='pbkdf2-sha256', iterations=ITERATIONS, salt=salt.hex(),
                digest=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex())


def password_matches(password, record):
    if not isinstance(password, str) or len(password.encode('utf-8')) > 128:
        return False
    if (record.get('algorithm') != 'pbkdf2-sha256' or record.get('iterations') != ITERATIONS or
            not re.fullmatch('[0-9a-f]{64}', record.get('salt', '')) or
            not re.fullmatch('[0-9a-f]{64}', record.get('digest', ''))):
        raise Refused('Unsupported password record')
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), ITERATIONS)
    return hmac.compare_digest(digest, bytes.fromhex(record['digest']))


def account_files(passwd, group, shadow, profiles):
    """Pure reconciliation, used only with fixed target paths in production."""
    rows = [line.split(':') for line in passwd.splitlines() if line]
    groups = [line.split(':') for line in group.splitlines() if line]
    shadows = [line.split(':') for line in shadow.splitlines() if line]
    for profile in profiles:
        name, uid, gid = profile['name'], profile['uid'], profile['gid']
        matching = [row for row in rows if len(row) == 7 and (row[0] == name or row[2] == str(uid))]
        if name == 'root':
            if len(matching) != 1 or matching[0][0] != 'root' or matching[0][2:4] != ['0', '0']:
                raise Refused('The image root account is inconsistent')
        elif matching:
            if len(matching) != 1 or matching[0][:4] != [name, 'x', str(uid), str(gid)] or matching[0][5:] != [profile['home'], '/sbin/nologin']:
                raise Refused('A profile conflicts with an existing Unix account')
        else:
            rows.append([name, 'x', str(uid), str(gid), '403 OS profile', profile['home'], '/sbin/nologin'])
        match = [row for row in groups if len(row) == 4 and (row[0] == name or row[2] == str(gid))]
        if match and (len(match) != 1 or match[0][0] != name or match[0][2] != str(gid)):
            raise Refused('A profile conflicts with an existing Unix group')
        if not match:
            groups.append([name, 'x', str(gid), ''])
        # GUI authentication is separate. No blank or reusable Unix password.
        shadows = [row for row in shadows if row[0] != name]
        shadows.append([name, '!', '0', '0', '99999', '7', '', '', ''])
    return tuple('\n'.join(':'.join(row) for row in items) + '\n' for items in (rows, groups, shadows))


class Store:
    def __init__(self, data, session, provision=False):
        self.data, self.session = Path(data), Path(session)
        self.base = self.data / 'profiles'
        self.database = self.base / 'auth.json'
        self.provision = provision

    def identity(self, record):
        name, uid = record['name'], record['uid']
        base = self.base / name
        return dict(name=name, uid=uid, gid=record['gid'], admin=uid == 0,
                    home=str(self.data / 'home' if uid == 0 else base / 'home'),
                    state=str(self.data / 'home' if uid == 0 else base / 'state'))

    def load(self):
        db = read_private(self.database)
        if db is None:
            return dict(version=1, profiles=[], attempts={})
        if (not isinstance(db, dict) or db.get('version') != 1 or
                not isinstance(db.get('profiles'), list) or len(db['profiles']) > 32):
            raise Refused('Invalid profile database')
        names, uids = set(), set()
        for record in db['profiles']:
            name, uid, gid = record.get('name'), record.get('uid'), record.get('gid')
            if (not isinstance(name, str) or not NAME.fullmatch(name) or name in names or
                    type(uid) is not int or type(gid) is not int or uid != gid or uid in uids or
                    (name == 'root') != (uid == 0) or (uid != 0 and not 2000 <= uid <= 2999)):
                raise Refused('Invalid profile identity')
            names.add(name)
            uids.add(uid)
        if db['profiles'] and db['profiles'][0]['name'] != 'root':
            raise Refused('The first profile must be root')
        return db

    def current(self, db):
        session = read_private(self.session, 4096)
        if session is None:
            return None
        for record in db['profiles']:
            identity = self.identity(record)
            if session == identity:
                return identity
        raise Refused('Invalid authenticated profile session')

    def prepare(self, db):
        if not self.provision:
            return
        require_guest()
        for record in db['profiles']:
            identity = self.identity(record)
            base = self.base / record['name']
            os.close(directory(base, mode=0o711))
            if record['uid']:
                os.close(directory(base / 'state'))
                os.close(directory(base / 'home', owner=(record['uid'], record['gid'])))
                for part in ('config', 'cache', 'share', 'saves', 'browser'):
                    os.close(directory(base / 'home' / part, owner=(record['uid'], record['gid'])))
        # Root is locked even before first-run enrollment, preventing blank
        # console passwords from bypassing the HOME setup flow.
        identities = [self.identity(record) for record in db['profiles']]
        if not identities:
            identities = [dict(name='root', uid=0, gid=0, home='/data/home')]
        etc = Path('/etc')
        values = account_files(*(path.read_text() for path in (etc / 'passwd', etc / 'group', etc / 'shadow')), identities)
        # Fixed, root-owned /etc is deliberately separate from fixture roots.
        for filename, value in zip(('passwd', 'group', 'shadow'), values):
            replace_account(etc / filename, value, 0o600 if filename == 'shadow' else 0o644)

    def request(self, request):
        if not isinstance(request, dict) or request.get('op') not in ('status', 'enroll-root', 'login', 'logout', 'create'):
            raise Refused('Unknown profile request')
        op = request['op']
        fields = {'status': {'op'}, 'logout': {'op'}, 'enroll-root': {'op', 'password', 'confirm'},
                  'login': {'op', 'name', 'password'}, 'create': {'op', 'name', 'password', 'confirm'}}
        if set(request) != fields[op]:
            raise Refused('Unexpected profile request fields')
        os.close(directory(self.base, mode=0o711))
        lock = os.open(self.base / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise Refused('Unsafe profile lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = self.load()
            current = self.current(db)
            if op == 'logout':
                if self.provision:
                    revoke_profile(current)
                with contextlib.suppress(FileNotFoundError):
                    self.session.unlink()
                current = None
            elif op in ('enroll-root', 'create'):
                name = 'root' if op == 'enroll-root' else request['name']
                if op == 'enroll-root' and db['profiles']:
                    raise Refused('Root is already enrolled; sign in instead')
                if op == 'create' and (not current or not current['admin']):
                    raise Refused('Only the signed-in root administrator can add profiles')
                if not isinstance(name, str) or not NAME.fullmatch(name) or (op == 'create' and name == 'root'):
                    raise Refused('Use a lowercase username of 1–24 letters, digits, underscore or hyphen')
                if any(record['name'] == name for record in db['profiles']) or len(db['profiles']) >= 32:
                    raise Refused('Profile already exists or the profile limit is reached')
                if request['password'] != request['confirm']:
                    raise Refused('The two passwords do not match')
                hashed = password_hash(request['password'])
                uid = 0 if op == 'enroll-root' else max([1999] + [row['uid'] for row in db['profiles']]) + 1
                record = dict(name=name, uid=uid, gid=uid, password=hashed)
                db['profiles'].append(record)
                self.prepare(db)
                atomic(self.database, db)
                if op == 'enroll-root':
                    current = self.identity(record)
                    atomic(self.session, current)
            elif op == 'login':
                # A failed attempt must never retain an earlier identity.
                if self.provision:
                    revoke_profile(current)
                with contextlib.suppress(FileNotFoundError):
                    self.session.unlink()
                current = None
                name = request['name']
                if not isinstance(name, str) or not NAME.fullmatch(name):
                    raise Refused('Invalid profile name')
                attempt = db.get('attempts', {}).get(name, {})
                if time.time() < attempt.get('until', 0):
                    raise Refused('Too many attempts; wait up to 30 seconds before trying again')
                record = next((row for row in db['profiles'] if row['name'] == name), None)
                if record is None or not password_matches(request['password'], record['password']):
                    count = min(10, attempt.get('count', 0) + 1)
                    db.setdefault('attempts', {})[name] = dict(count=count, until=time.time() + min(30, 2 ** max(0, count - 3)))
                    atomic(self.database, db)
                    raise Refused('Incorrect password; wait briefly before trying again')
                db.setdefault('attempts', {}).pop(name, None)
                self.prepare(db)
                atomic(self.database, db)
                current = self.identity(record)
                atomic(self.session, current)
            else:
                self.prepare(db)
            return dict(event='profile-status', enrolled=bool(db['profiles']), authenticated=current is not None,
                        current=current, profiles=[dict(name=row['name'], uid=row['uid'], admin=row['uid'] == 0) for row in db['profiles']])
        finally:
            os.close(lock)


def replace_account(path, value, mode):
    # No generic CLI exposes this function. /etc parent and old files must be
    # root-owned regular paths before atomically replacing known account files.
    require_guest()
    if Path(path).parent != Path('/etc') or Path(path).name not in ('passwd', 'group', 'shadow'):
        raise Refused('Only fixed image account files may be reconciled')
    parent = Path(path).parent.lstat()
    info = Path(path).lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022 or
            not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022):
        raise Refused('Unsafe image Unix account files')
    temporary = Path(path).with_name('.surface-account-' + secrets.token_hex(12))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        directory_fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def require_guest():
    info = Path('/etc/surface-image-id').lstat()
    if (os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
            not Path('/etc/surface-image-id').read_text().strip().startswith('surface-go-')):
        raise Refused('Profiles require the privileged service inside the booted OS image')
    fd = directory(DATA, mode=0o755)
    os.close(fd)
    if not any(len(fields := line.split()) > 5 and fields[4] == '/data' and 'rw' in fields[5].split(',')
               for line in Path('/proc/self/mountinfo').read_text().splitlines()):
        raise Refused('Writable persistent DATA must be mounted before profile access')


def link_wayland(source, runtime, gid):
    info = Path(source).lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise Refused('The compositor socket is not trusted')
    fd = directory(runtime, owner=(gid, gid))
    name = '.wayland-' + secrets.token_hex(12)
    try:
        # A same-tmpfs hard link gives this user access to the socket inode
        # without opening the compositor's private runtime directory to users.
        os.link(source, name, dst_dir_fd=fd, follow_symlinks=False)
        os.chown(source, os.geteuid(), gid, follow_symlinks=False)
        os.chmod(source, 0o660, follow_symlinks=False)
        os.replace(name, 'wayland-0', src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=fd)
        os.close(fd)


def dedicated_process(status, uid):
    if not 2000 <= uid <= 2999:
        return False
    for line in status.splitlines():
        if line.startswith('Uid:'):
            return line.split()[1:] == [str(uid)] * 4
    return False


def terminate_profile_processes(uid):
    """Use pidfds, never a racy PID-only kill or any root/system UID."""
    require_guest()
    if not 2000 <= uid <= 2999:
        return
    handles = []
    try:
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            descriptor = None
            try:
                descriptor = os.pidfd_open(int(path.name), 0)
                if dedicated_process((path / 'status').read_text(), uid):
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                    handles.append(descriptor)
                    descriptor = None
            except (ProcessLookupError, FileNotFoundError, PermissionError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        deadline = time.monotonic() + 3
        while handles and time.monotonic() < deadline:
            exited = select.select(handles, [], [], min(0.05, max(0, deadline - time.monotonic())))[0]
            for descriptor in exited:
                handles.remove(descriptor)
                os.close(descriptor)
        for descriptor in handles:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        deadline = time.monotonic() + 1
        while handles and time.monotonic() < deadline:
            for descriptor in select.select(handles, [], [], 0.05)[0]:
                handles.remove(descriptor)
                os.close(descriptor)
        if handles:
            raise Refused('Previous user applications did not stop; restart before signing in')
    finally:
        for descriptor in handles:
            os.close(descriptor)


def revoke_profile(identity):
    require_guest()
    if identity is not None and identity['uid'] != 0 and (not NAME.fullmatch(identity['name']) or not 2000 <= identity['uid'] <= 2999):
        raise Refused('Invalid previous profile identity')
    source = Path('/run/surface/wayland-0')
    runtime = None if identity is None or identity['uid'] == 0 else Path('/run/surface-profiles') / identity['name']
    revoke_socket(source, runtime)
    if identity is not None and identity['uid']:
        terminate_profile_processes(identity['uid'])


def revoke_socket(source, runtime=None, owner_uid=0, owner_gid=0):
    """Path-injected socket fixture; production passes only fixed /run paths."""
    source = Path(source)
    try:
        info = source.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner_uid:
            raise Refused('Cannot secure the compositor socket before sign-in')
        os.chown(source, owner_uid, owner_gid, follow_symlinks=False)
        os.chmod(source, 0o600, follow_symlinks=False)
    except FileNotFoundError:
        pass
    if runtime is None:
        return
    try:
        descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            # Only unlink this fixed directory entry, never follow or adopt
            # a user-controlled file/tree, and never recurse.
            with contextlib.suppress(OSError):
                os.unlink('wayland-0', dir_fd=descriptor)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        pass


def copy_controller_mapping(source, runtime, uid, gid, source_owner=0):
    """A fresh bounded snapshot; only a new descriptor is ever chowned."""
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != source_owner or info.st_mode & 0o022 or info.st_size > 1024 * 1024:
            raise Refused('The shared controller mapping file is unsafe')
        content = os.read(source_fd, 1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise Refused('The shared controller mapping file is too large')
    finally:
        os.close(source_fd)
    descriptor = directory(runtime, owner=(uid, gid))
    temporary = '.mapping-' + secrets.token_hex(12)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=descriptor)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(fd)
            os.fchown(fd, uid, gid)
        finally:
            os.close(fd)
        os.replace(temporary, 'gamecontrollerdb.txt', src_dir_fd=descriptor, dst_dir_fd=descriptor)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=descriptor)
        os.close(descriptor)
    return str(Path(runtime) / 'gamecontrollerdb.txt')


def drop_identity(identity, groups):
    if os.geteuid() == 0:
        os.setgroups(groups)
    os.setresgid(identity['gid'], identity['gid'], identity['gid'])
    os.setresuid(identity['uid'], identity['uid'], identity['uid'])
    if os.getresuid() != (identity['uid'],) * 3 or os.getresgid() != (identity['gid'],) * 3:
        raise Refused('Application privilege drop failed')


def launch_environment(identity, environment):
    home = identity['home']
    result = wipe_environment(environment)
    result.update(HOME=home, USER=identity['name'], LOGNAME=identity['name'],
                  XDG_CONFIG_HOME=home + '/config', XDG_CACHE_HOME=home + '/cache',
                  XDG_DATA_HOME=home + '/share', PATH='/usr/bin:/usr/sbin:/bin:/sbin',
                  SURFACE_PROFILE_NAME=identity['name'], SURFACE_PROFILE_HOME=home)
    if identity['uid'] == 0:
        result.update(XDG_CONFIG_HOME='/data/config', XDG_CACHE_HOME='/data/cache', XDG_DATA_HOME='/data/share')
    return result


def run_application(store, arguments):
    if not arguments or not arguments[0].startswith('/'):
        raise Refused('An absolute packaged application command is required')
    status = store.request({'op': 'status'})
    identity = status['current']
    if identity is None:
        raise Refused('Sign in before launching an application')
    executable = Path(arguments[0]).resolve(strict=True)
    info = executable.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
            not any(str(executable).startswith(prefix) for prefix in ('/usr/', '/bin/', '/sbin/'))):
        raise Refused('Only trusted packaged applications can be started')
    environment = launch_environment(identity, os.environ)
    if identity['uid']:
        runtime_root = Path('/run/surface-profiles')
        os.close(directory(runtime_root, mode=0o711))
        runtime = runtime_root / identity['name']
        link_wayland(Path('/run/surface/wayland-0'), runtime, identity['gid'])
        environment.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY='wayland-0')
        mapping = copy_controller_mapping('/data/config/gamecontrollerdb.txt', runtime, identity['uid'], identity['gid'])
        environment.pop('SDL_GAMECONTROLLERCONFIG_FILE', None)
        if mapping:
            environment['SDL_GAMECONTROLLERCONFIG_FILE'] = mapping
        # Hardware access is explicit; account passwords and other profile homes
        # stay private. Group entries come only from root-owned target /etc.
        import grp
        groups = [identity['gid']]
        for name in ('audio', 'video', 'render', 'gamepad'):
            with contextlib.suppress(KeyError):
                groups.append(grp.getgrnam(name).gr_gid)
        drop_identity(identity, groups)
    os.umask(0o077)
    os.chdir(identity['home'])
    if Path('/sys/class/dmi/id/product_name').read_text().strip() == 'Surface Go OS Disposable Test VM':
        real_uid, effective_uid, saved_uid = os.getresuid()
        real_gid, effective_gid, saved_gid = os.getresgid()
        print(f'SURFACE_PROFILE_APP_IDENTITY name={identity["name"]} real_uid={real_uid} effective_uid={effective_uid} saved_uid={saved_uid} real_gid={real_gid} effective_gid={effective_gid} saved_gid={saved_gid}', flush=True)
    os.execve(arguments[0], arguments, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--request', action='store_true')
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--run', action='store_true')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        require_guest()
        store = Store(DATA, SESSION, provision=True)
        if args.run:
            run_application(store, args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments)
        if args.arguments:
            raise Refused('Unexpected profile command arguments')
        if args.prepare:
            request = {'op': 'status'}
        else:
            raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                raise Refused('Profile request is too large')
            request = json.loads(raw)
        print(json.dumps(store.request(request)), flush=True)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Never include the submitted JSON, command input or password in errors.
        message = str(error) if isinstance(error, Refused) else 'Profile operation failed; check DATA and installed service'
        print(json.dumps(dict(event='profile-error', message=message)), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
