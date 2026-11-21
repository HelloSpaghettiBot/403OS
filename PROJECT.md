# 403 OS project scope

403 OS is a dedicated Linux gaming system for Microsoft Surface Go Gen 1
(2018), targeting its Pentium Gold 4415Y, Intel HD Graphics 615, 4/8 GiB RAM,
UEFI and internal eMMC/SSD variants. Product requirements include a native
controller-first shell, real emulator integration, browser and remote-play
apps, deliberate installation, and separate hardware validation.

## Product design

The original C++/SDL3 HOME interface has a horizontal game/app carousel,
visible controller focus, system status strip and app dock. Keyboard, gamepad
and touchscreen are first-class input paths. System status must reflect real
hardware and service state. Crashed or closed applications must return safely
to HOME. Original or appropriately licensed assets are required.

The default image includes eleven emulator engines, Cog/WPE WebKit,
Moonlight, Tailscale and Wine64/UMU. PokeMMO setup downloads the official client
on explicit request; Digimon World 3 Online imports an unchanged user-supplied
client. No copyrighted games, console BIOS, proprietary clients, account data
or credentials belong in the repository or release image.

Display settings must list actual connected DRM modes and native/preferred
mode. A change restarts the compositor/HOME, uses a 45-second trial with
automatic rollback, and persists only after Keep. Stream resolution is a
separate Moonlight setting. Physical mode support must be tested on the device.

## Image and installation model

Buildroot produces two separate artifacts:

- `surface-go.img`: a live USB with its installation payload inside DATA.
- `surface-go-base.img`: a pristine target image with empty DATA.

The outer USB embeds the sparse base as
`/data/SurfaceGoGamingOS/surface-go.img` with a checksum sidecar. Source
discovery is automatic. The inner image has no nested installer payload.
An internal installation verifies the embedded source, requires selection
and an exact destructive confirmation for the target disk, clones/readbacks
it, and expands only the fresh unmounted DATA filesystem.

USB writing and internal installation are distinct destructive operations.
Each requires review of the actual target; no unattended real-device erase is
permitted. Live-test before internal installation, and document backup,
recovery and reinstall procedures. A normal image flash is not secure erasure.

## Engineering and acceptance

Use pinned upstream sources, hashes and licenses, a Buildroot br2-external
image, capped builds, and evidence-based tests. Prefer minimal DRM/KMS/Mesa
and Wayland services without a desktop environment. Support 1800×1200 and
lower-resolution layouts without cropped controls.

Validate the same artifact through UEFI live boot, embedded-payload VM
installation and detached cold boots. Record input, network, browser,
emulator/save, audio, persistence and clean shutdown evidence. Treat QEMU
success as virtual-device evidence; Surface graphics, touch, radios, power
and GameCube performance require physical testing.

Track current results in [STATUS.md](docs/STATUS.md),
[VM_TEST_REPORT.md](docs/VM_TEST_REPORT.md) and the
[roadmap](docs/ROADMAP.md). Do not report unexecuted features as working.
Source is published at [HelloSpaghettiBot/403OS](https://github.com/HelloSpaghettiBot/403OS);
source publication does not constitute a binary release.
