#!/usr/bin/python3
"""Suspend 403 OS after a short physical power-button tap."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import time

MARKER = Path('/etc/surface-image-id')
MOUNTS = Path('/proc/mounts')
DATA = Path('/data')
STATE = Path('/sys/power/state')
MEM_SLEEP = Path('/sys/power/mem_sleep')
INPUT = Path('/dev/input')
PIDFILE = Path('/run/surface-power.pid')
LOCKS = (Path('/run/surface-library-discovery.lock'),
         Path('/run/surface-system-update.lock'))
EVENT = struct.Struct('@llHHi')
EV_SYN, EV_KEY = 0, 1
SYN_REPORT, SYN_DROPPED = 0, 3
POWER_KEYS = (116, 142)  # KEY_POWER, KEY_SLEEP
KEY_BYTES = 96
MAX_NODES = 256
MAX_TAP = 2.0
COOLDOWN = 2.0


class PowerError(ValueError):
    pass


class StorageBusy(PowerError):
    pass


def guard():
    info = MARKER.lstat()
    if (os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022
            or not MARKER.read_text().strip().startswith('surface-go-')):
        raise PowerError('Suspend requires the privileged booted 403 OS image')
    if (not os.path.ismount(DATA)
            or not any(row.split()[1:3] == [str(DATA), 'ext4']
                       for row in MOUNTS.read_text().splitlines())):
        raise PowerError('Persistent DATA must be mounted before suspend')


@contextmanager
def storage_idle():
    """Hold the same locks as installation, migration and offline updates."""
    descriptors = []
    try:
        for path in LOCKS:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            descriptors.append(fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise PowerError('Unsafe storage-operation lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StorageBusy('Suspend deferred while storage work is running') from error
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def suspend():
    guard()
    with storage_idle():
        descriptor = os.open(STATE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            modes = os.read(descriptor, 1024).decode('ascii').split()
        finally:
            os.close(descriptor)
        mode = 'mem' if 'mem' in modes else 'freeze' if 'freeze' in modes else None
        if mode is None:
            raise PowerError('The kernel does not expose a supported suspend state')
        # Respect the firmware/kernel's existing mem_sleep policy. A Surface
        # need not support deep sleep for mem (s2idle) to work.
        os.sync()
        descriptor = os.open(STATE, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            policy = mode
            if mode == 'mem':
                try:
                    policy = next(item[1:-1] for item in MEM_SLEEP.read_text().split()
                                  if item.startswith('[') and item.endswith(']'))
                except (OSError, StopIteration):
                    policy = 'unknown'
            identity = str(time.monotonic_ns())
            before = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
            print('SURFACE_POWER ' + json.dumps(dict(event='enter', id=identity,
                  state=mode, policy=policy)), flush=True)
            request = (mode + '\n').encode('ascii')
            if os.write(descriptor, request) != len(request):
                raise PowerError('The kernel did not accept the suspend request')
            after = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
            print('SURFACE_POWER ' + json.dumps(dict(event='resumed', id=identity,
                  state=mode, policy=policy, sleep_seconds=round(max(0.0, after-before), 3))),
                  flush=True)
        finally:
            os.close(descriptor)
        return mode


def open_button(path):
    """Only listen to evdev character nodes advertising a power/sleep key."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0:
            raise PowerError('Input event node is not a root-owned character device')
        capabilities = bytearray(KEY_BYTES)
        request = (2 << 30) | (KEY_BYTES << 16) | (ord('E') << 8) | (0x20 + EV_KEY)
        fcntl.ioctl(fd, request, capabilities, True)
        if not any(capabilities[key // 8] & (1 << (key % 8)) for key in POWER_KEYS):
            raise PowerError('Input device does not advertise a power or sleep key')
        # Event timestamps become monotonic where supported. Tap duration is
        # checked using both event timestamps and local monotonic receipt time.
        try:
            fcntl.ioctl(fd, (1 << 30) | (4 << 16) | (ord('E') << 8) | 0xa0,
                        struct.pack('@i', time.CLOCK_MONOTONIC))
        except OSError:
            pass
        return fd
    except BaseException:
        os.close(fd)
        raise


class TapDetector:
    def __init__(self):
        self.pressed = {}
        self.dropped = set()
        self.blocked_until = 0.0

    def reset(self, now):
        self.pressed.clear()
        self.dropped.clear()
        self.blocked_until = now + COOLDOWN

    def remove(self, node):
        self.pressed = {key: value for key, value in self.pressed.items() if key[0] != node}
        self.dropped.discard(node)

    def feed(self, node, kind, code, value, timestamp, now):
        if kind == EV_SYN:
            if code == SYN_DROPPED:
                self.remove(node)
                self.dropped.add(node)
            elif code == SYN_REPORT:
                self.dropped.discard(node)
            return False
        if node in self.dropped or kind != EV_KEY or code not in POWER_KEYS:
            return False
        key = (node, code)
        if now < self.blocked_until:
            self.pressed.pop(key, None)
            return False
        if value == 1:
            self.pressed.setdefault(key, (timestamp, now))
        elif value == 0:
            start = self.pressed.pop(key, None)
            if start and 0 <= timestamp - start[0] <= MAX_TAP and 0 <= now - start[1] <= MAX_TAP:
                self.reset(now)
                return True
        return False


def drain(fd):
    # Discard queued duplicate/wake events after returning from suspend.
    for _ in range(16):
        try:
            if not os.read(fd, EVENT.size * 64):
                return
        except BlockingIOError:
            return


def request_sleep():
    """One owner handles both UI and button sleep, including wake suppression."""
    guard()
    fd = os.open(PIDFILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077:
            raise PowerError('The power service PID file is not trusted')
        raw = os.read(fd, 32).strip()
        if not raw.isdigit() or int(raw) <= 1:
            raise PowerError('The power service PID is invalid')
        pid = int(raw)
    finally:
        os.close(fd)
    handle = os.pidfd_open(pid)
    try:
        arguments = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if b'/usr/sbin/surface-power' not in arguments or b'--watch' not in arguments:
            raise PowerError('The power-button service is not running')
        signal.pidfd_send_signal(handle, signal.SIGUSR1)
    finally:
        os.close(handle)
    print('Sleep requested; waiting for any active storage operation', flush=True)


def watch(stop=None, requested=None):
    guard()
    stop = stop or (lambda: False)
    detector = TapDetector()
    nodes = {}
    buffers = {}
    next_scan = 0.0
    pending_sleep = False
    retry_at = 0.0
    requested = requested or (lambda: False)
    with selectors.PollSelector() as selector:
        def remove(path):
            fd = nodes.pop(path, None)
            if fd is None:
                return
            selector.unregister(fd)
            os.close(fd)
            buffers.pop(path, None)
            detector.remove(path)

        def try_sleep():
            nonlocal pending_sleep, retry_at
            try:
                suspend()
                print('Power button: resumed', flush=True)
                pending_sleep = False
            except StorageBusy:
                # Keep the request; discovery runs periodically and is not a
                # reason to make the physical button silently stop working.
                retry_at = time.monotonic() + 1.0
            except (OSError, ValueError) as error:
                print('Power button: ' + str(error), flush=True)
                pending_sleep = False
            if not pending_sleep:
                detector.reset(time.monotonic())
                for current, fd in list(nodes.items()):
                    try:
                        drain(fd)
                        buffers[current] = b''
                    except OSError:
                        remove(current)

        try:
            while not stop():
                now = time.monotonic()
                if requested():
                    pending_sleep = True
                    retry_at = now
                if pending_sleep and now >= retry_at:
                    try_sleep()
                if now >= next_scan:
                    candidates = sorted(path for path in INPUT.glob('event*')
                                        if path.name[5:].isdigit())[:MAX_NODES]
                    for path in set(nodes) - set(candidates):
                        remove(path)
                    for path in candidates:
                        if path in nodes:
                            continue
                        fd = None
                        try:
                            fd = open_button(path)
                            selector.register(fd, selectors.EVENT_READ, path)
                        except (OSError, ValueError):
                            if fd is not None:
                                os.close(fd)
                            continue
                        nodes[path] = fd
                        buffers[path] = b''
                    next_scan = now + 2.0
                for key, _ in selector.select(timeout=0.25):
                    path = key.data
                    if path not in nodes:
                        continue
                    try:
                        chunk = os.read(key.fd, EVENT.size * 64)
                        if not chunk:
                            remove(path)
                            continue
                    except BlockingIOError:
                        continue
                    except OSError:
                        remove(path)
                        continue
                    pending = buffers[path] + chunk
                    complete = len(pending) // EVENT.size * EVENT.size
                    buffers[path] = pending[complete:]
                    for seconds, micros, kind, code, value in EVENT.iter_unpack(pending[:complete]):
                        if not detector.feed(path, kind, code, value,
                                             seconds + micros / 1000000.0, time.monotonic()):
                            continue
                        pending_sleep = True
                        try_sleep()
                        break
        finally:
            for path in list(nodes):
                remove(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--watch', action='store_true')
    actions.add_argument('--sleep', action='store_true')
    options = parser.parse_args(argv)
    try:
        if options.watch:
            stopped = False
            request = False

            def queue(_signal, _frame):
                nonlocal request
                request = True

            def take_request():
                nonlocal request
                value = request
                request = False
                return value

            def stop(_signal, _frame):
                nonlocal stopped
                stopped = True

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGUSR1, queue)
            watch(lambda: stopped, take_request)
        else:
            request_sleep()
        return 0
    except (OSError, ValueError) as error:
        print('Power: ' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
