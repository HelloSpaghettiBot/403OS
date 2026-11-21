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

`installer/flash-usb.py` is the current whole-device delivery tool. It replaces
all old partitions, including H, with the compiled image's EFI, root and
4 GiB DATA layout. It also clears the obsolete physical-end GPT. It does not
securely zero every remaining byte, enlarge DATA, or preserve old H files.
Unused USB capacity remains outside the image's partition layout.

On November 20 the authorized USB flash and complete image readback passed
for the exact image recorded in [STATUS.md](STATUS.md); GPT verification and
old end-GPT clearing passed too. This confirms delivery bytes, not Surface
boot. The original H contents were replaced. Keep the private write receipt
with the image/checksum and retain separate backups of any needed files.

Exact USB model, serial, capacity, stable USB by-id path and the final tested
image SHA-256 are recorded in ignored
`artifacts/usb-execution/full-device-plan.json`. The public
`installer/USB_EXECUTION_PLAN.json` is an intentionally unusable example.
The old H-preserving planner/writer is superseded; its execution entry point
is disabled. Do not use its synthetic fixture identity for a real device.

The source image stays on the separate mounted FILES drive. First run the
read-only preview, which opens no raw USB descriptor:

```sh
python3 installer/flash-usb.py --plan artifacts/usb-execution/full-device-plan.json
```

Before physical execution, record the final image SHA in the private plan,
complete the matching live UEFI VM checks, mark the reviewed private plan
`READY_FOR_EXECUTION`, and retain its SHA-256. Root alone executes the helper
with `--execute`, `--expected-plan-sha256`, `--boot-result` pointing to that
completed live result, and `--confirm` containing `ERASE ENTIRE USB ` followed
by the exact reviewed serial. These values must come from the reviewed
private plan, not a device letter or a copied generic disk-writing command.

The helper verifies the complete source hash before unmounting anything,
then unmounts only the target's filesystems through UDisks. It requires USB
kernel ancestry, an exact model/serial/capacity match, no protected host mounts
or active swap, and an exclusive block descriptor with matching geometry.
UDisks/polkit permission can be required; `--allow-auth-dialog` permits its
normal authentication dialog. Permission availability has been inspected
read only; no writable-open authorization was assumed or bypassed.

Old partition metadata, the private plan and completion/error receipts are
saved on FILES under `artifacts/usb-execution/full-flash-*/` before copying.
A successful result requires complete image readback SHA-256, valid image GPT
CRCs and verified clearing of the old end GPT. Metadata backups cannot restore
the old H files. An interrupted flash can leave the entire target unusable;
there is no automatic rollback of its old contents. Keep reliable power
connected and require the final `PASS` receipt.

Boot and test the USB before considering internal installation. Confirm HOME,
input, clean application return, display, sound, networking and required game
controllers on the actual Surface using
[SURFACE_HARDWARE_TESTS.md](SURFACE_HARDWARE_TESTS.md). A live boot is not an
installation confirmation.

The internal installer also needs a separate, validated source image file;
the raw-flashed USB does not contain a second copy for this purpose. For a
future separately authorized internal installation, retain these files on
separately reviewed source media:

```text
SurfaceGoGamingOS/surface-go.img
SurfaceGoGamingOS/surface-go.img.sha256
```

The sidecar contains the matching SHA-256, optionally followed by
`surface-go.img`, as in the build's `SHA256SUMS` line. The installer discovers
this folder on mounted media or mounts a separate NTFS USB source read only.
Do not expect the old H staging folder to survive the authorized whole-USB
flash. No additional source media has been modified automatically.

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

The currently flashed live-tested image has a known VM installation failure
in final DATA-growth validation. A corrected image and new successful
installation/cold-boot tests are required before this workflow is ready.
Do not treat a successful USB flash or live boot as installation acceptance.

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
2. Review the staged source filename and exact SHA-256, the all-partitions
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
