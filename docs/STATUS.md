# Status — November 21, 2026

403 OS is a development preview. The current build adds reliable live-USB
boot selection and a separate update workflow that preserves an existing
installation’s games, saves, profiles and settings.

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `82cd3cdcfcb8024bb0de8d9a40704d163b4a90f8d6a1ba95e90941e9a5479d76` |
| Pristine internal-install image | `1381785b4099e4c2a45935178c4ebf4186be4f29465a6cab1b1e79d0d94d5881` |

Both images are 6,510,628,864 logical bytes. Packaging verified the complete
embedded installation payload and its root-owned read-only metadata. Current
VM acceptance uses a local test-media variant with the same compiled root
filesystem and pristine installation payload. The exact public outer image
has not been booted separately.

## Installation and updates

Each live USB receives distinct root and DATA partition identifiers. Early
boot mounts DATA from the same physical disk as the selected root filesystem,
rather than selecting another disk with a shared identifier. UEFI VM run
`20261121-173759-live` selected the USB’s root and DATA correctly with an older
internal 403 OS installation attached.

**Settings → Install or update 403 OS** offers separate operations:

- Installation verifies the embedded pristine image, reviews the selected disk
  with Cancel selected by default, erases that target, verifies the full copy
  and expands fresh DATA. Supported local live-USB games, saves, settings and
  profiles are then migrated with file readback, ownership and permissions.
- Update boots from the newer USB and replaces only an eligible, unmounted
  installation’s EFI and root partitions. It verifies the source, saves and
  reads back the old system partitions before writing, then verifies the new
  contents. The installed partition layout and DATA are retained. Existing
  passwords remain valid. Update has its own Cancel-default review.

The current VM exercised the production update review, verified system backup,
copy/readback and GUI power-off against a disposable older installation.
Independent direct virtual-disk reads compared the complete 64 GiB target:
DATA and both GPT areas were byte-for-byte unchanged, and EFI/root matched
the new pristine payload exactly.

Detached UEFI run `20261121-175027-test` then booted the updated SATA disk
without its installation USB. Existing-password login, the retained 28-game
library and covers, all emulator/app checks, rotation, network, non-silent
audio and clean power-off passed. This establishes one current detached cold
boot; additional boots and recovery remain separate acceptance work.
Recovery from a retained backup has source-fixture coverage; interrupted-update
and real power-loss recovery remain untested. See [Updates](UPDATES.md).

## Included capabilities

- Native C++/SDL3 HOME, automatic game discovery, cached cover art, title search,
  controller navigation and custom controller mapping.
- Eleven emulator engines, Cog/WPE WebKit, Moonlight, Tailscale and Wine64/UMU.
- Password-protected profiles. The first account is root; additional accounts
  use separate Unix identities and private application storage.
- Display mode trials with rollback, persisted settings, and native Weston
  rotation that preserves running applications.
- Surface Go touch/sensor kernel paths and broad upstream USB/Bluetooth gamepad
  drivers, including BlueZ classic HID and LE HOG support. Configuration alone
  does not establish physical-device compatibility.

The recorded suite passed **371 Python tests** (344 main and 27 application
fixtures) and **five native unit test executables** in
`artifacts/tests/system-update-final.log`. The final DATA-mount correction also
passed 14 focused tests. Two read-only virtual-disk fixture tests also passed.
Twenty-two updater backend tests cover guarded writes,
source changes, backup validation and explicit restoration using fixtures.

## Bluetooth and physical-device limits

The USB Bluetooth transport loads as `btusb` after the root filesystem and
firmware are available. Build checks cover module dependencies, USB aliases,
firmware and startup. Scan waits for an actual powered BlueZ controller and
provides private transport/firmware diagnostics. Hardware blocks and pairing
policy remain intact. See [Bluetooth](BLUETOOTH.md).

The current VM passed module/service startup, private diagnostic permissions,
existing-profile login, library/covers, all eleven emulator probes, NES
input/SRAM, browser/global HOME return, rotation, networking, non-silent stereo
audio and clean power-off. This does not
establish Bluetooth adapter detection, pairing or reconnect on the Surface.

Earlier physical logs showed Intel HD 615 initialization and ELAN touch-device
registration. Those are detection evidence only. The reported touchscreen and
Bluetooth failures remain unresolved until physical retesting. Accelerometer
axes, four orientations, touch coordinates, individual controllers, radios,
audio, battery and suspend also require hardware acceptance.

## Earlier evidence and remaining acceptance

The preceding revision passed GUI installation/migration
(`20261121-110730-live`) and a detached boot with the existing password and
migrated library (`20261121-112520-test`). Earlier runs covered secondary-user
identity separation and display Apply/Keep, rollback and cold-boot persistence.
Those results apply to their recorded revisions.

Overall VM status remains **PARTIAL**. Enrolled streaming is **BLOCKED**;
physical hardware, interrupted-boot recovery, Proton setup and online-game
login/gameplay have not passed acceptance. No production-release or physical
Surface installation pass is claimed. Public source and base images contain
no game files, credentials or private device records.

See [installation](INSTALL_RECOVERY.md), [roadmap](ROADMAP.md) and
[test results](VM_TEST_REPORT.md).
