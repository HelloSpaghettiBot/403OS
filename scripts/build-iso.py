#!/usr/bin/env python3
"""Package the pristine image as a UEFI optical/USB ISO, without host mounts.

Run scripts/build-iso.sh after the ordinary capped image build. Only the fixed
public base image is accepted; personalized USBs and arbitrary source paths
are not inputs. Compression and boot-tool execution require the capped build
container. Pure helpers accept temporary fixtures in the packaging tests.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / 'output/images'
TARGET = ROOT / 'output/target'
HOST = ROOT / 'output/host'
NAME = '403os-surface-go1-x86_64.iso'
LABEL = '403OS_LIVE'
PAYLOAD = 'SurfaceGoGamingOS/surface-go.img'
APPLETS = ('sh', 'mount', 'umount', 'mkdir', 'mknod', 'cat', 'grep', 'sed',
           'awk', 'sleep', 'cp', 'chmod', 'losetup', 'truncate', 'switch_root',
           'sha256sum', 'sync', 'mv')
CHUNK = 4 * 1024**2


def ordinary(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Expected an ordinary file: ' + str(path))
    return info


def fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def digest(path):
    ordinary(path)
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(CHUNK), b''):
            value.update(data)
    return value.hexdigest()


def command(arguments, **kwargs):
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def inspect_fs(image, offset, request, binary=None):
    result = command([binary or HOST / 'sbin/debugfs', '-R', request,
                      str(image) + '?offset=' + str(offset)],
                     capture_output=True, timeout=30)
    if b'File not found' in result.stderr or b'Filesystem not open' in result.stderr:
        raise ValueError('Required public-image path is missing: ' + request)
    return result.stdout


def names(image, offset, directory):
    data = inspect_fs(image, offset, 'ls -p ' + directory).decode('utf-8')
    return {line.split('/')[5] for line in data.splitlines() if line.startswith('/')}


def public_filesystem(base, root_offset, data_offset):
    """Fail closed if ordinary live-state locations have been personalized."""
    if names(base, data_offset, '/') != {'.', '..', 'lost+found'}:
        raise ValueError('Public ISO requires empty pristine DATA, never personal USB data')
    for path, permitted in [('/root', {'.', '..'}), ('/data', {'.', '..', 'windows'}),
                            ('/data/windows', {'.', '..'}),
                            ('/var/lib/bluetooth', {'.', '..'})]:
        if names(base, root_offset, path) != permitted:
            raise ValueError('Unexpected private state in public image: ' + path)
    shadow = inspect_fs(base, root_offset, 'cat /etc/shadow').decode('utf-8')
    for line in shadow.splitlines():
        fields = line.split(':')
        if len(fields) != 9 or fields[1] not in ('', '!', '*', '!!'):
            raise ValueError('Public image contains a password hash or invalid account data')
    wifi = inspect_fs(base, root_offset, 'cat /etc/wpa_supplicant.conf').decode('utf-8')
    if any(re.search(r'\b(psk|password|private_key_passwd|wep_key\d|identity)\s*=', line)
           for line in wifi.splitlines() if not line.lstrip().startswith('#')):
        raise ValueError('Public image contains a Wi-Fi credential field')


def validate_base(base, expected):
    """Validate pristine GPT/DATA and known private-state locations before packing."""
    before = ordinary(base)
    spec = importlib.util.spec_from_file_location('iso_gpt', ROOT / 'services/grow-data.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with base.open('rb') as source:
        layout = helper.read_gpt(source.fileno(), before.st_size)
        helper.read_ext4(source.fileno(), layout['parts'][2], pristine=True)
    root_offset = layout['parts'][1]['first'] * 512
    public_filesystem(base, root_offset, layout['parts'][2]['first'] * 512)
    actual = digest(base)
    if actual != expected or fingerprint(base.stat()) != fingerprint(before):
        raise ValueError('Pristine image changed or failed its packaging checksum')
    return dict(ROOT_OFFSET=root_offset, ROOT_BYTES=layout['parts'][1]['bytes'],
                IMAGE_BYTES=before.st_size, IMAGE_SHA256=actual), fingerprint(before)


def layout_text(layout):
    if (set(layout) != {'ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES', 'IMAGE_SHA256'} or
            any(type(layout[key]) is not int or layout[key] <= 0 or layout[key] % 512
                for key in ('ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES')) or
            layout['ROOT_OFFSET'] + layout['ROOT_BYTES'] >= layout['IMAGE_BYTES'] or
            not re.fullmatch('[0-9a-f]{64}', str(layout['IMAGE_SHA256']))):
        raise ValueError('Invalid ISO root partition metadata')
    return ''.join(f'{key}={layout[key]}\n' for key in
                   ('ROOT_OFFSET', 'ROOT_BYTES', 'IMAGE_BYTES', 'IMAGE_SHA256')).encode('ascii')


def guest_file(root, name):
    """Resolve guest absolute symlinks inside its root, never against the host."""
    path = PurePosixPath(name)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe guest runtime path')
    pending = list(path.parts[1:])
    done, links = [], 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not done:
                raise ValueError('Guest symlink escapes its root')
            done.pop()
            continue
        candidate = root.joinpath(*done, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError('Guest runtime symlink loop')
            target = PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                done = []
                pending = list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
        else:
            done.append(part)
    result = root.joinpath(*done)
    ordinary(result)
    return result


def elf_dependencies(path):
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
        raise ValueError('Initramfs runtime requires a little-endian x86-64 ELF')
    program = command(['readelf', '-lW', path], capture_output=True, text=True).stdout
    dynamic = command(['readelf', '-dW', path], capture_output=True, text=True).stdout
    interpreter = re.findall(r'\[Requesting program interpreter: ([^\]]+)\]', program)
    needed = re.findall(r'\(NEEDED\).*\[([^\]]+)\]', dynamic)
    if len(interpreter) > 1 or any('/' in name or not re.fullmatch('[A-Za-z0-9_.+-]+', name)
                                   for name in needed):
        raise ValueError('Unsafe runtime ELF dependencies')
    return interpreter, needed


def runtime_entries(base, root_offset, target=TARGET):
    entries, pending = {}, ['/bin/busybox', '/sbin/blkid', '/sbin/mke2fs']
    # This x86-64 loader searches lib64, while Buildroot installs libraries in
    # lib and provides lib64 -> lib. Preserve those directory aliases in the
    # initramfs; merely copying DT_NEEDED files into lib leaves them invisible.
    aliases = {}
    for prefix in ('lib64', 'usr/lib64'):
        path = target / prefix
        if path.is_symlink():
            if os.readlink(path) != 'lib':
                raise ValueError('Unexpected runtime library directory alias')
            aliases[prefix] = prefix[:-2]
            entries[prefix] = (stat.S_IFLNK | 0o777, b'lib', (0, 0))
            entries[prefix[:-2]] = (stat.S_IFDIR | 0o755, b'', (0, 0))
    while pending:
        name = pending.pop()
        stored_name = name.lstrip('/')
        for alias, destination in aliases.items():
            if stored_name.startswith(alias + '/'):
                stored_name = destination + stored_name[len(alias):]
                break
        if stored_name in entries:
            continue
        source = guest_file(target, name)
        content = inspect_fs(base, root_offset, 'cat /' + str(source.relative_to(target)))
        if content != source.read_bytes():
            raise ValueError('Runtime staging differs from the pristine image: ' + name)
        entries[stored_name] = (stat.S_IFREG | 0o755, content, (0, 0))
        interpreters, needed = elf_dependencies(source)
        pending.extend(interpreters)
        for library in needed:
            found = None
            for folder in ('/lib', '/lib64', '/usr/lib', '/usr/lib64'):
                try:
                    guest_file(target, folder + '/' + library)
                    found = folder + '/' + library
                    break
                except FileNotFoundError:
                    continue
            if found is None:
                raise ValueError('Missing initramfs dependency: ' + library)
            pending.append(found)
    for applet in APPLETS:
        entries['bin/' + applet] = (stat.S_IFLNK | 0o777, b'busybox', (0, 0))
    entries['etc/mke2fs.conf'] = (stat.S_IFREG | 0o644,
        inspect_fs(base, root_offset, 'cat /etc/mke2fs.conf'), (0, 0))
    return entries


def write_initramfs(destination, entries, epoch):
    """Write newc with normalized ownership, including console nodes without root."""
    entries = dict(entries)
    for name in list(entries):
        if name.startswith('/') or '..' in PurePosixPath(name).parts or '\x00' in name:
            raise ValueError('Unsafe initramfs entry')
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                entries.setdefault(str(parent), (stat.S_IFDIR | 0o755, b'', (0, 0)))
    for name in ('dev', 'proc', 'sys', 'run', 'newroot'):
        entries.setdefault(name, (stat.S_IFDIR | 0o755, b'', (0, 0)))
    entries['dev/console'] = (stat.S_IFCHR | 0o600, b'', (5, 1))
    entries['dev/null'] = (stat.S_IFCHR | 0o666, b'', (1, 3))
    if sum(len(value[1]) for value in entries.values()) > 64 * 1024**2:
        raise ValueError('Initramfs exceeds its bounded runtime budget')
    with destination.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream,
                                                      mode='wb', mtime=epoch) as archive:
        def record(number, name, mode, content, device):
            encoded = name.encode('utf-8') + b'\0'
            fields = (number, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1,
                      epoch, len(content), 0, 0, *device, len(encoded), 0)
            header = b'070701' + ''.join(f'{value:08x}' for value in fields).encode('ascii')
            archive.write(header + encoded + bytes(-(len(header) + len(encoded)) % 4))
            archive.write(content + bytes(-len(content) % 4))
        for number, name in enumerate(sorted(entries), 1):
            mode, content, device = entries[name]
            record(number, name, mode, content, device)
        record(len(entries) + 1, 'TRAILER!!!', 0, b'', (0, 0))


def stage_payload(base, stage, expected):
    ordinary(base)
    stage.mkdir(mode=0o755)
    folder = stage / 'SurfaceGoGamingOS'
    folder.mkdir(mode=0o755)
    os.link(base, folder / 'surface-go.img', follow_symlinks=False)
    (folder / 'surface-go.img.sha256').write_text(expected + '  surface-go.img\n')
    # Never chmod/chown the hard link: the original compiled image is immutable
    # to this packer. SquashFS pseudo metadata supplies root ownership/mode.


def build(epoch=None):
    if os.environ.get('SURFACE_RESOURCE_SCOPE') != 'build' or not Path('/.dockerenv').exists() and not Path('/run/.containerenv').exists():
        raise ValueError('Run scripts/build-iso.sh through the capped build container')
    if IMAGES.is_symlink() or IMAGES.resolve() != ROOT / 'output/images':
        raise ValueError('Image output must be the project ordinary images directory')
    base = IMAGES / 'surface-go-base.img'
    sidecar = IMAGES / 'surface-go-base.img.sha256'
    ordinary(sidecar)
    match = re.fullmatch(r'([0-9a-f]{64})  surface-go-base\.img\n', sidecar.read_text())
    if not match:
        raise ValueError('Missing pristine image checksum')
    layout, original = validate_base(base, match[1])
    epoch = int(base.stat().st_mtime) if epoch is None else epoch
    if not 0 <= epoch <= 0xffffffff:
        raise ValueError('Invalid packaging epoch')
    init = ROOT / 'external/board/surface-go1/iso-init'
    ordinary(init)
    modules = ROOT / 'output/build/grub2-2.14/build-x86_64-efi/grub-core'
    for name in ('modinfo.sh', 'normal.mod', 'linux.mod', 'kernel.img'):
        ordinary(modules / name)
    kernel_config = (ROOT / 'output/build/linux-6.12.118/.config').read_text()
    for feature in ('BLK_DEV_INITRD', 'DEVTMPFS', 'BLK_DEV_LOOP', 'OVERLAY_FS',
                    'SQUASHFS', 'SQUASHFS_XZ', 'ISO9660_FS', 'BLK_DEV_SR'):
        if not re.search(r'^CONFIG_' + feature + '=y$', kernel_config, re.M):
            raise ValueError('Rebuild the kernel with built-in CONFIG_' + feature)
    for suffix in ('', '.sha256', '.json'):
        destination = IMAGES / (NAME + suffix)
        if destination.exists() or destination.is_symlink():
            ordinary(destination)
    with tempfile.TemporaryDirectory(prefix='.public-iso-', dir=IMAGES) as temporary:
        work = Path(temporary)
        stage = work / 'iso'
        (stage / '403os').mkdir(parents=True)
        (stage / 'boot/grub').mkdir(parents=True)
        command([HOST / 'bin/mcopy', '-i', str(base) + '@@1048576',
                 '::/bzImage', stage / 'boot/bzImage'])
        entries = runtime_entries(base, layout['ROOT_OFFSET'])
        entries['init'] = (stat.S_IFREG | 0o755, init.read_bytes(), (0, 0))
        entries['etc/surface-iso-layout'] = (stat.S_IFREG | 0o400, layout_text(layout), (0, 0))
        write_initramfs(stage / 'boot/initramfs.cpio.gz', entries, epoch)
        (stage / 'boot/grub/grub.cfg').write_text(
            'set default=0\nset timeout=3\n'
            'menuentry "403 OS Live / Install (UEFI preview)" {\n'
            '  linux /boot/bzImage surface.boot=iso surface.iso=' + LABEL +
            ' console=tty0 console=ttyS0,115200 quiet loglevel=3\n'
            '  initrd /boot/initramfs.cpio.gz\n}\n')
        payload = work / 'payload'
        stage_payload(base, payload, layout['IMAGE_SHA256'])
        # Creating our staging hard link changes ctime/link count. Require the
        # same inode/content metadata, then track its new ctime while compressing.
        staged = fingerprint(base.stat())
        if staged[:4] != original[:4]:
            raise ValueError('Pristine image changed before payload staging')
        original = staged
        squash = stage / '403os/payload.squashfs'
        command(['mksquashfs', payload, squash, '-noappend', '-no-progress',
                 '-comp', 'xz', '-b', '1M', '-processors', '10', '-mem', '1024M',
                 '-all-root', '-no-xattrs', '-all-time', epoch, '-mkfs-time', epoch,
                 '-p', PAYLOAD + ' m 444 0 0',
                 '-p', PAYLOAD + '.sha256 m 444 0 0'])
        stored = command(['unsquashfs', '-cat', squash, PAYLOAD + '.sha256'],
                         capture_output=True).stdout
        if stored != (layout['IMAGE_SHA256'] + '  surface-go.img\n').encode():
            raise ValueError('Compressed payload checksum sidecar differs')
        # Compression does not permit a concurrent rebuild to change the source.
        if fingerprint(base.stat()) != original:
            raise ValueError('Pristine image changed during ISO packaging')
        result = work / NAME
        # mkrescue renders an EFI volume label even with --fonts= empty.
        # Build that PF2 from the project's licensed font; Buildroot does not
        # install GRUB's optional unicode.pf2 into its host tools directory.
        label_font = work / 'label.pf2'
        command(['grub-mkfont', '-s', '16', '-o', label_font,
                 ROOT / 'ui/assets/fonts/Cantarell-Regular.otf'])
        command([HOST / 'bin/grub-mkrescue', '--directory=' + str(modules),
                 '--label-font=' + str(label_font), '--product-name=403 OS',
                 '--locales=', '--fonts=', '--themes=', '-o', result, stage,
                 '--', '-volid', LABEL])
        report = command(['xorriso', '-indev', result, '-report_el_torito', 'plain',
                          '-report_system_area', 'plain'], capture_output=True, text=True)
        boot = report.stdout + report.stderr
        if not re.search(r'El Torito boot img\s*:.*UEFI', boot) or 'GPT' not in boot:
            raise ValueError('ISO does not report a UEFI El Torito image and GPT hybrid layout')
        if result.stat().st_size >= 2 * 1024**3:
            raise ValueError('ISO exceeds the public release asset size limit')
        checksum = digest(result)
        manifest = dict(schema='403os-public-iso-v1', image=NAME,
                        sha256=checksum, bytes=result.stat().st_size,
                        base_sha256=layout['IMAGE_SHA256'], base_bytes=layout['IMAGE_BYTES'],
                        boot='UEFI El Torito with GPT hybrid', label=LABEL,
                        persistence='temporary live session; install to retain data',
                        personal_data=False, physical_boot_verified=False)
        checksum_file = work / (NAME + '.sha256')
        checksum_file.write_text(checksum + '  ' + NAME + '\n')
        manifest_file = work / (NAME + '.json')
        manifest_file.write_text(json.dumps(manifest, indent=2) + '\n')
        for path in (result, checksum_file, manifest_file):
            os.replace(path, IMAGES / path.name)
        print(json.dumps(manifest), flush=True)
        return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--epoch', type=int, help='Normalize archive timestamps (default: base-image mtime)')
    args = parser.parse_args()
    build(args.epoch)
