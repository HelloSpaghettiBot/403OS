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

Games in local `/data/roms` and safely mounted USB/removable-SD libraries are
discovered automatically and become available in HOME without a manual scan.
Title search uses the discovered catalog. Ordinary discovery makes no network
requests; missing cover downloads require an explicit action. External game
volumes remain read only, and incomplete discovery must retain the prior catalog.

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

Buildroot produces two disk artifacts:

- `surface-go.img`: a live USB with its installation payload inside DATA.
- `surface-go-base.img`: a pristine target image with empty DATA.

`scripts/build-iso.sh` packages that pristine base into
`403os-surface-go1-x86_64.iso`, a UEFI El Torito/GPT hybrid installer. Its live
root uses a RAM overlay and temporary DATA bounded by a shared 1536 MiB tmpfs.
The embedded installation image remains read only and checksum verified.
Fresh installation migrates supported live settings and profiles. ISO sessions
cannot update or restore an existing installation because their temporary
storage cannot retain a recovery backup. Persistent USB disk images remain the
update medium. Public releases exclude personalized DATA and credentials.

The outer USB embeds the sparse base as
`/data/SurfaceGoGamingOS/surface-go.img` with a checksum sidecar. Source
discovery is automatic. The inner image has no nested installer payload.
An internal installation verifies the embedded source, requires selection
and an exact destructive confirmation for the target disk, clones/readbacks
it, and expands only the fresh unmounted DATA filesystem.

After cloning and expansion, the physical installer migrates supported local
live-USB DATA into the fresh target: games, BIOS files, saves, settings,
application data and profiles, including existing password hashes. Preflight
checks capacity and source paths before erasure. Migration preserves ownership
and permissions, verifies file contents, and refuses changed source data or
unsupported external catalog references. It does not preserve files already
on the selected target or copy the nested installer payload. Authentication
remains locked at the next boot and existing profile passwords remain valid.

USB writing and internal installation are distinct destructive operations.
Each requires review of the actual target; no unattended real-device erase is
permitted. Live-test before internal installation, and document backup,
recovery and reinstall procedures. A normal image flash is not secure erasure.

An offline update boots from newer live USB media and updates an existing,
unmounted internal 403 OS disk with compatible EFI/root partition geometry.
It verifies the source, saves and reads back the old EFI/root on live DATA,
then replaces and verifies only those two system partitions. Expected partition
hashes are bound to the initial whole-source checksum. Installed DATA and GPT
remain untouched. Updates have a separate Cancel-default review and explicit
target confirmation. Recovery from a retained verified system backup is an
explicit administrator action; this is not an atomic A/B or network updater.

Installed systems check the public GitHub stable-release API after startup
and daily, with bounded requests and retries. Settings exposes an explicit
check and release link. These read-only checks do not install software; the
offline updater remains the installation path. Build identity comes from
`config/os-version.json`; release tags use `vMAJOR.MINOR.PATCH` and the matching
asset is `403os-surface-go1-x86_64.img.xz`, with a GitHub SHA-256 asset digest.

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
