# Installation, backup and recovery

403 OS is a development image. Read [status](STATUS.md) and the
[VM report](VM_TEST_REPORT.md) before using a build. Current self-contained
installer and display flows have disposable VM tests; physical Surface
installation and recovery remain untested.

## Image files

| File under `output/images/` | Purpose |
| --- | --- |
| `surface-go.img` | Complete bootable USB image; write this to USB |
| `surface-go-base.img` | Pristine installation payload with empty DATA |
| `SHA256SUMS`, `surface-go-base.img.sha256` | Checksums for the two artifacts |
| `installer-media.json` | Packaging and embedded-payload readback result |

The USB has EFI, root and 4 GiB DATA partitions. DATA contains the sparse base
as `SurfaceGoGamingOS/surface-go.img` with its `.sha256` sidecar, owned by root
with read-only file permissions. The installation panel discovers it
automatically. No second source USB or manual file staging is required.
Internal installation copies the pristine base, expands DATA, then copies and
verifies supported live-USB games and user data. Installed DATA does not contain
another installer image.

Retain a dated image/checksum pair, source revision and matching test results.
Rebuilding replaces output filenames. A checksum detects changed bytes; it
does not authenticate the publisher of an unsigned image.

## Prepare a live USB

1. Back up anything on the USB you intend to use. Writing the image replaces
   **all partitions and files on that selected USB**.
2. Verify the build's checksums:

   ```sh
   (cd output/images && sha256sum -c SHA256SUMS)
   ```

3. Use an image-writing tool to select `surface-go.img` and the intended whole
   USB device. Review its model, capacity and device identity. Select a whole
   device, not a partition; do not choose an internal or unrelated disk.
4. Unmount that USB's filesystems, write the image, and use the tool's readback
   verification. Require successful completion before safely ejecting it.
5. Boot the USB and check HOME, keyboard/controller input, display, audio,
   networking, app return and shutdown before considering installation.

A normal flash is not secure full-media erasure. Extra capacity remains
outside the image layout and USB DATA stays 4 GiB. Rewriting this USB also
replaces any saves/settings created during earlier live sessions.

The repository includes a development USB helper described in
[USB tooling](../installer/USB_PLAN.md). It uses explicit identity/checksum
records, matching VM evidence, exclusive access and readback checks. It is
not an automatic device selector or an internal installer.

## Surface firmware and USB boot

The development boot chain is unsigned and requires Secure Boot to be off.
Record the original setting and keep Windows recovery keys before changing
firmware settings. Leave TPM configuration and enrolled keys intact.
Microsoft documents the settings in
[Surface UEFI](https://support.microsoft.com/en-us/surface/drivers-firmware/how-to-use-surface-uefi).

To enter UEFI, shut down, wait about ten seconds, then hold Volume Up while
pressing and releasing Power. Keep Volume Up held until UEFI appears.
For USB startup, insert the USB while powered off, hold Volume Down, then
press and release Power. Follow Microsoft's
[USB startup instructions](https://support.microsoft.com/en-us/surface/drivers-firmware/boot-surface-from-a-usb-device).
Firmware policy or passwords must be handled by the device administrator.
These instructions do not establish that this build has passed Surface tests.

Keep a separate Windows recovery USB if you need a return path. Microsoft's
[Surface recovery guide](https://support.microsoft.com/en-us/surface/drivers-firmware/creating-and-using-a-usb-recovery-drive-for-surface)
explains model-specific recovery media. No Microsoft recovery image is
bundled or downloaded by this project.

## Display resolution

The new Settings → **Display resolution** flow lists modes reported by
connected DRM outputs, including native/preferred mode. It changes the OS
output; Moonlight streaming resolution is a separate profile.

Save work before Apply: applications close and the compositor/HOME restart.
The new mode has a 45-second trial. Choose **Keep this resolution** to save it
for later boots, **Restore previous resolution** to undo it, or wait for the
timer to restore the previous mode automatically. Apply/Keep and timed rollback
have passed installed VM tests; physical display validation remains pending.

Automatic rotation is a separate switch in this panel. It uses the internal
accelerometer and rotates the internal output without restarting applications.
Rotation pauses during a resolution trial. Missing sensor data is reported;
it does not establish a working physical sensor.

## Accounts and controllers

On first boot, create and confirm the root administrator password. Passwords
must contain 10–128 UTF-8 bytes. Settings → **Profiles** lets root add users
or lock HOME and switch accounts. Other users launch applications under their
own Unix identities with private home, browser and save directories. Cold
boots require a new login. The root profile has full administrative privileges.

USB and Bluetooth controller support includes the enabled Linux HID/gamepad
drivers and SDL's bundled mappings. Pair Bluetooth devices in **Controllers**;
use the mapping wizard for controllers whose layout is not recognized. Actual
compatibility depends on the device, firmware and connection mode; individual
physical models need testing. Bluetooth pairing on Surface hardware remains
under validation. Scan can start the Bluetooth service and
clear a Bluetooth software radio block; hardware blocks and driver errors are
reported separately. Driver coverage is not a promise of universal support.

## Find and search games

Store local games in `/data/roms` or connect a supported USB/removable-SD game
drive. HOME discovers games automatically and refreshes its library. External
game volumes are mounted read only; scanning does not move or change ROMs.

In **Games**, open **Search games** and enter part of a title. Confirm to see
matches. Back closes the title field and shows **Find games** to refresh
immediately. Missing cover downloads require the separate cover-art action;
ordinary discovery uses local or cached artwork.

Games stored on the live USB's DATA can be carried into an internal
installation. Games on another drive remain on that drive. Unsupported or
external catalog references are reported during installation preflight rather
than silently omitted.

## Install to internal storage

Installation replaces **every partition and file on the selected disk**,
including Windows, recovery partitions and existing DATA. There is no dual-boot
or mode that preserves DATA already on the target. Supported data from the
running live USB is copied into the new installation after cloning. Back up
first and use only an image whose matching live/install tests have completed.
VM installation and data migration have passed. Physical Surface installation
remains under validation.

1. In the live HOME, choose **Install 403 OS**, or open Settings → **Install to internal storage**.
   Review the intended internal whole disk's model, stable ID and capacity.
   The installer requires at least 60,000,000,000 bytes and 512-byte sectors.
   USB/removable, boot/root, mounted and active-swap targets are excluded.
2. Review the discovered embedded source filename/SHA-256, selected target,
   destructive warning and proposed DATA capacity.
   The source must be on storage separate from the target. Close running apps
   so saves and user data remain unchanged during copying.
3. Review the disk and erase warning, then choose **Install 403 OS — erase everything on this disk**.
   Cancel is selected by default. No keyboard or serial-number typing is required;
   the backend still verifies the reviewed hardware identity and source checksum.
4. Keep power connected through source verification, copying, complete
   readback, GPT verification, DATA expansion and user-data migration. Local
   games, BIOS files, saves, settings and profiles are copied with their
   ownership and permissions, then checked by file readback. Existing profile
   password hashes are retained, so the same passwords unlock the installed
   system. Wait for the final verified completion result; progress alone is
   not completion.
5. Choose **Power off**. After shutdown, remove the USB and start the device
   from internal storage. There is no automatic reboot.

The source and installed clone have fixed root/DATA PARTUUIDs. Leaving the
USB attached can select the wrong copy; remove it after shutdown before the
normal installed boot.

The installer expands only freshly cloned, unmounted DATA into unused space
on that target. It preserves partition starts/identities and EFI/root payloads,
checks DATA before resizing, then verifies the final ext4 capacity. This is
not a general resize tool for populated volumes. The expanded disk no longer
has the base image's whole-file checksum; retain installation/growth receipts.
`installer/install-vm.sh` is restricted to disposable QEMU targets and must
not be used as a physical-disk installer.

## Back up DATA

There is no backup/restore wizard. Close apps cleanly, then make a consistent
backup from an offline read-only environment. Keep a verified copy on a
separate physical device; backups on the disk being reinstalled are erased.

Preserve hidden files, numeric ownership, permissions, ACLs, extended
attributes and symlinks. Verify checksums/listings and test extracting a known
save into a temporary location. Relevant paths include:

| DATA path | Contents |
| --- | --- |
| `/data/saves` | SRAM and save states |
| `/data/config`, `/data/home` | Controller/display/emulator settings and HOME state |
| `/data/library`, `/data/roms`, `/data/bios` | Catalog, artwork and supplied game/firmware files |
| `/data/apps`, `/data/windows` | Imported clients, runtimes and separate Wine/Proton prefixes |
| `/data/bluetooth`, `/data/moonlight`, `/data/tailscale` | Pairing keys and persistent network identity |
| `/data/browser`, `/data/share`, `/data/logs` | Browser data, application state and diagnostics |
| `/data/profiles` | Password hashes, private per-user homes and HOME state |

Backups may contain credentials, cookies and pairing keys. Store them with
restricted access, preferably encrypted; exclude them from Git and public
support uploads. Do not run two devices with a cloned Tailscale identity.
External game drives need their own backups.

Restore only to a reviewed DATA filesystem with affected services stopped.
Retain root-owned service directories and gaming UID/GID `1000:1000` for user
files. Keep the original backup until restored saves/profiles have been tested;
states and Windows prefixes can depend on software versions.

## Recover before reinstalling

A normal 403 OS boot mounts DATA writable and updates its boot counter. It
is not a read-only recovery mode. Use a separate Linux recovery USB with
automatic mounting disabled and identities different from the installed clone.

Identify the source disk and DATA partition by stable hardware ID, capacity
and GPT. Confirm that neither it nor its descendants is mounted or used as
swap. Do not identify it only by a device letter or partition label.
Mount the reviewed ext4 DATA filesystem with `ro,noload,nosuid,nodev,noexec`.
The `noload` option matters because an ext4 read-only mount can otherwise replay
its journal; see the [kernel documentation](https://docs.kernel.org/admin-guide/ext4.html#options).
Copy files to a separately reviewed backup destination.

If safe mounting fails, preserve a read-only device image before repair.
Journal replay and filesystem repair can alter the source. For a failing disk
or irreplaceable files, preserve the original rather than retrying installation.

## Interrupted installation and updates

An interruption can leave a partially overwritten disk or incomplete DATA
growth. Preserve diagnostics and recover needed files before another write;
there is no automatic rollback of old contents.

A reinstall repeats the same whole-disk review and new exact destructive
confirmation. It replaces the target's DATA too; only supported data from the
current live USB is migrated. Do not resume a partial clone with raw
disk commands or apply a USB helper to an internal disk. If the installer
refuses the layout, retain its diagnostics for recovery review.

There are no automatic OS updates, A/B slots or unattended rollback. Keep the
previous image and a verified backup before changing versions. Returning to
Windows is a separate recovery operation using the correct model's media.
Physical installation, recovery and restored-data behavior remain unverified
until tested on the actual hardware.
