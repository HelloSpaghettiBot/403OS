#!/usr/bin/env python3
"""Discover local and safely mounted game libraries inside the booted OS."""
import argparse
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

OUTPUT = Path('/data/library')
CACHE = Path('/data/artwork')
LOCK = Path('/run/surface-library-discovery.lock')


def mounts():
    result = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, separator, right = line.partition(' - ')
        fields, extra = left.split(), right.split()
        if separator and len(fields) >= 6 and extra:
            path = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
            result[path] = dict(device=fields[2], options=set(fields[5].split(',')), filesystem=extra[0])
    return result


def safe_directory(path, create=False):
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in Path(path).parts[1:]:
            if create:
                try: os.mkdir(part, 0o755, dir_fd=descriptor)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Library service directories must belong to the system')
    finally:
        os.close(descriptor)


def require_guest():
    marker = Path('/etc/surface-image-id')
    info = marker.lstat()
    if (os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            info.st_mode & 0o022 or not marker.read_text().startswith('surface-go-')):
        raise ValueError('Automatic discovery requires the booted 403 OS system service')
    safe_directory('/data')
    data = mounts().get('/data', {})
    device = Path('/data').stat().st_dev
    if (data.get('filesystem') != 'ext4' or 'rw' not in data.get('options', set()) or
            data.get('device') != f'{os.major(device)}:{os.minor(device)}'):
        raise ValueError('Automatic discovery requires mounted writable DATA')


def discovery_roots(mounted):
    roots = []
    local = Path('/data/roms')
    if local.is_dir() and not local.is_symlink():
        safe_directory(local)
        roots.append(local)
    shared = mounted.get('/library/roms', {})
    if shared.get('filesystem') == '9p' and 'ro' in shared.get('options', set()):
        roots.append(Path('/library/roms'))
    for name, mount in sorted(mounted.items()):
        if (re.fullmatch(r'/media/library/[a-fA-F0-9][a-fA-F0-9-]{3,63}', name) and
                {'ro', 'nosuid', 'nodev', 'noexec'}.issubset(mount['options'])):
            path = Path(name)
            if path.is_dir() and not path.is_symlink():
                roots.append(path)
    if len(roots) > 66:
        raise ValueError('Too many game volumes for automatic discovery')
    return roots


def refresh(scanner, roots, output, cache, fetch_covers=False):
    """Pure directory operation; production passes only verified guest roots."""
    games, errors, seen = [], [], set()
    deadline = time.monotonic() + 90
    for root in roots:
        found, problems = scanner.scan(root, deadline=deadline)
        errors.extend(problems)
        for game in found:
            if game['id'] not in seen:
                seen.add(game['id'])
                games.append(game)
    # A drive removed mid-scan must not replace a previously populated catalog.
    if errors:
        raise ValueError('A game volume could not be read completely; the previous library was kept')
    if not roots or (not games and (output / 'catalog.tsv').is_file() and (output / 'catalog.tsv').stat().st_size):
        return dict(event='library-discovery', games=None, covers=None, warnings=0,
                    message='No games found on connected game storage; the previous library was kept')
    output.mkdir(parents=True, exist_ok=True)
    if fetch_covers:
        cache.mkdir(parents=True, exist_ok=True)
    errors += scanner.prepare_covers(games, output, cache, fetch_covers)
    scanner.write_catalog(games, errors, ', '.join(map(str, roots)), output)
    return dict(event='library-discovery', games=len(games), covers=sum(bool(game['cover_texture_path']) for game in games),
                warnings=len(errors), message='Games found and added to HOME')


def once(fetch_covers=False):
    require_guest()
    safe_directory(OUTPUT, create=True)
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('Unsafe game-discovery lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        volumes = subprocess.run(['/usr/bin/surface-library-volumes', '--mount-readonly'],
                                 capture_output=True, timeout=40, check=False)
        if len(volumes.stdout) > 1024 * 1024:
            raise ValueError('Game volume inventory exceeded its limit')
        try:
            inventory = json.loads(volumes.stdout)
        except (ValueError, UnicodeError) as error:
            raise ValueError('Game volume inventory was invalid; the previous library was kept') from error
        if (volumes.returncode or not isinstance(inventory, dict) or inventory.get('status') != 'OK'
                or inventory.get('errors') != []):
            raise ValueError('A game volume could not be mounted or inspected; the previous library was kept')
        loader = importlib.machinery.SourceFileLoader('surface_library_scan', '/usr/bin/surface-library-scan')
        spec = importlib.util.spec_from_loader(loader.name, loader)
        scanner = importlib.util.module_from_spec(spec)
        loader.exec_module(scanner)
        return refresh(scanner, discovery_roots(mounts()), OUTPUT, CACHE, fetch_covers)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--once', action='store_true')
    action.add_argument('--daemon', action='store_true')
    parser.add_argument('--fetch-covers', action='store_true')
    args = parser.parse_args()
    if args.daemon and args.fetch_covers:
        parser.error('Cover downloads require the explicit --once action')
    try:
        require_guest()
    except (OSError, ValueError) as error:
        print(json.dumps(dict(event='library-discovery', error=str(error))), flush=True)
        return 1
    while True:
        try:
            result = once(args.fetch_covers)
            print(json.dumps(result), flush=True)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(json.dumps(dict(event='library-discovery', error=str(error))), flush=True)
            if not args.daemon: return 1
        if not args.daemon: return 0
        time.sleep(15)


if __name__ == '__main__':
    sys.exit(main())
