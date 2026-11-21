#!/usr/bin/python3
"""Private live-DATA migration after a verified pristine internal installation.

There is no alternate source, mountpoint, device root or host-execution switch.
Pure inventory/copy functions accept fixture directories only when imported.
"""
import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import subprocess

SOURCE=Path('/data')
LOCK=Path('/run/surface-library-discovery.lock')
MOUNT=Path('/run/surface-installer/seed-target')
GROW=Path('/usr/sbin/surface-grow-data')
ROOTS=('roms','bios','library','artwork','saves','home','config','apps','windows','browser','share','moonlight','bluetooth','profiles')
MAX_FILES=250_000
MAX_DEPTH=64
RESERVE=256*1024**2
CHUNK=4*1024**2
DATA_UUID='8b4a530e-d6d4-4e69-9307-310000000002'


class Refused(ValueError):pass


def emit(event,**fields):print(json.dumps(dict(event=event,**fields)),flush=True)


def guest():
    fd=os.open('/etc/surface-image-id',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if os.geteuid()!=0 or not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode&0o022 or not os.read(fd,128).startswith(b'surface-go-'):
            raise Refused('Live DATA migration requires the privileged booted Surface OS image')
    finally:os.close(fd)


def directory(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise Refused('Invalid migration directory')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise


@contextlib.contextmanager
def library_lock():
    guest()
    fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode&0o077:raise Refused('Unsafe library migration lock')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)


def excluded(parts):
    # Never omit a ROM/BIOS/save just because its filename happens to be cache.
    if parts[0] in ('roms','bios','library','artwork','saves'):return False
    return (parts==('profiles','.lock') or any(p in ('cache','.cache','logs') for p in parts[1:]) or
            parts[-1] in ('SingletonLock','SingletonCookie','SingletonSocket'))


def metadata(info):
    return dict(device=info.st_dev,inode=info.st_ino,bytes=info.st_size,mtime=info.st_mtime_ns,
                ctime=info.st_ctime_ns,uid=info.st_uid,gid=info.st_gid,mode=stat.S_IMODE(info.st_mode))


def inventory(root):
    fd=directory(root);device=os.fstat(fd).st_dev;entries=[];total=0
    def walk(parent,name,parts):
        nonlocal total
        if excluded(parts):return
        if len(parts)>MAX_DEPTH or len(entries)>=MAX_FILES:raise Refused('Live DATA exceeds the migration file/depth bound')
        info=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if info.st_dev!=device:raise Refused('Nested mounts are not accepted in live DATA migration')
        value=dict(path='/'.join(parts),**metadata(info))
        if info.st_mode&(stat.S_ISUID|stat.S_ISGID):raise Refused('Set-user/group-ID permission bits are not migrated')
        if stat.S_ISDIR(info.st_mode):
            value['kind']='directory';entries.append(value)
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            try:
                if os.fstat(child).st_ino!=info.st_ino:raise Refused('A source directory changed during inventory')
                for item in sorted(os.listdir(child)):walk(child,item,(*parts,item))
            finally:os.close(child)
        elif stat.S_ISREG(info.st_mode):
            if info.st_size>1024**4:raise Refused('A live DATA file exceeds the migration size bound')
            value['kind']='file';total+=info.st_size;entries.append(value)
        elif stat.S_ISLNK(info.st_mode):
            if parts[0]=='roms':raise Refused('ROM symbolic links must be imported as ordinary files before installing')
            link=os.readlink(name,dir_fd=parent)
            if len(os.fsencode(link))>4096:raise Refused('A live DATA link exceeds the migration bound')
            value.update(kind='symlink',link=link);entries.append(value)
        else:raise Refused('Live DATA contains a socket, device or FIFO; close applications before installing')
    try:
        for name in ROOTS:
            try:os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:continue
            if not stat.S_ISDIR(os.stat(name,dir_fd=fd,follow_symlinks=False).st_mode):raise Refused('A selected live DATA root is not an ordinary directory')
            walk(fd,name,(name,))
    finally:os.close(fd)
    return entries,total


def open_relative(root,relative,flags=os.O_RDONLY):
    parts=relative.split('/')
    if not parts or any(p in ('','.','..') for p in parts):raise Refused('Unsafe migration entry path')
    fd=os.dup(root)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        result=os.open(parts[-1],flags|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=fd)
        return result
    finally:os.close(fd)


def validate_auth(root,entries):
    entry=next((x for x in entries if x['path']=='profiles/auth.json'),None)
    if entry is None:return False
    # Production entry points require effective UID 0. Using the effective
    # owner also permits ordinary, unprivileged temporary-directory fixtures.
    if entry['kind']!='file' or entry['uid']!=os.geteuid() or entry['mode']!=0o600 or entry['bytes']>128*1024:
        raise Refused('Profile authentication must be a private root-owned regular file')
    fd=directory(root)
    try:
        child=open_relative(fd,'profiles/auth.json')
        with os.fdopen(child,'rb') as stream:db=json.loads(stream.read(128*1024+1))
    finally:os.close(fd)
    if not isinstance(db,dict) or db.get('version')!=1 or not isinstance(db.get('profiles'),list) or len(db['profiles'])>32:
        raise Refused('The profile authentication database is invalid')
    names=set();uids=set()
    for profile in db['profiles']:
        if not isinstance(profile,dict):raise Refused('Invalid profile authentication record')
        name,uid,gid=profile.get('name'),profile.get('uid'),profile.get('gid');password=profile.get('password',{})
        if (not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,23}',name) or name in names or
            type(uid) is not int or type(gid) is not int or uid!=gid or uid in uids or (name=='root')!=(uid==0) or
            (uid!=0 and not 2000<=uid<=2999) or not isinstance(password,dict) or password.get('algorithm')!='pbkdf2-sha256' or
            password.get('iterations')!=600_000 or any(not isinstance(password.get(k),str) or not re.fullmatch('[0-9a-f]{64}',password[k]) for k in ('salt','digest'))):
            raise Refused('Invalid profile identity or password hash; source authentication was not changed')
        names.add(name);uids.add(uid)
    if db['profiles'] and db['profiles'][0]['name']!='root':raise Refused('The administrator profile must be first')
    return bool(db['profiles'])


def manifest_sha(entries):
    return hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def source_plan(capacity,root=SOURCE):
    entries,total=inventory(root)
    if total+RESERVE+len(entries)*8192>capacity:raise Refused('Live games and profiles do not fit the installed DATA filesystem; nothing was erased')
    auth=validate_auth(root,entries)
    # Existing absolute /data references remain valid on the installed system.
    # Never silently publish a catalog that still points to the removed USB.
    catalog=next((x for x in entries if x['path']=='library/catalog.tsv'),None)
    if catalog:
        if catalog['kind']!='file' or catalog['bytes']>64*1024**2:raise Refused('The live library catalog exceeds its migration bound')
        fd=directory(root)
        try:
            stream=os.fdopen(open_relative(fd,'library/catalog.tsv'),'r')
            paths={x['path'] for x in entries if x['kind']=='file'}
            with stream:
                for line in stream:
                    fields=line.rstrip('\n').split('\t')
                    if len(fields)<4:raise Refused('The live library catalog is invalid')
                    for value in (fields[3],fields[4] if len(fields)>4 else ''):
                        if value and (not value.startswith('/data/') or value[6:] not in paths):
                            raise Refused('A library entry points outside the copied live DATA; import it before installing')
        finally:os.close(fd)
    token=manifest_sha(entries)
    return dict(entries=entries,bytes=total,files=sum(x['kind']=='file' for x in entries),manifest_sha256=token,
                root_auth_preserved=auth,rom_files=sum(x['kind']=='file' and x['path'].startswith('roms/') for x in entries))


def public_plan(value):return {key:value[key] for key in ('bytes','files','rom_files','manifest_sha256','root_auth_preserved')}


def assert_entry(fd,entry):
    info=os.fstat(fd)
    if metadata(info)!={key:entry[key] for key in metadata(info)}:raise Refused('Live DATA changed after review; keep the USB and retry installation')


def copy_tree(source,destination,plan,progress=emit):
    """FD-relative no-follow copy; fixture callers supply only temporary trees."""
    source_fd=directory(source);destination_fd=directory(destination)
    stage='.surface-seed-stage-'+secrets.token_hex(8);done=0;digests=[]
    by_path={entry['path']:entry for entry in plan['entries']}
    try:
        if set(os.listdir(destination_fd))-{'lost+found'}:raise Refused('Installed DATA is not fresh; existing user data will never be overwritten')
        if plan['bytes']+RESERVE+len(plan['entries'])*8192>os.fstatvfs(destination_fd).f_bavail*os.fstatvfs(destination_fd).f_frsize:
            raise Refused('Fresh installed DATA does not have enough space for the live games and profiles')
        os.mkdir(stage,0o700,dir_fd=destination_fd)
        stage_fd=os.open(stage,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=destination_fd)
        try:
            for entry in plan['entries']:
                path=entry['path'];parent,_,name=path.rpartition('/')
                out_parent=os.dup(stage_fd) if not parent else open_relative(stage_fd,parent,os.O_RDONLY|os.O_DIRECTORY)
                try:
                    if entry['kind']=='directory':
                        child=open_relative(source_fd,path,os.O_RDONLY|os.O_DIRECTORY)
                        try:assert_entry(child,entry)
                        finally:os.close(child)
                        os.mkdir(name,0o700,dir_fd=out_parent)
                    elif entry['kind']=='symlink':
                        source_parent=os.dup(source_fd) if not parent else open_relative(source_fd,parent,os.O_RDONLY|os.O_DIRECTORY)
                        try:
                            info=os.stat(name,dir_fd=source_parent,follow_symlinks=False)
                            if metadata(info)!={k:entry[k] for k in metadata(info)} or os.readlink(name,dir_fd=source_parent)!=entry['link']:
                                raise Refused('A live DATA link changed during migration')
                        finally:os.close(source_parent)
                        os.symlink(entry['link'],name,dir_fd=out_parent);os.chown(name,entry['uid'],entry['gid'],dir_fd=out_parent,follow_symlinks=False)
                    else:
                        inp=open_relative(source_fd,path);out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=out_parent)
                        try:
                            assert_entry(inp,entry);digest=hashlib.sha256();remaining=entry['bytes']
                            while remaining:
                                data=os.read(inp,min(CHUNK,remaining))
                                if not data:raise Refused('Live DATA file was truncated during migration')
                                digest.update(data);remaining-=len(data);offset=0
                                while offset<len(data):
                                    written=os.write(out,data[offset:])
                                    if written<=0:raise Refused('Installed DATA write did not make progress')
                                    offset+=written
                                done+=len(data)
                                progress('progress',phase='preserve-user-data',percent=done*100//max(1,plan['bytes']),bytes=done,total=plan['bytes'])
                            if os.read(inp,1):raise Refused('Live DATA file grew during migration')
                            assert_entry(inp,entry);os.fchown(out,entry['uid'],entry['gid']);os.fchmod(out,entry['mode']);os.fsync(out)
                            digests.append((path,digest.hexdigest(),entry['bytes']))
                        finally:os.close(inp);os.close(out)
                    os.fsync(out_parent)
                finally:os.close(out_parent)
            verified=0
            for path,expected,size in digests:
                fd=open_relative(stage_fd,path);digest=hashlib.sha256()
                try:
                    entry=by_path[path]
                    info=os.fstat(fd)
                    if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_size)!=(entry['uid'],entry['gid'],entry['mode'],size):
                        raise Refused('Installed DATA ownership or permissions differ after copying')
                    while True:
                        data=os.read(fd,CHUNK)
                        if not data:break
                        digest.update(data)
                finally:os.close(fd)
                if digest.hexdigest()!=expected:raise Refused('Installed DATA file readback did not match its live source')
                verified+=size;progress('progress',phase='verify-user-data',percent=verified*100//max(1,plan['bytes']),bytes=verified,total=plan['bytes'])
            for entry in plan['entries']:
                if entry['kind']!='symlink':continue
                parent,_,name=entry['path'].rpartition('/')
                fd=open_relative(stage_fd,parent,os.O_RDONLY|os.O_DIRECTORY)
                try:
                    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                    if (not stat.S_ISLNK(info.st_mode) or (info.st_uid,info.st_gid)!=(entry['uid'],entry['gid']) or
                        os.readlink(name,dir_fd=fd)!=entry['link']):raise Refused('Installed DATA link readback differs from its source')
                finally:os.close(fd)
            # Recheck the complete live manifest, including new/deleted files.
            newest,_=inventory(source)
            if manifest_sha(newest)!=plan['manifest_sha256']:raise Refused('Live DATA changed during verification; installation is incomplete')
            for entry in reversed(plan['entries']):
                if entry['kind']!='directory':continue
                fd=open_relative(stage_fd,entry['path'],os.O_RDONLY|os.O_DIRECTORY)
                try:
                    os.fchown(fd,entry['uid'],entry['gid']);os.fchmod(fd,entry['mode']);os.fsync(fd)
                    info=os.fstat(fd)
                    if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(entry['uid'],entry['gid'],entry['mode']):
                        raise Refused('Installed DATA directory ownership or permissions differ')
                finally:os.close(fd)
            # Publish authentication last, only after every copied file passed.
            for name in ROOTS:
                if name in os.listdir(stage_fd):os.rename(name,name,src_dir_fd=stage_fd,dst_dir_fd=destination_fd)
            os.fsync(destination_fd)
        finally:os.close(stage_fd)
        os.rmdir(stage,dir_fd=destination_fd)
        return dict(files=plan['files'],bytes=done,rom_files=plan['rom_files'],root_auth_preserved=plan['root_auth_preserved'],verified=True)
    finally:os.close(source_fd);os.close(destination_fd)


def mounted_data():
    fd=directory(SOURCE)
    try:dev=f'{os.major(os.fstat(fd).st_dev)}:{os.minor(os.fstat(fd).st_dev)}'
    finally:os.close(fd)
    found=False
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split()
        if len(fields)<10 or '-' not in fields:continue
        point=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[4]);split=fields.index('-')
        if point=='/data':found=fields[2]==dev and fields[split+1]=='ext4' and 'rw' in fields[5].split(',')
        elif any(point=='/data/'+root or point.startswith('/data/'+root+'/') for root in ROOTS):
            raise Refused('Nested mounts in selected live DATA must be removed before installation')
    if not found:raise Refused('Migration requires the mounted writable live DATA filesystem')


def load_growth():
    fd=directory(GROW.parent)
    try:
        child=os.open(GROW.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        info=os.fstat(child);os.close(child)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode&0o022:raise Refused('Unsafe installed DATA growth helper')
    finally:os.close(fd)
    return runpy.run_path(str(GROW),run_name='surface_seed_growth')


def target_snapshot(helper,target,identity,fs_uuid,fs_bytes):
    disk=helper['current_target'](target,'physical-internal',identity)
    fd=helper['open_readonly_target'](disk)
    try:gpt=helper['read_gpt'](fd,disk['bytes'])
    finally:os.close(fd)
    part=gpt['parts'][2]
    if part['uuid']!=DATA_UUID:raise Refused('The fresh installation DATA partition identity differs')
    device=helper['data_device'](disk,part);fs=helper['read_data_filesystem'](disk,part,device)
    if (fs['uuid'],fs['bytes'])!=(fs_uuid,fs_bytes):raise Refused('The fresh installation DATA filesystem differs')
    info=Path(device).lstat()
    if not stat.S_ISBLK(info.st_mode):raise Refused('Migration requires an ordinary DATA block partition')
    return disk,device,info.st_rdev


def tool(argv):
    result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False,
                          env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    if result.returncode:raise Refused('The controlled DATA mount/unmount utility failed')


def verify_mount(device_number):
    info=MOUNT.stat()
    if info.st_dev!=device_number:raise Refused('The installer mount is not the verified target DATA filesystem')
    matches=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split()
        if len(fields)>9 and fields[2]==f'{os.major(device_number)}:{os.minor(device_number)}':matches.append(fields)
    if len(matches)!=1 or matches[0][4]!=str(MOUNT) or not {'rw','nosuid','nodev','noexec'}<=set(matches[0][5].split(',')):
        raise Refused('Target DATA has unexpected mounts or unsafe mount options')


def mounted_identity(helper,disk,device,number):
    verify_mount(number)
    lines=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split()
        if len(fields)>4 and fields[4]==str(MOUNT) and fields[2]==f'{os.major(number)}:{os.minor(number)}':continue
        lines.append(line)
    # Permit only this helper's verified private mount, retaining every other
    # mounted descendant/boot/swap/transport restriction from the growth policy.
    fresh=helper['inspect_target'](disk['name'],'physical-internal',disk['stable_id'],'\n'.join(lines),
          Path('/proc/swaps').read_text(),Path('/sys/class/dmi/id/product_name').read_text(),Path('/proc/cmdline').read_text())
    if fresh!=disk or Path(device).lstat().st_rdev!=number:raise Refused('Internal hardware identity changed while DATA was mounted')


def apply_seed(target,identity,fs_uuid,fs_bytes,plan):
    guest();mounted_data();helper=load_growth()
    if source_plan(fs_bytes)['manifest_sha256']!=plan['manifest_sha256']:raise Refused('Live DATA changed since preflight')
    initial=target_snapshot(helper,target,identity,fs_uuid,fs_bytes)
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.unshare(0x00020000):raise Refused('Could not isolate the installer mount namespace')
    tool(['mount','--make-rprivate','/'])
    parent=MOUNT.parent
    try:parent.mkdir(mode=0o700)
    except FileExistsError:pass
    pfd=directory(parent)
    try:
        info=os.fstat(pfd)
        if info.st_uid or info.st_mode&0o077:raise Refused('Unsafe installer mount parent')
        os.mkdir(MOUNT.name,0o700,dir_fd=pfd)
    finally:os.close(pfd)
    mounted=False;block=None
    try:
        newest=target_snapshot(helper,target,identity,fs_uuid,fs_bytes)
        if newest!=initial:raise Refused('Internal target changed immediately before DATA mount')
        block=os.open(initial[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        info=os.fstat(block)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev!=initial[2]:raise Refused('DATA block node changed before mount')
        # --no-canonicalize keeps /proc/self/fd bound to this opened device.
        result=subprocess.run(['mount','--no-canonicalize','-t','ext4','-o','rw,nosuid,nodev,noexec',f'/proc/self/fd/{block}',str(MOUNT)],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,pass_fds=(block,),check=False)
        if result.returncode:raise Refused('Could not mount the verified fresh target DATA partition')
        mounted=True;mounted_identity(helper,*initial)
        result=copy_tree(SOURCE,MOUNT,plan)
        mounted_identity(helper,*initial);os.sync()
    finally:
        if block is not None:os.close(block)
        if mounted:tool(['umount',str(MOUNT)])
        MOUNT.rmdir()
    final=target_snapshot(helper,target,identity,fs_uuid,fs_bytes)
    if final!=initial:raise Refused('Internal target changed after migration')
    return dict(event='seed-complete',target=target,target_id=identity,**result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--plan',action='store_true');mode.add_argument('--execute',action='store_true')
    parser.add_argument('--target');parser.add_argument('--expected-target-id');parser.add_argument('--expected-data-uuid');parser.add_argument('--expected-data-bytes',type=int,required=True)
    parser.add_argument('--manifest-sha256');args=parser.parse_args()
    try:
        guest()
        with library_lock():
            mounted_data();plan=source_plan(args.expected_data_bytes)
            if args.plan:emit('seed-plan',**public_plan(plan));return 0
            if not all((args.target,args.expected_target_id,args.expected_data_uuid,args.manifest_sha256)) or args.manifest_sha256!=plan['manifest_sha256']:
                raise Refused('Execution requires the reviewed internal target, DATA identity and unchanged live manifest')
            result=apply_seed(args.target,args.expected_target_id,args.expected_data_uuid,args.expected_data_bytes,plan)
            emit(**result);return 0
    except (OSError,ValueError,subprocess.SubprocessError) as error:emit('seed-error',message=str(error));return 2


if __name__=='__main__':raise SystemExit(main())
