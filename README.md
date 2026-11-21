# Surface Go Gaming OS

An original C++/SDL3 HOME shell and Buildroot Linux image for the first
generation Microsoft Surface Go. Started November 19, 2026 from the owner's
8-page PDF. This is a development build with incomplete acceptance. See [status](docs/STATUS.md)
for executed checks and missing features. Source: [HelloSpaghettiBot/403OS](https://github.com/HelloSpaghettiBot/403OS).

The original HOME UI uses a status strip, horizontal app carousel, focus
outline and eight-item dock. Keyboard arrows/Enter/Escape/Tab, gamepad
D-pad/A/B/Start, and touch are implemented. Missing apps produce explicit
errors. System information comes from sysfs; an unavailable adapter or battery
is never represented as a simulated working device.

While an app runs, Guide/Start opens HOME controls. Ctrl+Alt+Home or a
three-finger touch closes the app with a save/exit grace and returns HOME.
Cog also supports Ctrl+W. The global shortcut parser is tested; physical
Surface touchscreen behavior still needs live validation.

Build prerequisites on a Linux host: rootless Podman, a running systemd user
manager, curl, tar, git and mounted FILES drive. No immutable-host packages are
installed. The pinned builder container supplies compilation dependencies.
QEMU, qemu-img and unsigned OVMF firmware are needed for VM checks. Secure
Boot must be disabled for this unsigned development image.

```sh
cd /run/media/system/Files/Projects/SurfaceGoGamingOS
scripts/test.sh
scripts/build.sh
scripts/vm-create.sh                     # live UEFI boot, never a host disk
scripts/vm-install.sh                    # prints path of new 64 GiB qcow2 target
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2
```

Use `--visible` to show QEMU, `--memory 3072 --cpus 2` for a constrained run,
`--sata` for installed-disk AHCI testing, and `--width 1280 --height 800` for a
lower-resolution profile. Artifacts, serial logs, screenshots and JSON results
go under `artifacts/vm-tests/`. A successful QEMU process launch is not a pass;
the harness waits for rendered Wayland HOME, verifies keyboard focus through
the framebuffer and serial events, then requests shutdown through the UI.

Build output is `output/images/surface-go.img` plus `SHA256SUMS`. The image
contains GPT, a UEFI partition, ext4 root and a separate ext4 data partition.
The guarded installer copies the same image to the disposable disk. Installer
provisioning directly boots its kernel with an explicit VM-only boot argument;
live and installed-disk acceptance boots always use OVMF with no direct kernel
and no installer media attached to the installed-disk runs.

Builds use ten workers, a 24 GiB throttle, 32 GiB hard cap, 1 GiB swap and a
single-workload lock. The container has a separate 32 GiB cap. VM host memory
remains capped at 6 GiB, including the mandatory 4 GiB guest. Initial builds
download several GB and can take hours. Incremental builds reuse
`output/` and the download cache on FILES. No uncapped fallback is provided.

For source changes use `scripts/build.sh surface-shell-rebuild` followed by
`scripts/build.sh` to regenerate the filesystem and image. Use
`scripts/build.sh --configure` after defconfig changes. Toolchain or graphics
stack changes may require a clean output tree; preserve logs before doing so.

The default configuration includes eleven emulator engines, Cog/WPE WebKit,
Moonlight, Tailscale, Wine64 and the UMU Proton launcher. All eleven emulator
engines compiled and passed real binary/core runtime probes in the completed
live VM. NES input/save/reload, browser/HOME return, Wine command execution,
networking and non-silent captured audio also passed. The overall VM result is
PARTIAL: installed-disk persistence/recovery, physical hardware and enrolled
streaming remain unverified. See the status and VM report for exact evidence.

HOME includes guided official PokeMMO setup, owner-supplied Digimon World 3
Online import and Windows application selection. Proton and its Steam runtime
require explicit first-run setup with at least 6 GiB free. No game clients,
ROMs, BIOS files or account credentials ship in the image. See
[Windows compatibility](docs/WINDOWS_COMPATIBILITY.md).

The earlier image failed final DATA validation in the 64 GiB VM install.
A corrected image has compiled and 197 Python tests pass; its new VM install,
cold boots and matching live checks remain pending. See status for the exact
image hashes. The installer requires target review and typed destructive
confirmation. Live-test the actual Surface before considering Windows replacement. Surface graphics, touch, radios, audio, power and battery remain
separate physical-device acceptance gates. The latest owner instruction
authorizes erasing the entire reviewed external H USB, including all its
files, and flashing the validated image. The guarded
`installer/flash-usb.py` requires a private identity/checksum plan and matching
live-boot evidence, then verifies the complete image by readback. This does
not authorize erasing the Surface internal disk. The authorized USB flash
and complete image readback have passed; actual Surface boot remains untested.
See [installation and recovery](docs/INSTALL_RECOVERY.md) and
[remote PC setup](docs/REMOTE_PLAY.md) for the review and acceptance workflows.

Project source license: MIT (see `ui/LICENSE`); upstream components retain
their own licenses. Run Buildroot `legal-info` before distribution. No game
commercial artwork, ROMs, console BIOS images or credentials are bundled here.
Redistributable hardware firmware has its own upstream notices.
