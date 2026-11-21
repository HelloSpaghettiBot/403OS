# Installation, backup and recovery

This is a development image. Read [STATUS.md](STATUS.md) and
[VM_TEST_REPORT.md](VM_TEST_REPORT.md) for the exact image and checks that have
passed. Physical Surface Go boot, installation and recovery are **UNTESTED**.
Source code and fixture tests do not establish hardware support.

The latest owner instruction authorizes erasing ALL partitions and files on
the reviewed external H USB and flashing the validated live image. This
supersedes the earlier instruction to preserve H. **No Surface internal-disk
erase has been authorized.** These instructions document that USB scope and
the separate review required for internal installation.

## Keep the recovery materials separately

Before an internal installation, save the owner's Windows files and recovery
keys, and prepare a way to restore Windows if needed. Microsoft's
[Surface recovery instructions](https://support.microsoft.com/en-us/surface/drivers-firmware/creating-and-using-a-usb-recovery-drive-for-surface)
describe recovery media for the specific Surface model. Use a separate blank
USB for that workflow, and retain it separately from the USB authorized for
the gaming image. No Microsoft recovery image is bundled or downloaded
by this project.

Retain the validated `output/images/surface-go.img`, its matching
`output/images/SHA256SUMS`, the source revision, and the corresponding
`artifacts/vm-tests/` results. A new build can replace these output filenames;
keep a dated copy of each image you intend to recover to. A checksum verifies
bytes against the retained value; it does not authenticate an unsigned image's
publisher.

## Prepare and live-test the authorized USB

The build produces two artifacts:

| Artifact under `output/images/` | Use |
| --- | --- |
| `surface-go.img` | Flash this self-contained bootable USB image |
| `surface-go-base.img` | Pristine installation payload; empty DATA, no nested image |
| `SHA256SUMS`, `surface-go-base.img.sha256` | Separate outer/base checksum records |
| `installer-media.json` | Both identities and embedded-payload readback receipt |

The outer USB carries the sparse base as
`/data/SurfaceGoGamingOS/surface-go.img` and its `.sha256` sidecar. These files
are root owned and read only; the mounted DATA also holds normal live settings.
The installation panel discovers that source automatically through the real
source-finder service. **No second source USB or manual staging is required.**
Installation copies the pristine embedded file, not the outer USB image, so
the installed DATA starts empty and does not contain another installer image.

The new self-contained images compiled and the embedded file passed build-time
readback/ownership checks; VM runtime validation is pending. The
preceding pristine image passed installation, DATA growth and three detached
boots, but those results do not validate newly embedded source discovery.
Before delivery require exact new outer/base checksums, live full-payload
verification and successful VM installation from the embedded file.

`installer/flash-usb.py` replaces the authorized USB's old partitions/files
with the outer image's EFI, root and 4 GiB DATA and clears the obsolete end GPT.
It does not securely zero remaining media or expand USB DATA. Extra USB
capacity remains outside the image layout. Exact target identity, image SHA
and execution receipts stay under ignored `artifacts/usb-execution/`; the
checked-in execution plan is an unusable example.

The source outer image stays on the separate mounted FILES drive. The default
preview opens no raw USB descriptor:

```sh
python3 installer/flash-usb.py --plan artifacts/usb-execution/full-device-plan.json
```

Physical execution requires the reviewed private plan SHA, matching completed
live evidence and the exact serial-specific erase phrase. The helper verifies
source SHA before target unmounts, rechecks USB identity and active storage,
opens exclusively through UDisks, retains partition-metadata backups on FILES,
and verifies full image readback/GPT and end-GPT clearing. Normal polkit
permission can be needed. Require the final PASS receipt; metadata backups
cannot restore old files after copying begins. An interruption may require a
fresh verified flash.

An earlier image's authorized flash/readback and power-off passed, replacing
H's old files; that image lacks the self-contained installer payload. It must
be replaced by the new validated outer image. This is not a Surface boot claim.

Boot and test the new USB on the actual Surface before internal installation.
Check HOME, input, app return, display, sound, networking and controllers using
[SURFACE_HARDWARE_TESTS.md](SURFACE_HARDWARE_TESTS.md). A live boot is not an
erase confirmation.

## Change the OS display resolution

The new Settings → **Display resolution** flow is being integrated and is
not yet validated in the flashed USB. It lists modes detected on connected
DRM outputs, including **native / preferred**; it does not invent unsupported
resolutions. This changes the OS output, separately from Moonlight's streaming
profile.

Close apps and save work before Apply: the compositor and HOME restart to
try the selected mode. The new mode has a 45-second confirmation window.
Choose **Keep this resolution** only when display, text and input are usable;
Keep saves it for later boots. Choose **Restore previous resolution**, or wait
for the timer to restore the previous mode automatically. This recovery flow
still requires VM and physical display tests before being treated as verified.

## Surface firmware and USB boot

This development boot chain is unsigned and requires Secure Boot to be off.
Record the existing firmware setting and retain any Windows recovery keys
before changing it. Keep TPM configuration and enrolled boot keys intact.
Microsoft documents the Secure Boot setting in
[Surface UEFI](https://support.microsoft.com/en-us/surface/drivers-firmware/how-to-use-surface-uefi).

To enter UEFI, shut down, wait about ten seconds, then hold Volume Up while
pressing and releasing Power. Keep Volume Up held until UEFI appears.
Use its Security and Boot configuration controls as applicable to the device.
These are Microsoft's firmware instructions; this project's physical Surface
results remain unverified.

For USB startup, with the Surface off and the prepared USB inserted, hold
Volume Down while pressing and releasing Power, then follow Microsoft's
[USB startup sequence](https://support.microsoft.com/en-us/surface/drivers-firmware/boot-surface-from-a-usb-device).
Use the firmware's USB boot option if needed. An existing enterprise firmware
policy or password requires the owner's administrator; this project does not
bypass it.

## Review an internal installation

The self-contained image's embedded-source installation must pass the new VM
checks before this workflow is ready. Earlier pristine-image installation and
cold boots passed; physical installation remains untested. Do not treat a
source test, USB flash or live boot as physical installation acceptance.

After the owner separately authorizes erasing the exact internal disk, use
HOME's Settings → **Install to internal storage**. This calls the image's
`/usr/sbin/surface-install-device`; `installer/install-vm.sh` is restricted to
the disposable QEMU fixture and is not the physical installer.

1. Refresh storage and select the intended internal whole disk. Review its
   hardware model, stable serial/WWID, capacity and displayed device path.
   The current installer requires at least 60,000,000,000 bytes and 512-byte
   logical sectors. USB, removable, boot/root, mounted and active-swap targets
   are excluded. An excluded target is a reason to investigate, not to bypass
   the guard.
2. Review the discovered embedded source filename and exact SHA-256, the all-partitions
   erase warning and the proposed DATA capacity. The source must be separate
   from the target. Existing Windows, recovery partitions and existing OS DATA
   on the selected internal disk are all replaced; there is no dual-boot or
   preserve-DATA installation mode.
3. Only after reviewing that concrete disk, type exactly `ERASE ` followed by
   the displayed stable ID. The USB preparation phrase is not this internal
   installation confirmation. Cancel if any identity or checksum differs.
4. Keep reliable power connected through source verification, image copy,
   readback, GPT verification and DATA expansion. The service rechecks target
   identity and mount state before opening it writable. A progress message is
   not completion; require the final verified completion result.
5. Use the completion panel's **Power off** action. Wait for shutdown, remove
   the USB boot media, then power on to test internal storage. There is no
   automatic restart.

The current image uses fixed root and DATA PARTUUIDs. A clone and its USB
source have duplicate identities; leaving both attached can select the wrong
root or DATA filesystem. Removing the USB after shutdown is required for the
normal first internal boot and subsequent cold-boot checks.

During an internal installation, `/usr/sbin/surface-grow-data` expands only the
fresh, unmounted 4 GiB DATA partition into reviewed unused space on that same
internal disk. It preserves partition starts/identities and EFI/root payloads,
checks the original DATA contents before resizing, and verifies the resulting
ext4 capacity. Growth is part of the confirmed installation, not an automatic
boot-time action or a supported resize of an existing populated DATA volume.
The expanded disk will not have the source image's whole-file checksum;
retain the installer's copy/readback and final growth receipts instead.

## Back up private DATA

There is no built-in backup/restore wizard. Close games and apps cleanly so
their saves are flushed, then use an offline, read-only recovery environment
for a consistent backup. Keep at least one verified copy on a different
physical device. A backup inside the disk being reinstalled will be erased.

Back up the complete DATA filesystem, including hidden files, ownership,
numeric UID/GID, permissions, ACLs, extended attributes and symbolic links.
Verify the backup's checksum and file listing, and test extracting a known save
into a separate temporary location before erasing the original. In particular:

| DATA path | Contents to retain |
| --- | --- |
| `/data/saves` | Core SRAM and quick states, including `save.srm` and `quick.state` |
| `/data/config`, `/data/home` | Emulator/controller profiles, HOME history/favorites, audio/brightness settings and private Wi-Fi configuration |
| `/data/library`, `/data/roms`, `/data/bios` | Local catalog/artwork and owner-provided game/firmware files, where present |
| `/data/apps`, `/data/windows` | Imported clients/ROMs, setup receipts, separate Wine/Proton prefixes, Windows app registry and installed runtimes |
| `/data/bluetooth`, `/data/moonlight`, `/data/tailscale` | Pairing material, streaming keys and persistent network identity |
| `/data/browser`, `/data/share`, `/data/logs` | Browser state, other application data and private diagnostic evidence |

Treat the whole backup as private: it can contain cookies, passwords, pairing
keys and device credentials. Store it with owner-only access, preferably
encrypted. Do not put it in Git, a release image, public logs or a support
upload. Tailscale state must not be restored onto a second simultaneously
running device as a cloned identity. External game drives have their own
backup needs; copying DATA does not copy those drives.

Restore from a verified private backup only after identifying the destination
DATA filesystem and stopping the affected applications/services. Preserve
root-owned service parents and gaming UID/GID `1000:1000` for that user's files.
Keep an untouched backup while testing restored saves and profiles against the
retained image/core versions; save states and Windows prefixes can be version
dependent. There is no automatic migration or restore acceptance claim.

## Recover files before reinstalling

Ordinary Surface OS startup mounts DATA writable and increments its boot
counter. The current USB image has no dedicated read-only recovery entry, and
its fixed PARTUUIDs can conflict with an installed clone. Do not treat a normal
USB HOME session as a forensic/read-only recovery environment.

For recovery, use an independently prepared Linux USB with automatic mounting
disabled and identities that do not duplicate this image. Have the operator
identify the internal disk and its DATA partition from stable hardware ID,
size and GPT, and verify that neither it nor its descendants is mounted or
used as swap. Do not identify a recovery source solely by a device letter,
partition label or the shared DATA PARTUUID.

Mount that reviewed ext4 DATA partition read only with
`ro,noload,nosuid,nodev,noexec`. `noload` matters: an ext4 read-only mount alone
can replay its journal, as documented by the
[Linux kernel](https://docs.kernel.org/admin-guide/ext4.html#options).
Copy recovered files to a separately reviewed backup
destination, never onto another attached volume merely because it is
available. The project's library-volume helper excludes internal disks and is
not an internal-DATA recovery tool. No recovery mount or backup has been
performed automatically by these instructions.

If DATA cannot be mounted safely, preserve a read-only image of the original
device on separate storage before considering repair. Journal replay or a
repair pass can change the original and is a separate reviewed operation.
For a failing device or irreplaceable files, preserve the original for recovery
instead of repeatedly attempting installation.

## Interrupted installation and reinstall

An interruption after copying starts can leave a partially overwritten target
or incomplete DATA growth. Do not boot it or assume that old Windows/DATA is
recoverable. Preserve progress/error records and recover any needed original
files before attempting another write. The physical internal installer has
no automatic rollback to the old disk contents.

Once backups and the validated source are available, a reinstall follows the
same whole-internal-disk review and fresh exact `ERASE <displayed stable ID>`
confirmation above. It replaces DATA too. Do not resume a partial clone with
raw disk commands or apply either USB helper to an internal disk. The
external-USB erase authorization does not extend to any internal disk. If the installer
refuses a damaged layout, retain its diagnostics for a reviewed recovery plan.

There are no automated OS updates, A/B slots or unattended rollback. Keep the
previous validated image and private backup before changing versions; a full
reinstall is destructive and needs its own target review and confirmation.
Returning to Windows is a separate owner-approved Microsoft recovery workflow
using the correct model's media and a separate USB. Hardware installation,
recovery and restored-data behavior remain **UNTESTED** until recorded on the
actual Surface.
