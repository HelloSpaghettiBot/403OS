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
                   "-display", "gtk" if args.visible else "none"]
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
        image = ROOT / "output/images/surface-go.img"
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


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["live", "install", "test"])
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--test-proton", action="store_true",
                        help="Explicit installed-VM Proton/runtime download and Windows command probe; requires --disk")
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
    parser.add_argument("--catalog", type=Path,default=ROOT / "artifacts/library/catalog.tsv")
    parser.add_argument("--ovmf", default="/usr/share/edk2/ovmf/OVMF_CODE.fd")
    args = parser.parse_args(argv)
    if args.keep_open and (not args.visible or args.mode == "install"):
        parser.error("--keep-open requires --visible and a live or test boot")
    if args.test_proton and (args.mode != "test" or args.disk is None):
        parser.error("--test-proton requires mode=test and an explicit existing --disk")
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
        image = ROOT / "output/images/surface-go.img"
        if not image.is_file():
            raise FileNotFoundError("Build output/images/surface-go.img first")
        summary["image_sha256"] = hashlib.file_digest(image.open("rb"), "sha256").hexdigest()
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
        else:
            vm.wait_for("SURFACE_BROWSER_DISPLAY_READY",300)
            vm.screenshot("browser.png")
            vm.wait_for("SURFACE_BROWSER_CLOSE_INPUT_READY",45)
            vm.chord(["ctrl","w"])
            summary["browser_close_input"]="QMP Ctrl+W"
            vm.wait_for("SURFACE_APP_CHECK_END",1800 if args.test_proton else 120)
            vm.wait_for("SURFACE_READY graphics=wayland")
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
            for feature,marker in {
                "browser_runtime_launch_exit":"SURFACE_BROWSER_PASS",
                "moonlight_binary_runtime":"SURFACE_MOONLIGHT_PASS",
                "tailscale_daemon_runtime":"SURFACE_TAILSCALE_PASS",
                "wine64_windows_command_runtime":"SURFACE_WINE_PASS",
            }.items():
                summary["checks"][feature]="PASS" if marker in app_log else "FAIL"
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
            if args.keep_open:
                vm.key("left")
                vm.screenshot("demo.png")
                summary["status"] = "RUNNING"
                summary["disk"] = str(disk)
                summary["checks"]["ui_clean_poweroff"] = "NOT_RUN"
                (directory / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
                print(f"HOME ready for interaction: {directory}", flush=True)
                vm.process.wait()
            else:
                vm.key("down")
                vm.key("left")
                vm.key("ret")
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
