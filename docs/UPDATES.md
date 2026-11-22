# Update 403 OS from USB

Use a newer 403 OS installer USB to update an existing internal installation
while keeping its games, saves, profiles, passwords and settings. Updates run
from the USB so the installed system can stay stopped during replacement.

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
2. Choose **Update existing 403 OS (keep games and settings)**.
3. Select the internal disk containing your existing 403 OS installation.
   Review its identity and capacity. The running USB and disks in use are
   excluded.
4. Review the update and backup information. **Cancel** is selected initially.
   Choose **Update 403 OS** when the correct internal disk is shown.
5. Keep power and the USB connected until the verified completion screen
   appears. The updater checks its source and saves a verified copy of the
   existing system before replacing EFI and the root filesystem. It then
   reads back the replacement to check its contents.
6. Choose **Power off**. After shutdown, remove the USB and start the device.
   Sign in with your existing password; your internal game library and
   settings remain available.

The update leaves the installed DATA partition and partition layout in place.
It does not copy additional games from the USB into the existing installation.
Updating uses the image already on the USB; it does not download an update
over the network.

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
