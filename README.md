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
