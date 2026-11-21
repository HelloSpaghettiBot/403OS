#!/usr/bin/env python3
"""QEMU harness. Only creates regular files below artifacts/vm-tests.

Installation deliberately uses the image's kernel + readonly rootfs with an
explicit boot guard, rather than weakening the production EFI menu. All live
and installed-disk boot tests use OVMF/UEFI, without direct kernel boot.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import struct
import time

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts/vm-tests"


def inspect_audio_capture(path):
    """Read QEMU's canonical PCM WAV without modifying the recording.

    wavaudio.c starts with zero RIFF/data sizes and fills them at voice
    teardown. Some QEMU versions leave those fields unset at clean VM exit.
    Only that exact header form may use the observed regular-file length.
    """
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as source:
        before=os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 44<=before.st_size<=128*1024*1024:
            raise RuntimeError('QEMU audio capture is not a bounded regular WAV file')
        header=source.read(44)
        if len(header)!=44 or header[:4]!=b'RIFF' or header[8:16]!=b'WAVEfmt ' or header[36:40]!=b'data':
            raise RuntimeError('QEMU audio capture has an unexpected WAV chunk layout')
        riff_size,=struct.unpack_from('<I',header,4)
        format_size,pcm,channels,rate,byte_rate,alignment,bits=struct.unpack_from('<IHHIIHH',header,16)
        data_size,=struct.unpack_from('<I',header,40)
        if (format_size,pcm,channels,rate,byte_rate,alignment,bits)!=(16,1,2,44100,176400,4,16):
            raise RuntimeError('QEMU audio capture is not the configured 44100 Hz 16-bit stereo PCM')
        observed=before.st_size-44
        finalized=riff_size==before.st_size-8 and data_size==observed
        zero_sizes=riff_size==0 and data_size==0
        if not (finalized or zero_sizes) or observed%alignment:
            raise RuntimeError('QEMU audio capture sizes are inconsistent or its PCM frames are truncated')
        frames=observed//alignment
        if not rate//2<=frames<=rate*600:
            raise RuntimeError('QEMU audio capture duration is outside the 0.5 to 600 second bound')
        digest=hashlib.sha256(header);peak=0;signal_frames=0
        remaining=observed
        while remaining:
            block=source.read(min(65536,remaining))
            if not block or len(block)%alignment:
                raise RuntimeError('QEMU audio capture changed or lost complete PCM frames during reading')
            digest.update(block);remaining-=len(block)
            for left,right in struct.iter_unpack('<hh',block):
                amplitude=max(abs(left),abs(right));peak=max(peak,amplitude)
                if amplitude>=100:signal_frames+=1
        after=os.fstat(source.fileno())
        if source.read(1) or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            raise RuntimeError('QEMU audio capture changed during verification')
    return {'path':str(path),'sha256':digest.hexdigest(),'header_finalized':finalized,
            'container_status':'finalized' if finalized else 'qemu_zero_size_header',
            'sample_rate':rate,'channels':channels,'sample_bits':bits,'frames':frames,
            'duration_seconds':round(frames/rate,6),'peak':peak,'signal_frames':signal_frames}


def require_audio_evidence(capture,guest_audio_pass):
    if not guest_audio_pass:
        raise RuntimeError('Guest did not report successful ALSA playback')
    if capture['peak']<100 or capture['signal_frames']<capture['sample_rate']//4:
        raise RuntimeError('Guest audio did not produce at least 0.25 seconds of non-silent QEMU PCM samples')


def safe_file(path):
    path = Path(path)
    resolved = path.resolve()
    if "," in str(resolved):
        raise ValueError("Commas are not allowed in QEMU disk paths")
    if not resolved.is_relative_to(ARTIFACTS.resolve()) or path.is_symlink():
        raise ValueError(f"Target must be an ordinary file under {ARTIFACTS}")
    if path.exists() and not path.is_file():
        raise ValueError("Refusing a device, directory or special file")
    return resolved


def prepare_test_options(directory, args, disk, install=False, media=False):
    """Explicit installed-VM request only; the guest can never write this share."""
    if not getattr(args, "test_proton", False):
        return None
    if args.mode != "test" or install or media or args.disk is None:
        raise ValueError("--test-proton requires mode=test and an explicit existing --disk")
    if safe_file(args.disk) != safe_file(disk) or not safe_file(disk).is_file():
        raise ValueError("--test-proton requires the reviewed existing installed virtual disk")
    directory = Path(directory)
    if not directory.resolve().is_relative_to(ARTIFACTS.resolve()) or directory.is_symlink() or "," in str(directory):
        raise ValueError("Test options must stay inside this VM artifacts run")
    options = directory / "test-options"
    options.mkdir(mode=0o700)
    request = options / "request.json"
    descriptor = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"schema": "surface-vm-test-options-v1", "test_proton": True}, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return options


def record_proton_result(summary, log, requested):
    accepted = bool(re.search(r"^SURFACE_PROTON_REQUEST requested=True(?:\s|$)", log, re.M))
    passed = bool(re.search(r"^SURFACE_PROTON_PASS(?:\s|$)", log, re.M))
    failed = bool(re.search(r"^SURFACE_PROTON_FAIL(?:\s|$)", log, re.M))
    status = "PASS" if requested and accepted and passed and not failed else "FAIL" if requested or accepted or passed or failed else "NOT_REQUESTED"
    summary["proton_test"] = {"requested": requested, "guest_requested": accepted,
                              "status": status, "gameplay_verified": False}
    if status != "NOT_REQUESTED":
        summary["checks"]["proton_runtime_setup_windows_command"] = status


class VM:
    def __init__(self, directory, args, disk, install=False, media=False):
        self.directory = directory
        self.log = directory / "serial.log"
        self.qmp_path = Path(os.environ["XDG_RUNTIME_DIR"]) / f"sgo-{os.getpid()}.sock"
        if self.qmp_path.exists():
            raise RuntimeError("QMP path already exists")
        self.connection = None
        self.stream = None
        firmware = Path(args.ovmf)
        if "," in str(firmware):
            raise ValueError("Commas are not allowed in firmware paths")
        variables = firmware.with_name("OVMF_VARS.fd")
        if not firmware.is_file() or not variables.is_file():
            raise RuntimeError("OVMF_CODE.fd and OVMF_VARS.fd are required")
        shutil.copyfile(variables, directory / "OVMF_VARS.fd")
        accelerator = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
        memory, cpus = args.memory, args.cpus
        command = ["qemu-system-x86_64", "-name", "Surface Go OS disposable VM",
                   "-machine", f"q35,accel={accelerator}", "-cpu", "qemu64",
                   "-smp", f"{cpus},sockets=1,cores=2,threads={cpus // 2}",
                   "-m", str(memory), "-no-reboot",
                   "-smbios", "type=1,product=Surface Go OS Disposable Test VM",
                   "-drive", f"if=pflash,format=raw,readonly=on,file={firmware}",
                   "-drive", f"if=pflash,format=raw,file={directory / 'OVMF_VARS.fd'}",
                   "-device", f"virtio-vga,xres={args.width},yres={args.height}",
                   "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd",
                   "-device", "usb-mouse", "-device", "usb-tablet",
                   "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0",
                   "-audiodev", f"driver=wav,id=audio0,path={directory / 'audio.wav'}", "-device", "intel-hda",
                   "-device", "hda-duplex,audiodev=audio0",
                   "-serial", f"file:{self.log}", "-monitor", "none",
                   "-qmp", f"unix:{self.qmp_path},server=on,wait=off",
                   "-display", "sdl,gl=off" if args.visible else "none"]
        options = prepare_test_options(directory, args, disk, install=install, media=media)
        if options is not None:
            command += ["-virtfs", f"local,id=test_options,path={options},mount_tag=surface_test_options,security_model=none,readonly=on"]
        if args.library_root and not install:
            library_root=args.library_root.resolve(strict=True)
            if not library_root.is_dir() or "," in str(library_root):
                raise ValueError("Library root must be an existing directory without commas")
            catalog=args.catalog.resolve(strict=True)
            index=directory / "library-index"
            index.mkdir()
            (index / "covers").mkdir()
            rows=[]
            for line in catalog.read_text().splitlines():
                parts=line.split("\t")
                if len(parts)<4:
                    continue
                rom=Path(parts[3]).resolve(strict=True)
                if not rom.is_relative_to(library_root) or not rom.is_file():
                    raise ValueError("Catalog ROM path escapes the explicitly shared library")
                parts[3]="/library/roms/"+str(rom.relative_to(library_root))
                if len(parts)>4 and parts[4]:
                    cover=Path(parts[4]).resolve(strict=True)
                    if not cover.is_relative_to((ROOT / "artifacts/library").resolve()) or not cover.is_file():
                        raise ValueError("Private cover must be a regular file under artifacts/library")
                    target=index / "covers" / (parts[0]+".sgo")
                    shutil.copyfile(cover,target)
                    parts[4]="/library/index/covers/"+target.name
                rows.append("\t".join(parts))
            (index / "catalog.tsv").write_text("\n".join(rows)+"\n")
            command += ["-virtfs",f"local,id=games,path={library_root},mount_tag=surface_games,security_model=none,readonly=on,multidevs=remap",
                        "-virtfs",f"local,id=index,path={index},mount_tag=surface_index,security_model=none,readonly=on"]
        image = args.image.resolve(strict=True)
        if args.test_install_ui:
            ui_disk=directory/'ui-install-target.qcow2'
            create_disk(ui_disk)
            command += ['-drive',f'file={ui_disk},if=none,id=uitarget,format=qcow2,discard=unmap,detect-zeroes=unmap',
                        '-device','ide-hd,drive=uitarget,bus=ide.0,serial=SURFACE_UI_TARGET']
        if install:
            command += ["-drive", f"file={image},if=none,id=media,format=raw,readonly=on",
                        "-device", "virtio-blk-pci,drive=media,serial=SURFACE_MEDIA",
                        "-drive", f"file={safe_file(disk)},if=none,id=installtarget,format=qcow2,discard=unmap,detect-zeroes=unmap",
                        "-device", "virtio-blk-pci,drive=installtarget,serial=SURFACE_TEST_TARGET",
                        "-kernel", str(ROOT / "output/images/bzImage"),
                        "-append", "root=/dev/vda2 rootwait ro console=ttyS0,115200 surface.vm-install=1"]
        elif args.sata:
            command += ["-drive", f"file={safe_file(disk)},if=none,id=installed,format=qcow2,discard=unmap,detect-zeroes=unmap",
                        "-device", "ide-hd,drive=installed,bus=ide.0"]
        else:
            command += ["-drive", f"file={safe_file(disk)},if=virtio,format=qcow2,discard=unmap,detect-zeroes=unmap"]
        # No installation media at all in installed-disk tests.
        (directory / "command.json").write_text(json.dumps(command, indent=2))
        self.stderr = open(directory / "qemu.log", "wb")
        self.process = subprocess.Popen(command, stdout=self.stderr, stderr=self.stderr)
        self.metadata = {"accelerator": accelerator, "memory_mib": memory,
                         "vcpus": cpus, "installer_attached": install or media,
                         "direct_kernel_boot": install, "sata": args.sata,
                         "proton_requested": bool(getattr(args, "test_proton", False))}

    def wait_for(self, token, timeout=180, after=0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.log.read_text(errors="replace") if self.log.exists() else ""
            if token in data[after:]:
                return data
            if self.process.poll() is not None:
                raise RuntimeError(f"QEMU exited ({self.process.returncode}) before {token}; see {self.directory}")
            time.sleep(0.2)
        if self.process.poll() is None:
            self.screenshot('timeout.png')
        raise TimeoutError(f"No {token} within {timeout}s; see {self.directory}")

    def qmp(self, command, arguments=None):
        if self.connection is None:
            self.connection = socket.socket(socket.AF_UNIX)
            self.connection.settimeout(10)
            self.connection.connect(str(self.qmp_path))
            self.stream = self.connection.makefile("rwb", buffering=0)
            self.stream.readline()
            self.qmp("qmp_capabilities")
        request = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.stream.write(json.dumps(request).encode() + b"\n")
        while True:
            response = json.loads(self.stream.readline())
            if "error" in response:
                raise RuntimeError(response["error"])
            if "return" in response:
                return response["return"]

    def key(self, key):
        self.qmp("send-key", {"keys": [{"type": "qcode", "data": key}], "hold-time": 80})
        time.sleep(0.2)

    def chord(self, keys):
        self.qmp("send-key", {"keys": [{"type": "qcode", "data": key} for key in keys], "hold-time": 100})
        time.sleep(0.2)

    def fixture_text(self, value):
        if not re.fullmatch('[a-z0-9]+',value):
            raise ValueError('Unsupported disposable VM fixture input')
        for character in value:self.key(character)

    def click_ui(self, x, y):
        # Convert the real SDL letterboxed logical canvas to USB-tablet pixels.
        time.sleep(.5)
        width,height=struct.unpack('>II',self.screenshot('pointer-position.png').read_bytes()[16:24])
        scale=min(width/1600,height/1066)
        px=(width-1600*scale)/2+x*scale;py=(height-1066*scale)/2+y*scale
        if not 0<=px<width or not 0<=py<height:raise ValueError('Pointer outside VM display')
        tablets=[device for device in self.qmp('query-mice') if device.get('absolute')]
        if len(tablets)!=1:raise RuntimeError('Expected exactly one absolute VM tablet')
        self.qmp('human-monitor-command',{'command-line':'mouse_set '+str(tablets[0]['index'])})
        self.qmp('input-send-event',{'events':[
            {'type':'abs','data':{'axis':'x','value':round(px*32767/(width-1))}},
            {'type':'abs','data':{'axis':'y','value':round(py*32767/(height-1))}}]})
        time.sleep(.3)
        self.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':True,'button':'left'}}]})
        time.sleep(.1)
        self.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':False,'button':'left'}}]})
        time.sleep(.3)

    def screenshot(self, name):
        target = self.directory / name
        self.qmp("screendump", {"filename": str(target), "format": "png"})
        return target

    def changed_screenshot(self, name, previous, timeout=8):
        # Wayland/compositor presentation follows the serial input acknowledgement.
        # Poll actual output rather than assuming a fixed software-render frame time.
        deadline=time.monotonic()+timeout
        original=previous.read_bytes()
        while True:
            target=self.screenshot(name)
            if target.read_bytes()!=original:
                return target
            if time.monotonic()>=deadline:
                raise RuntimeError("Navigation was acknowledged but no changed framebuffer was presented")
            time.sleep(0.15)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.stream:
            self.stream.close()
        if self.connection:
            self.connection.close()
        self.stderr.close()
        self.qmp_path.unlink(missing_ok=True)


def create_disk(path):
    path = safe_file(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(path), "64G"], check=True)


def create_live_disk(path, image):
    path = safe_file(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "raw", "-b", str(image), str(path)], check=True)


def serial_json(vm, prefix, after=0, predicate=None, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        for line in reversed(vm.log.read_text(errors='replace')[after:].splitlines()):
            if line.startswith(prefix):
                # A serial write can be observed before its final bytes arrive.
                try:value=json.loads(line[len(prefix):])
                except json.JSONDecodeError:continue
                if predicate is None or predicate(value):return value
        if vm.process.poll() is not None:raise RuntimeError('Guest stopped during display settings test')
        time.sleep(.1)
    raise TimeoutError('Display UI did not report the requested state')


def authenticate_profile(vm, summary, required=False):
    """Exercise the production enrollment/login UI on disposable VM disks.

    This deliberately synthetic password is used only by the host test driver.
    The shipped images stay pristine and contain no password or auth bypass.
    """
    password='surfacevmfixture2026'
    startup=vm.log.read_text(errors='replace')
    if 'SURFACE_PROFILE_PROTOCOL 1' not in startup:
        if required:raise RuntimeError('This image did not advertise the required profile UI')
        return

    def offset():return len(vm.log.read_text(errors='replace'))
    deadline=time.monotonic()+45
    while True:
        data=vm.log.read_text(errors='replace')
        setup='SURFACE_PROFILE_SETUP_REQUIRED' in data
        login='SURFACE_PROFILE_LOGIN_REQUIRED' in data
        if setup or login:break
        if vm.process.poll() is not None:raise RuntimeError('Guest stopped before profile authentication')
        if time.monotonic()>=deadline:raise TimeoutError('Profile UI did not request authentication')
        time.sleep(.1)
    evidence=dict(action='enroll-root' if setup else 'login-root', method='QMP production UI',uid=0)
    if setup:
        vm.wait_for('SURFACE_PROFILE_PASSWORD_READY',30)
        vm.fixture_text(password);position=offset();vm.key('ret')
        confirm=vm.wait_for('SURFACE_PROFILE_PASSWORD_READY',30,after=position)
        if 'SURFACE_PROFILE_AUTH_SUCCESS' in confirm[position:]:
            raise RuntimeError('Root enrolled before confirming its password')
    else:
        position=offset();vm.key('ret')
        vm.wait_for('SURFACE_PROFILE_PASSWORD_READY',30,after=position)
        if required:
            vm.fixture_text('wrongvmfixture2026');position=offset();vm.key('ret')
            failed=vm.wait_for('SURFACE_PROFILE_AUTH_FAILED',30,after=position)
            if 'SURFACE_PROFILE_AUTH_SUCCESS' in failed[position:]:
                raise RuntimeError('An incorrect password authenticated the profile')
            evidence['incorrect_password_refused']='PASS'
            time.sleep(2);position=offset();vm.key('ret')
            vm.wait_for('SURFACE_PROFILE_PASSWORD_READY',30,after=position)
    vm.fixture_text(password);position=offset();vm.key('ret')
    authenticated=vm.wait_for('SURFACE_PROFILE_AUTH_SUCCESS',45,after=position)
    if not re.search(r'^SURFACE_PROFILE_AUTH_SUCCESS name=root uid=0(?:\s|$)',authenticated[position:],re.M):
        raise RuntimeError('Expected an authenticated root profile with UID zero')
    evidence['password_authentication']='PASS'
    summary['profile_authentication']=evidence
    summary['checks']['profile_password_enrollment_or_login']='PASS'


def exercise_profiles(vm, summary, create=False):
    """Create/switch a real Unix profile and launch its browser through HOME."""
    def offset():return len(vm.log.read_text(errors='replace'))
    def open_profiles():
        vm.click_ui(60+6*187+85,904)
        time.sleep(2);vm.screenshot('profiles-settings.png')
        vm.key('up');vm.key('ret');time.sleep(3);vm.screenshot('profiles-panel.png')
    def password(value):
        vm.wait_for('SURFACE_PROFILE_PASSWORD_READY',30,after=position[0])
        vm.fixture_text(value);position[0]=offset();vm.key('ret')
    def signed_in(name,uid):
        value=vm.wait_for('SURFACE_PROFILE_AUTH_SUCCESS',45,after=position[0])
        if f'SURFACE_PROFILE_AUTH_SUCCESS name={name} uid={uid}' not in value[position[0]:]:
            raise RuntimeError('Profile switch authenticated an unexpected Unix identity')

    open_profiles();position=[offset()]
    if create or summary['profile_authentication']['action']=='enroll-root':
        vm.key('down');vm.key('down');vm.key('ret')
        time.sleep(1);vm.screenshot('profile-username.png')
        vm.fixture_text('vmplayer');position[0]=offset();vm.key('ret')
        password('secondaryvmfixture2026');password('secondaryvmfixture2026')
        vm.wait_for('SURFACE_PROFILE_CREATED name=vmplayer uid=2000',45,after=position[0])
    position[0]=offset();vm.key('down');vm.key('ret')
    vm.wait_for('SURFACE_PROFILE_LOGIN_REQUIRED',30,after=position[0])
    vm.screenshot('profiles-login.png')
    vm.key('down');position[0]=offset();vm.key('ret')
    password('secondaryvmfixture2026');signed_in('vmplayer',2000)
    vm.screenshot('profile-secondary-home.png')
    start=offset();vm.click_ui(60+3*187+85,904)
    vm.wait_for('SURFACE_APPLICATION_START',20,after=start)
    application=vm.wait_for('SURFACE_PROFILE_APP_IDENTITY name=vmplayer ',20,after=start)
    line=next(row for row in application[start:].splitlines() if row.startswith('SURFACE_PROFILE_APP_IDENTITY name=vmplayer '))
    values=dict(re.findall(r'([a-z_]+)=([a-z0-9_-]+)',line))
    if any(values.get(key)!='2000' for key in ('real_uid','effective_uid','saved_uid','real_gid','effective_gid','saved_gid')):
        raise RuntimeError('The secondary application retained privileged Unix IDs')
    time.sleep(3);vm.screenshot('browser-secondary-profile.png')
    vm.chord(['ctrl','alt','home'])
    vm.wait_for('SURFACE_GLOBAL_HOME_REQUEST',10,after=start)
    vm.wait_for('SURFACE_APPLICATION_RETURN status=0',15,after=start)
    vm.wait_for('SURFACE_HOME_FOCUS_READY',10,after=start)
    open_profiles();position[0]=offset();vm.key('down');vm.key('ret')
    vm.wait_for('SURFACE_PROFILE_LOGIN_REQUIRED',30,after=position[0])
    position[0]=offset();vm.key('ret');password('surfacevmfixture2026');signed_in('root',0)
    summary['profile_switching']={'secondary_uid':2000,'secondary_application_uid_gid_drop':'PASS',
                                  'browser_launch_global_return':'PASS','switch_back_to_root':'PASS'}
    summary['checks']['profile_switch_and_unprivileged_browser']='PASS'


def display_view(vm, after=0, trial=None, timeout=15):
    return serial_json(vm,'SURFACE_DISPLAY_VIEW ',after,
                       None if trial is None else lambda value:value['trial_active'] is trial,timeout)


def exercise_display(vm, width, height):
    """Operate the real Settings UI and verify scanout, Keep and timed rollback."""
    def offset():return len(vm.log.read_text(errors='replace'))
    def size(name):
        result=struct.unpack('>II',vm.screenshot(name).read_bytes()[16:24])
        return f'{result[0]}x{result[1]}'
    def open_settings():
        start=offset();vm.click_ui(60+6*187+85,904);time.sleep(2)
        vm.key('up')
        if 'SURFACE_PROFILE_PROTOCOL 1' in vm.log.read_text(errors='replace'):vm.key('up')
        vm.key('ret')
        return display_view(vm,start,False)
    def apply(view, chosen):
        modes=[(item['name'],mode) for item in view['outputs'] for mode in item['modes']
               if mode=='preferred' or all(low<=dimension<=high and dimension%2==0
                  for dimension,low,high in zip(map(int,mode.split('x')),(320,240),(3840,2160)))]
        index=modes.index(chosen)+1
        for _ in range(index):vm.key('down')
        vm.key('ret');vm.key('up')
        start=offset();vm.key('ret')
        vm.wait_for('SURFACE_READY graphics=wayland',30,after=start)
        trial=display_view(vm,start,True,30)
        observed=size('display-trial-'+chosen[1]+'.png')
        if observed!=chosen[1]:raise RuntimeError(f'Resolution trial scanout {observed} != {chosen[1]}')
        return trial
    def keep(chosen):
        start=offset();deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            vm.key('ret')
            try:view=display_view(vm,start,False,1)
            except TimeoutError:continue
            if view['saved']!={'output':chosen[0],'mode':chosen[1]}:
                raise RuntimeError('Display Keep did not persist the selected mode')
            return view
        raise TimeoutError('Keep resolution was not accepted through the UI')
    view=open_settings();original=f'{width}x{height}'
    candidates=[(item['name'],mode) for item in view['outputs'] for mode in item['modes']
                if mode!='preferred' and mode!=original and tuple(map(int,mode.split('x'))) <= (width,height)]
    chosen=next((item for item in candidates if item[1]=='1280x960'),None)
    if chosen is None:raise RuntimeError('The VM does not advertise the required lower display test mode')
    native=(chosen[0],original)
    apply(view,chosen);view=keep(chosen)
    vm.screenshot('display-kept.png')
    trial=apply(view,native)
    start=offset()
    vm.wait_for('SURFACE_READY graphics=wayland',65,after=start)
    if size('display-timeout-restored.png')!=chosen[1]:
        raise RuntimeError('Unconfirmed resolution did not restore the last kept scanout')
    view=open_settings()
    if view['saved']!={'output':chosen[0],'mode':chosen[1]} or view['trial_active']:
        raise RuntimeError('Timed resolution rollback damaged confirmed persistent settings')
    apply(view,native);keep(native);vm.key('esc')
    if size('display-native-restored.png')!=original:raise RuntimeError('Final native resolution was not restored')
    return {'mode_list_apply_keep':'PASS','timeout_rollback':'PASS','native_restore':'PASS',
            'trial_mode':chosen[1],'saved_mode':original,'output':chosen[0]}


def exercise_install_ui(vm):
    """Click through the production installer into an explicitly created VM disk."""
    start=len(vm.log.read_text(errors='replace'))
    vm.click_ui(60+6*187+85,904);time.sleep(1)
    for _ in range(10):vm.key('down')
    vm.key('ret')
    listing=vm.wait_for('SURFACE_INSTALL_UI_LIST ',90,after=start)
    match=re.search(r'SURFACE_INSTALL_UI_LIST sources=(\d+) targets=(\d+)',listing[start:])
    if not match or int(match[1])<1 or int(match[2])!=1:
        raise RuntimeError('Click installer did not expose exactly one disposable target and a valid source')
    vm.screenshot('installer-disks.png')
    start=len(vm.log.read_text(errors='replace'));vm.key('ret')
    reviewed=vm.wait_for('SURFACE_INSTALL_UI_REVIEW target=',45,after=start)
    identity=re.search(r'SURFACE_INSTALL_UI_REVIEW target=([^\r\n]+) cancel_selected=1',reviewed[start:])
    if not identity or not identity[1].endswith('SURFACE_UI_TARGET'):
        raise RuntimeError('Installer review does not identify the explicitly serialized disposable SATA target')
    vm.screenshot('installer-review.png')
    start=len(vm.log.read_text(errors='replace'));vm.key('up');vm.key('ret')
    vm.wait_for('SURFACE_INSTALL_UI_COMPLETE ',1200,after=start)
    vm.screenshot('installer-complete.png')
    # Completion defaults to Return HOME; select Power off, then explicitly
    # confirm its second Cancel-default review and require real QEMU shutdown.
    vm.key('up');vm.key('ret');vm.key('up');vm.key('ret');vm.process.wait(timeout=45)
    if vm.process.returncode!=0:raise RuntimeError('GUI installer did not power off cleanly')
    return str(vm.directory/'ui-install-target.qcow2')


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["live", "install", "test"])
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--image",type=Path,default=ROOT/'output/images/surface-go.img',help="Explicit live/installer image, including private media")
    parser.add_argument("--test-proton", action="store_true",
                        help="Explicit installed-VM Proton/runtime download and Windows command probe; requires --disk")
    parser.add_argument("--test-display", action="store_true",help="Exercise installed display mode UI, confirmation and timeout rollback")
    parser.add_argument('--test-profiles',action='store_true',help='Require actual profile enrollment/login and reject an incorrect login password')
    parser.add_argument('--create-test-profile',action='store_true',help='Create the synthetic secondary profile on an existing root-only disposable disk')
    parser.add_argument('--test-rotation',action='store_true',help='Require actual live Weston rotation with preserved browser/compositor processes')
    parser.add_argument('--test-install-ui',action='store_true',help='Test click installer into a newly created disposable 64 GiB SATA disk')
    parser.add_argument("--memory", type=int, choices=[3072, 4096], default=4096)
    parser.add_argument("--cpus", type=int, choices=[2, 4], default=4)
    parser.add_argument("--width", type=int, choices=[1280, 1800], default=1800)
    parser.add_argument("--height", type=int, choices=[800, 1200], default=1200)
    parser.add_argument("--sata", action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--keep-open", action="store_true",
                        help="Keep the visible VM open after boot checks for interactive use")
    parser.add_argument("--library-root", type=Path,
                        help="Explicit read-only user game directory to share with this VM")
    parser.add_argument('--expect-library-games',type=int,default=0,help='Require this many catalog entries and real embedded cover loading')
    parser.add_argument("--catalog", type=Path,default=ROOT / "artifacts/library/catalog.tsv")
    parser.add_argument("--ovmf", default="/usr/share/edk2/ovmf/OVMF_CODE.fd")
    args = parser.parse_args(argv)
    if args.keep_open and (not args.visible or args.mode == "install"):
        parser.error("--keep-open requires --visible and a live or test boot")
    if args.test_proton and (args.mode != "test" or args.disk is None):
        parser.error("--test-proton requires mode=test and an explicit existing --disk")
    if args.test_display and (args.mode != "test" or args.disk is None):
        parser.error("--test-display requires mode=test and an explicit existing --disk")
    if args.test_profiles and args.mode=='install':
        parser.error('--test-profiles requires a live or installed graphical boot')
    if args.create_test_profile and not args.test_profiles:
        parser.error('--create-test-profile requires --test-profiles')
    if args.test_rotation and args.mode=='install':
        parser.error('--test-rotation requires a live or installed graphical boot')
    if args.test_install_ui and (args.mode!='live' or args.keep_open or args.test_display):
        parser.error('--test-install-ui requires a disposable live acceptance run')
    if not 0<=args.expect_library_games<=100000 or args.expect_library_games and args.mode=='install':
        parser.error('--expect-library-games requires a live/installed boot and a bounded nonnegative count')
    return args


def main():
    args = parse_arguments()
    directory = ARTIFACTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.mode}"
    directory.mkdir(parents=True, exist_ok=False)
    summary = {"mode": args.mode, "status": "FAIL", "checks": {}}
    summary["proton_test"] = {"requested": args.test_proton, "guest_requested": False,
                              "status": "NOT_RUN" if args.test_proton else "NOT_REQUESTED",
                              "gameplay_verified": False}
    vm = None
    try:
        image = args.image.resolve(strict=True)
        if args.image.is_symlink():raise ValueError('Image must not be a symlink')
        if not image.is_file():
            raise FileNotFoundError("Build output/images/surface-go.img first")
        summary["image_sha256"] = hashlib.file_digest(image.open("rb"), "sha256").hexdigest()
        base = ROOT / "output/images/surface-go-base.img"
        if not base.is_file() or base.is_symlink():
            raise FileNotFoundError("Build the pristine embedded installer payload first")
        with base.open("rb") as source:
            summary["installer_payload_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        summary["qemu"] = subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).splitlines()[0]
        disk = args.disk or directory / "installed.qcow2"
        if args.mode == "live":
            create_live_disk(disk, image)
        elif args.mode == "install":
            create_disk(disk)
        elif not safe_file(disk).is_file():
            raise FileNotFoundError("Use --disk with the previously installed virtual disk")
        vm = VM(directory, args, disk, install=args.mode == "install", media=args.mode == "live")
        summary.update(vm.metadata)
        if args.mode == "install":
            vm.wait_for("SURFACE_VM_INSTALL_PASS",900)
            vm.process.wait(timeout=60)
            summary["checks"]["guarded_vm_install"] = "PASS"
            if "SURFACE_VM_DATA_GROW_PASS" not in vm.log.read_text(errors="replace"):
                raise RuntimeError("Verified installed DATA expansion was not reported")
            summary["checks"]["pristine_installed_data_expansion"] = "PASS"
            marker = re.search(r"SURFACE_VM_EMBEDDED_PAYLOAD_VERIFIED sha256=([a-f0-9]{64}) bytes=(\d+)", vm.log.read_text(errors="replace"))
            if not marker or marker.group(1) != summary["installer_payload_sha256"] or int(marker.group(2)) != base.stat().st_size:
                raise RuntimeError("VM installation did not verify the expected embedded pristine payload")
            summary["checks"]["embedded_installer_payload"] = "PASS"
        else:
            vm.wait_for("SURFACE_BROWSER_DISPLAY_READY",300)
            vm.screenshot("browser.png")
            vm.wait_for("SURFACE_BROWSER_CLOSE_INPUT_READY",45)
            vm.chord(["ctrl","w"])
            summary["browser_close_input"]="QMP Ctrl+W"
            vm.wait_for("SURFACE_APP_CHECK_END",1800 if args.test_proton else 120)
            vm.wait_for("SURFACE_READY graphics=wayland")
            authenticate_profile(vm,summary,required=args.test_profiles)
            summary['display_configuration']=serial_json(vm,'SURFACE_DISPLAY_STARTUP ',timeout=15)
            vm.wait_for("SURFACE_EFI_PASS")
            summary["checks"]["uefi_graphical_boot"] = "PASS"
            mounts = vm.wait_for("SURFACE_ROOT_MOUNTS_END")
            if " / ext4 " not in mounts or " /data ext4 " not in mounts:
                raise RuntimeError("Root or separate data filesystem not mounted")
            summary["checks"]["root_and_persistent_data_mount"] = "PASS"
            match = re.search(r"SURFACE_DATA_BOOT_COUNT=(\d+)", mounts)
            if not match:
                raise RuntimeError("Persistent data boot counter missing")
            summary["data_boot_count"] = int(match.group(1))
            vm.wait_for("SURFACE_SELF_TEST PASS")
            summary["checks"]["sdl_keyboard_gamepad_injection"] = "PASS"
            idle = vm.wait_for("SURFACE_IDLE_MEMORY VmRSS:", 45)
            summary["idle_memory"] = [line for line in idle.splitlines() if line.startswith("SURFACE_IDLE_MEMORY")]
            before = vm.screenshot("home.png")
            dimensions = struct.unpack(">II", before.read_bytes()[16:24])
            summary["display_pixels"] = list(dimensions)
            if dimensions != (args.width, args.height):
                raise RuntimeError(f"Display mode {dimensions} != requested {(args.width, args.height)}")
            offset = len(vm.log.read_text(errors="replace"))
            vm.key("right")
            vm.wait_for("action=1 tile=1", after=offset)
            after = vm.changed_screenshot("focus.png",before)
            summary["checks"]["qmp_keyboard_focus_and_framebuffer"] = "PASS"
            offset = len(vm.log.read_text(errors="replace"))
            vm.key("tab")
            vm.wait_for("overlay=1", after=offset)
            vm.changed_screenshot("status.png",after)
            vm.key("esc")
            data = vm.wait_for("SURFACE_HTTPS_", 45)
            summary["checks"]["dhcp_dns_https"] = "PASS" if "SURFACE_HTTPS_PASS" in data else "FAIL"
            summary["checks"]["bluetooth_service"] = "PASS" if "SURFACE_BLUETOOTH_SERVICE_PASS" in data else "FAIL"
            app_log=vm.log.read_text(errors="replace")
            record_proton_result(summary,app_log,args.test_proton)
            if args.mode == "live":
                marker = re.search(r"SURFACE_INSTALLER_PAYLOAD_VERIFIED sha256=([a-f0-9]{64}) bytes=(\d+)", app_log)
                if not marker or marker.group(1) != summary["installer_payload_sha256"] or int(marker.group(2)) != base.stat().st_size:
                    raise RuntimeError("Live USB did not discover and verify its expected embedded installer payload")
                summary["checks"]["embedded_installer_payload"] = "PASS"
            for feature,marker in {
                "browser_runtime_launch_exit":"SURFACE_BROWSER_PASS",
                "moonlight_binary_runtime":"SURFACE_MOONLIGHT_PASS",
                "tailscale_daemon_runtime":"SURFACE_TAILSCALE_PASS",
                "wine64_windows_command_runtime":"SURFACE_WINE_PASS",
            }.items():
                summary["checks"][feature]="PASS" if marker in app_log else "FAIL"
            if 'SURFACE_ROTATION_RUNTIME_PASS' in app_log:
                summary['checks']['live_rotation_preserves_running_client']='PASS'
            elif args.test_rotation or 'SURFACE_ROTATION_RUNTIME_FAIL' in app_log:
                raise RuntimeError('The actual live rotation probe did not pass')
            state_match=re.search(r"SURFACE_TAILSCALE_STATE_SHA256=([a-f0-9]{64})",app_log)
            if not state_match:
                raise RuntimeError("Tailscale persistent state checksum missing")
            summary["tailscale_state_sha256"]=state_match.group(1)
            summary["checks"]["tailscale_daemon_persistence"]="PASS" if "SURFACE_TAILSCALE_STATE_PERSISTENCE_PASS" in app_log else "NOT_VERIFIED"
            summary["checks"]["hardware"]="UNTESTED"
            for feature in ["reboot", "interrupted_boot_recovery"]:
                summary["checks"][feature] = "NOT_RUN"
            summary["checks"]["live_tailnet_enrollment_streaming"] = "BLOCKED"
            vm.wait_for("SURFACE_EMULATOR_HOMEBREW_PASS",45)
            vm.wait_for("SURFACE_ALL_EMULATOR_RUNTIMES_PASS",20)
            summary["checks"]["all_eleven_emulator_binary_runtimes"]="PASS"
            summary["checks"]["real_core_homebrew_input_sram_reload"]="PASS"
            summary["checks"]["emulator"]="PASS"
            if args.library_root:
                vm.wait_for("SURFACE_LIBRARY_MOUNT_PASS")
                vm.wait_for("SURFACE_ARTWORK_LOADED /library/index/covers/")
                summary["checks"]["read_only_user_library_and_cover_loading"]="PASS"
            if args.expect_library_games:
                vm.wait_for(f'SURFACE_LIBRARY games={args.expect_library_games} systems=',20)
                vm.wait_for('SURFACE_ARTWORK_LOADED /data/library/covers/',30)
                summary['checks']['embedded_private_library_and_cover_loading']='PASS'
                summary['embedded_library_games']=args.expect_library_games
            # Launch the browser through the real HOME dock, then close it
            # through the global evdev chord while HOME has no keyboard focus.
            offset=len(vm.log.read_text(errors="replace"))
            vm.key("down")
            for _ in range(3):vm.key("right")
            vm.key("ret")
            vm.wait_for("SURFACE_APPLICATION_START",20,after=offset)
            time.sleep(3)
            vm.screenshot("browser-from-home.png")
            vm.chord(["ctrl","alt","home"])
            vm.wait_for("SURFACE_GLOBAL_HOME_REQUEST",10,after=offset)
            vm.wait_for("SURFACE_APPLICATION_RETURN status=0",15,after=offset)
            vm.wait_for("SURFACE_HOME_FOCUS_READY",10,after=offset)
            vm.screenshot("home-after-browser.png")
            summary["checks"]["browser_home_launch_global_return"]="PASS"
            for _ in range(3):vm.key("left")
            vm.key("up")
            if args.test_display:
                summary['display_settings_test']=exercise_display(vm,args.width,args.height)
                summary['checks']['display_apply_confirm_timeout_rollback']='PASS'
            if args.test_profiles:exercise_profiles(vm,summary,create=args.create_test_profile)
            if args.test_install_ui:
                summary['ui_installed_disk']=exercise_install_ui(vm)
                summary['checks']['click_installer_copy_readback_data_growth']='PASS'
                summary['checks']['ui_clean_poweroff']='PASS'
                summary['audio_capture']=inspect_audio_capture(directory/'audio.wav')
                require_audio_evidence(summary['audio_capture'],'SURFACE_AUDIO_PASS' in vm.log.read_text(errors='replace'))
                summary['checks']['audio_playback']='PASS'
            elif args.keep_open:
                vm.key("left")
                vm.screenshot("demo.png")
                summary["status"] = "RUNNING"
                summary["disk"] = str(disk)
                summary["checks"]["ui_clean_poweroff"] = "NOT_RUN"
                (directory / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
                print(f"HOME ready for interaction: {directory}", flush=True)
                vm.process.wait()
            else:
                vm.click_ui(60+7*187+85,904)
                vm.key("ret")
                vm.process.wait(timeout=45)
                if vm.process.returncode != 0:
                    raise RuntimeError(f"QEMU shutdown status {vm.process.returncode}")
                summary["checks"]["ui_clean_poweroff"] = "PASS"
                summary["audio_capture"]=inspect_audio_capture(directory / "audio.wav")
                summary["audio_peak"]=summary["audio_capture"]["peak"]
                require_audio_evidence(summary["audio_capture"],"SURFACE_AUDIO_PASS" in vm.log.read_text(errors="replace"))
                summary["checks"]["audio_playback"]="PASS"
        failures = [name for name, result in summary["checks"].items() if result == "FAIL"]
        if failures:
            raise RuntimeError("Failed VM checks: " + ", ".join(failures))
        summary["status"] = "PARTIAL" if any(v != "PASS" for v in summary["checks"].values()) else "PASS"
        summary["disk"] = str(disk)
        print(json.dumps(summary, indent=2))
    except Exception as error:
        if vm and vm.log.is_file():
            record_proton_result(summary,vm.log.read_text(errors="replace"),args.test_proton)
        summary["error"] = str(error)
        raise
    finally:
        if vm:
            vm.close()
        (directory / "result.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
