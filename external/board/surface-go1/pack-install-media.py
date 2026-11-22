#!/usr/bin/env python3
"""Build an outer USB image containing one sparse, pristine installation image.

Runs under the builder's fakeroot. No mounts, block devices or host rootfs are
used. The pristine base is generated first, never from an earlier outer image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

CHUNK = 4 * 1024**2
DATA_BYTES = 4 * 1024**3
RESERVE = 512 * 1024**2
PAYLOAD = '/SurfaceGoGamingOS/surface-go.img'
BASE_ROOT_UUID = '8b4a530e-d6d4-4e69-9307-310000000001'
BASE_DATA_UUID = '8b4a530e-d6d4-4e69-9307-310000000002'


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('Image pipeline accepts ordinary files only: '+str(path))
    return info


def digest(path):
    regular(path)
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK), b''):
            value.update(chunk)
    return value.hexdigest()


def root_owned(path, mode):
    os.chmod(path, mode, follow_symlinks=False)
    os.chown(path, 0, 0, follow_symlinks=False)
    info = path.lstat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, mode):
        raise RuntimeError('Run the media packer under the Buildroot host fakeroot.')


def stage_payload(base, root, base_sha):
    info = regular(base)
    if info.st_blocks*512 > DATA_BYTES-RESERVE:
        raise RuntimeError('Sparse pristine image allocation exceeds the 4 GiB DATA budget.')
    if root.exists():
        raise RuntimeError('Installer DATA staging must be a new directory.')
    root.mkdir(mode=0o755)
    folder = root/'SurfaceGoGamingOS'
    folder.mkdir(mode=0o755)
    embedded = folder/'surface-go.img'
    # The source has never contained this directory: it is the prior empty-DATA
    # genimage output. A hard link preserves sparse extents without another copy.
    os.link(base, embedded, follow_symlinks=False)
    sidecar = folder/'surface-go.img.sha256'
    sidecar.write_text(base_sha+'  surface-go.img\n')
    for directory in (root, folder):
        root_owned(directory, 0o755)
    for path in (embedded, sidecar):
        root_owned(path, 0o444)
    if embedded.stat().st_ino != info.st_ino or embedded.stat().st_dev != info.st_dev:
        raise RuntimeError('Installer payload staging unexpectedly copied the base image.')
    return folder


def debugfs(binary, image, request):
    result = subprocess.run([str(binary), '-R', request, str(image)], check=True,
                            capture_output=True, text=True, timeout=30)
    return result.stdout


def verify_pristine_data(binary, data):
    regular(data)
    listing = debugfs(binary, data, 'ls -p /')
    names = [line.split('/')[5] for line in listing.splitlines() if line.startswith('/')]
    if set(names) != {'.', '..', 'lost+found'}:
        raise RuntimeError('Base DATA must be pristine, with no embedded installer or private state.')


def verify_data(binary, data, expected_sha, expected_size):
    for path, kind, mode in [('/SurfaceGoGamingOS', 'directory', '0755'),
                             (PAYLOAD, 'regular', '0444'),
                             (PAYLOAD+'.sha256', 'regular', '0444')]:
        output = debugfs(binary, data, 'stat '+path)
        if (not re.search(r'Type:\s*'+kind+r'\s+Mode:\s*0*'+mode.lstrip('0')+r'\b', output)
                or not re.search(r'User:\s*0\s+Group:\s*0\b', output)):
            raise RuntimeError('Embedded installer ownership/type/mode verification failed: '+path)
        if path == PAYLOAD and not re.search(r'Size:\s*'+str(expected_size)+r'\b', output):
            raise RuntimeError('Embedded installer logical size changed.')
    sidecar = debugfs(binary, data, 'cat '+PAYLOAD+'.sha256')
    if sidecar != expected_sha+'  surface-go.img\n':
        raise RuntimeError('Embedded installer checksum sidecar changed.')
    # Stream the sparse logical bytes; never extract a second 6.5 GiB host copy.
    value = hashlib.sha256()
    total = 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(binary), '-R', 'cat '+PAYLOAD, str(data)],
                                   stdout=subprocess.PIPE, stderr=errors)
        try:
            for chunk in iter(lambda: process.stdout.read(CHUNK), b''):
                total += len(chunk)
                if total > expected_size:
                    raise RuntimeError('Embedded installer exceeds its declared logical size.')
                value.update(chunk)
            code = process.wait(timeout=30)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
    if code or total != expected_size or value.hexdigest() != expected_sha:
        raise RuntimeError('Embedded installer full-file readback checksum failed.')


def prepare_live_boot(images, workspace, tools):
    """Give each USB its own root selector without changing the installed base.

    The EFI FAT copy and GPT must be rendered together. Merely changing the GPT
    would leave GRUB requesting the older installed filesystem at startup.
    """
    source = images/'efi-part/EFI/BOOT/grub.cfg'
    text = source.read_text()
    selector = 'root=PARTUUID=' + BASE_ROOT_UUID
    if text.count(selector) != 1:
        raise RuntimeError('Expected exactly one installed root selector in EFI configuration')
    root_uuid, data_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    if len({root_uuid, data_uuid, BASE_ROOT_UUID, BASE_DATA_UUID}) != 4:
        raise RuntimeError('Live and installed partition identifiers must differ')
    config = workspace/'grub-live.cfg'
    config.write_text(text.replace(selector, 'root=PARTUUID='+root_uuid+' surface.boot=live'))
    efi = workspace/'efi-usb.vfat'
    regular(images/'efi.vfat')
    shutil.copyfile(images/'efi.vfat', efi)
    subprocess.run([str(tools/'mcopy'), '-o', '-i', str(efi), str(config),
                    '::/EFI/BOOT/grub.cfg'], check=True, capture_output=True, timeout=30)
    readback = subprocess.run([str(tools/'mtype'), '-i', str(efi), '::/EFI/BOOT/grub.cfg'],
                              check=True, capture_output=True, text=True, timeout=30).stdout
    if readback != config.read_text():
        raise RuntimeError('Live EFI root selector readback failed')
    return dict(root_uuid=root_uuid, data_uuid=data_uuid, efi_image=str(efi.resolve()))


def render_config(template, destination, stage, boot):
    text = template.read_text()
    if text.count('@INSTALLER_DATA_ROOT@') != 1:
        raise RuntimeError('USB config must have exactly one installer DATA staging reference.')
    name = str(stage.resolve())
    if any(c in name for c in ('"', "'", '\\', '\n', '\r')):
        raise RuntimeError('Unsupported image staging path for genimage shell invocation.')
    replacements = {'@INSTALLER_DATA_ROOT@': name, '@LIVE_ROOT_UUID@': boot['root_uuid'],
                    '@LIVE_DATA_UUID@': boot['data_uuid'], '@LIVE_EFI_IMAGE@': boot['efi_image']}
    for token, value in replacements.items():
        if text.count(token) != 1 or any(c in value for c in ('"', "'", '\\', '\n', '\r')):
            raise RuntimeError('Invalid live image substitution: '+token)
        text = text.replace(token, value)
    destination.write_text(text)


def build(images, build_dir, template, genimage, debugfs_binary):
    images = images.resolve(strict=True)
    build_dir = build_dir.resolve(strict=True)
    base = images/'surface-go-base.img'
    original_mode = stat.S_IMODE(regular(base).st_mode)
    verify_pristine_data(debugfs_binary, images/'data.ext4')
    base_sha = digest(base)
    (images/'surface-go-base.img.sha256').write_text(base_sha+'  surface-go-base.img\n')
    # Both staged link and genimage work files live on the same filesystem.
    with tempfile.TemporaryDirectory(prefix='.installer-media-', dir=images) as temporary:
        temp = Path(temporary)
        root = temp/'data-root'
        config = temp/'genimage-usb.cfg'
        work = temp/'genimage-work'
        try:
            stage_payload(base, root, base_sha)
            boot = prepare_live_boot(images, temp, genimage.parent)
            render_config(template, config, root, boot)
            subprocess.run([str(genimage), '--rootpath', str(temp), '--tmppath', str(work),
                            '--inputpath', str(images), '--outputpath', str(images),
                            '--config', str(config)], check=True)
            verify_data(debugfs_binary, images/'data-usb.ext4', base_sha, base.stat().st_size)
        finally:
            # Hard-linked staging requires the image's own mode to remain writable
            # for the next incremental base regeneration after packaging completes.
            os.chmod(base, original_mode)
    outer = images/'surface-go.img'
    outer_sha = digest(outer)
    (images/'SHA256SUMS').write_text(outer_sha+'  surface-go.img\n'+base_sha+'  surface-go-base.img\n')
    manifest = dict(schema='surface-self-contained-media-v1',
        outer_image=outer.name, outer_sha256=outer_sha, outer_bytes=outer.stat().st_size,
        installation_image=base.name, installation_sha256=base_sha, installation_bytes=base.stat().st_size,
        embedded_path='/data'+PAYLOAD, data_bytes=DATA_BYTES, payload_readback='PASS',
        payload_uid=0, payload_gid=0, payload_mode='0444', recursive_payload=False,
        live_root_uuid=boot['root_uuid'], live_data_uuid=boot['data_uuid'])
    (images/'installer-media.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--genimage', type=Path, required=True)
    parser.add_argument('--debugfs', type=Path, required=True)
    args = parser.parse_args()
    build(args.images, args.build_dir, args.template, args.genimage, args.debugfs)


if __name__ == '__main__':
    main()
