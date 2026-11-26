# Development USB tooling

For general use, write `surface-go.img` to a deliberately selected whole USB
with an image-writing tool and verify readback. Review model, capacity and
device identity: this erases every partition and file on that USB. Internal
installation is a separate workflow with its own target selection/confirmation.
See [installation and recovery](../docs/INSTALL_RECOVERY.md).

`flash-usb.py` is the repository's guarded development helper. It consumes an
explicit local JSON record with stable USB identity, exact image SHA-256 and
reviewed readiness. The checked-in `USB_EXECUTION_PLAN.json` is a synthetic,
unusable example. Keep actual hardware identities and execution receipts out
of public source.

Preview is read only and opens no raw block descriptor. Execution requires an
explicit execute flag, matching plan digest, completed image-specific VM
checks and an exact serial-specific erase phrase. It verifies USB ancestry,
model/serial/capacity, protected mounts and swap before requesting exclusive
UDisks access. It retains metadata backups, copies image bytes, clears the
obsolete physical-end GPT, and verifies complete readback/GPT checksums.
Host storage-location and permission guards must also be satisfied; the tool
is intended for the configured development environment, not a portable GUI.

Readback is enabled by default. The explicit `--skip-readback` option flushes
the write but omits target readback and records `WRITTEN_UNVERIFIED`, with
`full_image_readback=SKIPPED_BY_EXPLICIT_REQUEST`. This result is not a verified
flash; source checks, target identity and exclusive-access guards still apply.

After the image has passed full readback, the writer expands the USB's final
DATA partition and ext4 filesystem to use the remaining capacity. This leaves
room on the same USB for the installer payload, game library, and verified
system-update backups. A failed expansion is recorded as a failed write and
must be repaired before relying on the USB. This is not secure erasure.
Metadata backups cannot restore overwritten files. An interrupted write may
require a fresh complete verified flash.

The legacy `prepare-usb.py`/`write-usb.py` preservation transaction remains
only for synthetic fixture coverage; legacy execution is disabled.

```sh
python3 -m unittest discover -s tests -p 'test_usb_*.py' -v
```

Tests use disposable files/mocks and never write a host block device.
The helper needs dbus-python/UDisks2; authorization may be required. See
[OpenDevice](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Block.html)
and [Filesystem.Unmount](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Filesystem.html).
API availability does not imply that writable access is already authorized.
