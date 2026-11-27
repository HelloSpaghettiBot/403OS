# Update 403 OS

## Check for a new version

Open **Settings → System updates → Check for updates**. 403 OS also checks
GitHub after startup and once a day while running. If the network is offline,
it retries later. Checks use the public release metadata and send no game
library, account credentials or device identifiers.

The page shows the installed version, latest stable release and last check.
**Open GitHub release details** opens the official release page. A release is
offered as available only when it includes the Surface Go image and a SHA-256
digest. Source commits and source archives are not OS updates.

No downloadable stable OS release has been published yet. Until one is
published, the checker says so. Checking never downloads or installs software
automatically. Installation still uses the USB procedure below.

## Update from USB

Use a newer 403 OS installer USB to update an existing internal installation
while keeping its games, saves, profiles, passwords and settings. Updates run
from the USB so the installed system can stay stopped during replacement.
Until a binary release is available, [build the disk image](BUILD.md) and write
`output/images/surface-go.img` to USB. Experimental ISO media provides temporary
live storage and fresh installation only; it cannot retain an update recovery
copy after shutdown.

## Before you start

Keep a separate backup of important saves. Connect the device to power and
prepare a current 403 OS installer USB. Boot that USB and sign in as the root
administrator. See [USB startup](INSTALL_RECOVERY.md#surface-firmware-and-usb-boot)
if the installed system starts instead.

The USB needs free space for complete copies of the installed EFI and root
partitions, plus a small working reserve. The updater checks this before it
changes the installed system. The system backup stays on the USB under
`/data/updates/`, in a private directory readable only by root. Copy a backup
elsewhere before rewriting that USB if you still need it for recovery.

## Update the installed system

1. Open **Settings → Install or update**.
2. The page opens in **Update 403 OS** mode. Use **Install 403 OS instead**
   only when you intend to erase a disk for a fresh installation.
3. Select the internal disk containing your existing 403 OS installation.
   Review its identity and capacity. The running USB and disks in use are
   excluded.
4. Review the update and backup information. **Cancel** is selected initially.
   Choose **Update 403 OS** when the correct internal disk is shown.
5. Keep power and the USB connected until the verified completion screen
   appears. The updater checks its source and saves a verified copy of the
   existing system before replacing EFI and the root filesystem. It then
   reads back the replacement to check its contents.
6. Choose **Finish and power off**. After shutdown, remove the USB and start the device.
   Sign in with your existing password; your internal game library and
   settings remain available.

The completion screen initially selects **Finish and power off**. Choosing
**Continue using USB** keeps the current USB session running; it does not
switch HOME to the installed system. Leave the USB connected until shutdown.

The update leaves the installed DATA partition and partition layout in place.
It does not copy additional games from the USB into the existing installation.
Updating uses the image already on the USB; it does not download an update
over the network.

## Internal drive detected but unavailable

Inserting the USB into an already-running internal installation does not stop
that installation. The disk list identifies the running drive and explains why
it cannot be overwritten. Shut down, leave the USB connected, then boot from
USB and open **Settings → Install or update** again. Choose **Update 403 OS**
to keep games, profiles and settings; a fresh install erases them.

An unmounted installation with pending normal ext4 journal replay can receive
a system-only update. DATA stays byte-for-byte unchanged during the update;
the kernel handles journal replay when the installed system next starts. This
does not bypass filesystem error flags or permit resizing unclean storage.

## If an update cannot finish

Keep the USB connected and continue using its live system. Do not choose a
fresh installation to retry an update: installation erases the selected disk.
Retain the update backup until the updated system has booted and important
saves have been checked.

If system replacement has started and fails, the updater reports the backup
location. Recovery is an explicit administrator action; there is no automatic
rollback. Boot the USB again if needed, then restore the backup from its root
administrator terminal. Replace the example backup path, internal disk and
stable disk ID below with the values from the update review and recovery
message:

```sh
surface-update-device --restore \
  --backup /data/updates/update-BACKUP_ID \
  --target /dev/INTERNAL_DISK \
  --expected-target-id "STABLE_ID" \
  --confirm "RESTORE STABLE_ID"
```

Recovery checks the backup, the disk identity and the partition layout before
restoring the previous EFI and root contents. The target must be unmounted.
It does not restore or replace your DATA partition. Wait for verified recovery
completion, power off and remove the USB before starting the internal system.

Updates replace the system partitions in place. Losing power during replacement
may require restoring the backup. See [current validation](STATUS.md) for
executed VM tests and remaining physical-device checks.
