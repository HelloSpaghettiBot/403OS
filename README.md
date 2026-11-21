# Surface Go Gaming OS

An original C++/SDL3 HOME shell and Buildroot Linux image for the first
generation Microsoft Surface Go. Started November 19, 2026 from the owner's
8-page PDF. This is a development build with incomplete acceptance. See [status](docs/STATUS.md)
for executed checks and missing features. Initial source commit `6e4f165` is
published at [HelloSpaghettiBot/403OS](https://github.com/HelloSpaghettiBot/403OS).
This is source publication, not a binary release; images and private artifacts
are excluded.

The original HOME UI uses a status strip, horizontal app carousel, focus
outline and eight-item dock. Keyboard arrows/Enter/Escape/Tab, gamepad
D-pad/A/B/Start, and touch are implemented. Missing apps produce explicit
errors. System information comes from sysfs; an unavailable adapter or battery
is never represented as a simulated working device.

While an app runs, Guide/Start opens HOME controls. Ctrl+Alt+Home or a
three-finger touch closes the app with a save/exit grace and returns HOME.
Cog also supports Ctrl+W. The global shortcut parser is tested; physical
Surface touchscreen behavior still needs live validation.

The new Settings → **Display resolution** flow lists modes reported by the
connected DRM output and offers native/preferred mode. Apply restarts the
compositor and HOME, with a 45-second trial: choose **Keep** to persist it,
or the previous mode returns automatically. This OS setting is separate from
Moonlight stream resolution. Its rebuild and runtime validation are pending;
the currently flashed USB does not yet include it.

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

The build produces two images with separate checksums:

| Artifact under `output/images/` | Purpose |
| --- | --- |
| `surface-go.img` | Bootable live USB; DATA contains the installer payload and checksum |
| `surface-go-base.img` | Pristine image installed to the reviewed target; DATA starts empty |
| `SHA256SUMS`, `surface-go-base.img.sha256` | Checksums for the two artifacts and base payload |
| `installer-media.json` | Image identities and embedded-payload verification receipt |

Both images contain EFI, ext4 root and 4 GiB DATA. The outer USB embeds the
sparse base at `/data/SurfaceGoGamingOS/surface-go.img` with its `.sha256`
sidecar. Settings → **Install to internal storage** discovers it automatically;
no second USB or manual staging is required. The inner image contains no
installer image, avoiding recursive payloads. The VM installer mounts the
serialized outer media's DATA read only and clones that embedded file.
Installer provisioning uses an explicit VM-only direct-kernel guard; live and
installed acceptance use UEFI, and installed boots detach the installer media.

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
Moonlight, Tailscale, Wine64 and UMU. The preceding pristine image passed a
64 GiB VM installation, three detached UEFI boots including 3 GiB/AHCI,
Tailscale state persistence, and a live run. All implemented runtime/audio/
browser/HOME-return checks passed; overall boot results remain PARTIAL for
physical hardware, recovery and enrolled streaming. The new self-contained
USB packaging compiled with embedded-payload readback verified. Display
settings require another rebuild; complete live/install/display checks and
USB replacement remain pending. See the
status and VM report for exact artifact identities and evidence.

HOME includes guided official PokeMMO setup, owner-supplied Digimon World 3
Online import and Windows application selection. Proton and its Steam runtime
require explicit first-run setup with at least 6 GiB free. No game clients,
ROMs, BIOS files or account credentials ship in the image. See
[Windows compatibility](docs/WINDOWS_COMPATIBILITY.md).

The current source passes 210 Python tests and five native C++ test
executables. Physical Surface boot, graphics, touch, radios, audio and power
remain untested. The installer requires target review and an exact typed
destructive confirmation; no Surface internal erase is authorized.

The owner authorized replacing the entire reviewed H USB, including its old
files. An earlier image's flash/readback passed; it requires replacement with
the new self-contained image after validation. The guarded flasher checks
private stable identity, image checksum and full readback. This is normal
flashing, not secure full-media erasure. See
[installation and recovery](docs/INSTALL_RECOVERY.md) and
[remote PC setup](docs/REMOTE_PLAY.md).

Project source license: MIT (see `ui/LICENSE`); upstream components retain
their own licenses. Run Buildroot `legal-info` before distribution. No game
commercial artwork, ROMs, console BIOS images or credentials are bundled here.
Redistributable hardware firmware has its own upstream notices.
