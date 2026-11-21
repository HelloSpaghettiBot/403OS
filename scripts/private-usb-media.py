#!/usr/bin/env python3
"""Package an explicitly staged private live USB without altering public images.

Run under the builder's fakeroot. All outputs and private input files must stay
under ignored artifacts/private-personalization. No block devices are opened.
The embedded internal-install image remains the pristine public base.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'artifacts/private-personalization'
spec=importlib.util.spec_from_file_location('media',ROOT/'external/board/surface-go1/pack-install-media.py')
media=importlib.util.module_from_spec(spec);spec.loader.exec_module(media)


def private_path(path):
    resolved=path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_relative_to(PRIVATE.resolve(strict=True)):
        raise ValueError('Private media must remain under ignored private-personalization artifacts')
    return resolved


def hash_stream(command,expected_size):
    value=hashlib.sha256();total=0
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors)
        try:
            for block in iter(lambda:process.stdout.read(media.CHUNK),b''):
                total+=len(block)
                if total>expected_size:raise ValueError('Private media file exceeds its inventory size')
                value.update(block)
            if process.wait(timeout=30) or total!=expected_size:raise ValueError('Private file readback failed')
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close()
    return value.hexdigest()


def restore_state(archive, stage):
    """Restore only reviewed DATA state, retaining private profile/save modes.

    No tar extraction helper is used: links, devices, parent traversal and
    privileged permission bits are refused before any staging file is changed.
    """
    allowed = {'profiles', 'bluetooth', 'config', 'home', 'saves', 'moonlight', 'library'}
    entries = {}
    with tarfile.open(archive, 'r:') as source:
        members = source.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > 512 * 1024**2:
            raise ValueError('Private state archive exceeds its bound')
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise ValueError('Unsafe private state path')
            if path.parts[0] == 'logs':
                continue
            if (path.parts[0] not in allowed or not (member.isdir() or member.isfile())
                    or member.mode & 0o7022 or member.uid < 0 or member.gid < 0
                    or member.uid > 65535 or member.gid > 65535 or path in entries):
                raise ValueError('Unsupported private state entry')
            destination = stage / path
            for component in [destination, *destination.parents]:
                if component == stage.parent:
                    break
                if component.is_symlink():
                    raise ValueError('Private state may not follow a staging symlink')
            entries[path] = member
        # Create containers before files regardless of archive entry order.
        for path, member in sorted(entries.items(), key=lambda item: (not item[1].isdir(), len(item[0].parts))):
            destination = stage / path
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, destination.open('wb') as output:
                    while block := incoming.read(media.CHUNK):
                        output.write(block)
        return {path: dict(uid=m.uid, gid=m.gid, mode=m.mode & 0o777) for path, m in entries.items()}


def state_mode(path, stage, state):
    if metadata := state.get(path.relative_to(stage)):
        os.chmod(path, metadata['mode'], follow_symlinks=False)
        os.chown(path, metadata['uid'], metadata['gid'], follow_symlinks=False)
    else:
        media.root_owned(path, (0o700 if path == stage/'home' else 0o755) if path.is_dir()
                         else (0o600 if path == stage/'home/wpa_supplicant.conf' else 0o444))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data-gib',type=int,default=16,choices=range(8,65))
    parser.add_argument('--state-archive',type=Path,help='Private backup from the same authorized USB; never public input')
    args=parser.parse_args()
    stage=private_path(args.stage);manifest_path=private_path(args.manifest)
    if PRIVATE.stat().st_mode&0o077:raise ValueError('Private personalization directory must have mode 0700')
    if not args.output.resolve().is_relative_to(PRIVATE.resolve(strict=True)):
        raise ValueError('Private output must remain under ignored personalization artifacts')
    if args.output.resolve().is_relative_to(stage) or stage.is_relative_to(args.output.resolve()):
        raise ValueError('Private staging and output directories must be separate')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True);output=private_path(args.output)
    state = restore_state(private_path(args.state_archive), stage) if args.state_archive else {}
    images=ROOT/'output/images';host=ROOT/'output/host'
    inventory=json.loads(manifest_path.read_text());entries=inventory['files']
    allocated=(images/'surface-go-base.img').stat().st_blocks*512
    if inventory['bytes']+allocated+1024**3>args.data_gib*1024**3:raise ValueError('Private DATA capacity is insufficient')
    for item in entries:
        relative=Path(item['relative'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe private library path')
        source=stage/'roms'/relative
        if media.regular(source).st_size!=item['bytes'] or media.digest(source)!=item['sha256']:
            raise ValueError('Staged private library changed since copying')
    auxiliary=[]
    for directory,dirs,files in os.walk(stage,followlinks=False):
        folder=Path(directory)
        state_mode(folder, stage, state)
        for name in dirs:
            if (folder/name).is_symlink():raise ValueError('Private stage contains a symlink')
        for name in files:
            path=folder/name;media.regular(path)
            if not path.is_relative_to(stage/'roms') and not path.is_relative_to(stage/'SurfaceGoGamingOS'):
                auxiliary.append(dict(relative=str(path.relative_to(stage)),bytes=path.stat().st_size,sha256=media.digest(path)))
            state_mode(path, stage, state)
    base=images/'surface-go-base.img';base_sha=media.digest(base)
    media.verify_pristine_data(host/'sbin/debugfs',images/'data.ext4')
    installer=stage/'SurfaceGoGamingOS';installer.mkdir(exist_ok=True)
    payload=installer/'surface-go.img'
    if payload.exists():
        if payload.stat().st_ino!=base.stat().st_ino:payload.unlink()
    if not payload.exists():os.link(base,payload,follow_symlinks=False)
    (installer/'surface-go.img.sha256').write_text(base_sha+'  surface-go.img\n')
    media.root_owned(installer,0o755)
    for path in installer.iterdir():media.root_owned(path,0o444)
    template=(ROOT/'external/board/surface-go1/genimage-usb.cfg').read_text().replace('size = 4G',f'size = {args.data_gib}G')
    config=output/'genimage-private.cfg';config.write_text(template.replace('@INSTALLER_DATA_ROOT@',str(stage)))
    try:
        subprocess.run([str(host/'bin/genimage'),'--rootpath',str(stage),'--tmppath',str(output/'genimage-work'),
            '--inputpath',str(images),'--outputpath',str(output),'--config',str(config)],check=True)
    finally:os.chmod(base,0o644)
    data=output/'data-usb.ext4'
    media.verify_data(host/'sbin/debugfs',data,base_sha,base.stat().st_size)
    contents=[dict(item,relative='roms/'+item['relative']) for item in entries]+auxiliary
    for index,item in enumerate(contents,1):
        name='/'+item['relative']
        if any(c in name for c in '\n\r\x00'):raise ValueError('Unsupported private filename')
        request='cat "'+name.replace('\\','\\\\').replace('"','\\"')+'"'
        actual=hash_stream([str(host/'sbin/debugfs'),'-R',request,str(data)],item['bytes'])
        if actual!=item['sha256']:raise ValueError('Private file readback checksum differs from source')
        if index%50==0:print('Private file readback verified',index,'/',len(contents),flush=True)
    for relative, metadata in state.items():
        name = '/' + str(relative)
        if any(c in name for c in '\n\r\x00"\\'):
            raise ValueError('Unsupported private state filename')
        info = media.debugfs(host/'sbin/debugfs', data, 'stat "' + name + '"')
        if (not re.search(r'User:\s*' + str(metadata['uid']) + r'\s+Group:\s*' + str(metadata['gid']) + r'\b', info)
                or not re.search(r'Mode:\s*0*' + format(metadata['mode'], 'o') + r'\b', info)):
            raise ValueError('Private state ownership/mode readback failed')
    wifi=media.debugfs(host/'sbin/debugfs',data,'stat /home/wpa_supplicant.conf')
    if not re.search(r'User:\s*0\s+Group:\s*0\b',wifi) or not re.search(r'Mode:\s*0600\b',wifi):
        raise ValueError('Private Wi-Fi permissions are incorrect')
    saved_wifi=stage/'home/wpa_supplicant.conf'
    if hash_stream([str(host/'sbin/debugfs'),'-R','cat /home/wpa_supplicant.conf',str(data)],saved_wifi.stat().st_size)!=media.digest(saved_wifi):
        raise ValueError('Private Wi-Fi readback differs from the exported settings')
    outer=output/'surface-go.img';sha=media.digest(outer)
    (output/'SHA256SUMS').write_text(sha+'  surface-go.img\n')
    receipt=dict(schema='surface-private-usb-v1',image_sha256=sha,image_bytes=outer.stat().st_size,
        installer_payload_sha256=base_sha,library_files=len(entries),library_bytes=inventory['bytes'],
        private_library_readback='PASS',auxiliary_files_readback=len(auxiliary),embedded_installer_readback='PASS',wifi_permissions='0600 root:root',
        preserved_state_entries=len(state),preserved_state_permissions='PASS',
        contains_private_data=True,public_distribution=False)
    (output/'private-media.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
