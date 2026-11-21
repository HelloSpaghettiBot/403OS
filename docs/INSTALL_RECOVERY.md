# Installation, backup and recovery

403 OS is a development image. Read [status](STATUS.md) and the
[VM report](VM_TEST_REPORT.md) before using a build. Current self-contained
installer/display changes still need complete acceptance; physical Surface
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
Internal installation copies the pristine base, so installed DATA does not
contain another installer image.

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
timer to restore the previous mode automatically. This flow is being integrated;
actual Apply/Keep/rollback and physical display validation remain pending.

## Install to internal storage

Installation replaces **every partition and file on the selected disk**,
including Windows, recovery partitions and existing DATA. There is no dual-boot
or preserve-DATA mode. Back up first and use only an image whose matching
live/install tests have completed. The current revised image is still under
validation.

1. In HOME, open Settings → **Install to internal storage** and refresh devices.
   Review the intended internal whole disk's model, stable ID and capacity.
   The installer requires at least 60,000,000,000 bytes and 512-byte sectors.
   USB/removable, boot/root, mounted and active-swap targets are excluded.
2. Review the discovered embedded source filename/SHA-256, selected target,
   destructive warning and proposed DATA capacity. The source must be on
   storage separate from the target.
3. Type exactly `ERASE ` followed by the stable ID displayed for that disk.
   Cancel if the identity, source checksum or erase scope is wrong.
4. Keep power connected through source verification, copying, complete
   readback, GPT verification and DATA expansion. Wait for the final verified
   completion result; progress alone is not completion.
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
confirmation. It replaces DATA too. Do not resume a partial clone with raw
disk commands or apply a USB helper to an internal disk. If the installer
refuses the layout, retain its diagnostics for recovery review.

There are no automatic OS updates, A/B slots or unattended rollback. Keep the
previous image and a verified backup before changing versions. Returning to
Windows is a separate recovery operation using the correct model's media.
Physical installation, recovery and restored-data behavior remain unverified
until tested on the actual hardware.
