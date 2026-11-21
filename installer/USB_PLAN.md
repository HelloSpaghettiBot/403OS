# Whole-device USB delivery

The latest owner instruction authorizes erasing the entire specifically
reviewed external H USB. The former H-preserving boot-area plan is superseded.
No Surface internal-disk erase is authorized.

Use `flash-usb.py` and a private, ignored
`artifacts/usb-execution/full-device-plan.json`. The checked-in
`USB_EXECUTION_PLAN.json` contains synthetic example values only. It is not an
execution plan for any real device. Never publish real hardware identities or
private VM/device evidence.

The default preview is read only and opens no raw block descriptor. Execution
requires `--execute`, the exact reviewed private-plan SHA, matching final-image
live UEFI boot/input/clean-power evidence, and the exact phrase
`ERASE ENTIRE USB <reviewed serial>`. Only the project root agent performs the
owner-authorized physical write after final validation.

The helper hashes the source before unmounting target filesystems, rechecks
USB model/serial/capacity and ancestry, refuses host system storage or swap,
requests an exclusive UDisks descriptor, backs up old partition metadata on
FILES, copies every image byte, clears obsolete physical-end GPT metadata, and
verifies full image readback and image GPT CRCs. It leaves extra USB space
unused; DATA remains 4 GiB. Normal flashing is not full-media secure erasure.
Old files are not preserved and an interrupted write has no content rollback.

`prepare-usb.py` and `write-usb.py` retain the earlier synthetic preservation
fixture tests. Their checked-in identities are artificial and the legacy
writer's `--execute` entry point is disabled. Historical private plans remain
only under ignored `artifacts/usb-execution/legacy-private/`.

See [installation and recovery](../docs/INSTALL_RECOVERY.md) for live-first
validation, backups, firmware settings and the separate internal-installation
confirmation requirements.

Disposable-file verification:

```sh
python3 -m unittest discover -s tests -p 'test_usb_*.py' -v
```

These tests never open a host block device or call UDisks to unmount/write.
Physical USB writing and Surface hardware results require separate evidence.

The host execution path requires Python 3 with dbus-python and UDisks2. The
[official UDisks Block API](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Block.html)
documents `OpenDevice("rw", options)` with exclusive/synchronous open flags;
[Filesystem.Unmount](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Filesystem.html)
handles only the reviewed target filesystems. An available API does not prove
that polkit has granted writable access. No writable method was invoked during
the permission audit.
