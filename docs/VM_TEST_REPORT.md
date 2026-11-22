# VM test report

## Current compiled artifacts

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `dc8b69a3682aa9794c273a8f9c5cb262ac2941f0035fb0dcd4457dc37f86f77f` |
| Pristine installation payload | `e1e032df9066e075166fe3b9e2c4395a79c0c1f5fe39cdcf84afdccc136aa7b6` |

Both are 6,510,628,864 logical bytes. Packaging passed complete embedded
payload readback, root ownership, mode 0444 and absence of a recursive payload.
Current acceptance uses a local test-media variant with the same compiled root
filesystem and pristine payload. The exact public outer image has not been
booted separately. Earlier runs retain their original hashes and apply only
to their recorded revisions.

## Bluetooth correction acceptance

The actual pre-patch BlueZ 5.86 client returned empty output for list, show and
power against a valid private D-Bus Adapter1 fixture. All three integration
tests failed before the backports and passed afterward. Tests cover adapter
listing, power off/on with readback, discovery, pairing, trust, connection,
disconnection and removal. They access no host radio.

The kernel now enables AF_ALG hash/cipher APIs plus AES/ECB/CMAC. UEFI run
`20261121-191015-live` passed actual shell output and both crypto known-answer
checks, transport/service startup, private diagnostics, existing-profile login,
library/covers, all eleven emulator runtimes, input/SRAM, browser/global return,
live rotation, network, non-silent audio and GUI power-off. It used OVMF/KVM,
4 GiB RAM, four CPUs, 1800×1200 and an older SATA installation attached. Root
and DATA selected the USB correctly; no direct kernel boot was used.
Every implemented check passed; overall status is PARTIAL for hardware,
recovery and enrolled streaming. The exact public outer image was not booted
separately; this test variant shares its compiled root and pristine payload.
Physical Surface adapter power, pairing, reconnect and resume remain unverified.

## Previous revision: live boot and offline update

`20261121-173759-live` used QEMU 11.1.3, OVMF/UEFI and KVM, with 4 GiB RAM,
four virtual CPUs and an 1800×1200 framebuffer. It booted from emulated USB
storage with an older installed SATA disk attached; no direct kernel boot
was used. The operating system reported root and DATA on the selected USB,
with their distinct live-media partition identifiers.

The following guest checks passed:

- Existing-profile login through the production UI. A synthetic password was
  prepared only in a disposable qcow2 overlay; the input image and its original
  authentication record were unchanged.
- Root/DATA selection, HOME framebuffer/input, DHCP/DNS/HTTPS and automatic
  library/cover loading.
- All eleven emulator runtime probes, NES input/SRAM reload, Wine Windows
  command exit, Moonlight startup and Tailscale daemon/policy routing.
- Real WebKit launch/return and global HOME return. Native output rotation
  preserved browser and compositor process identity.
- `btusb` module/firmware/service startup and root-owned mode-0600 diagnostics.
  No physical Bluetooth adapter was supplied.
- Production GUI update with a separate Cancel-default review, verified old
  EFI/root backup, new system copy/readback and explicit GUI power-off.

Independent read-only virtual-disk reads compared the complete updated disk:

| Compared range | Bytes | Result |
| --- | ---: | --- |
| Disk prefix and primary GPT | 1,048,576 | Unchanged |
| Entire DATA region and backup GPT | 66,503,835,648 | Unchanged |
| EFI and root system partitions | 2,214,592,512 | Exact match to new payload |

The first host allocation-metadata comparison reported a false mismatch.
Complete direct byte comparison resolved it; the original failed host-check
receipt is retained in ignored local artifacts. Every implemented guest and
completed host check passed. Captured stereo PCM was non-silent (peak 2987,
41,438 signal frames).

## Previous revision: updated-system detached boot

`20261121-175027-test` booted the updated SATA disk through OVMF/UEFI with
no installation media and no direct kernel boot. Existing-password login and
the retained 28-game library with covers passed. HOME input/return, all eleven
emulator probes, NES input/SRAM, browser, Wine, Moonlight, Tailscale daemon
persistence, network, live rotation and GUI power-off passed. Stereo audio
was non-silent (peak 2987, 41,735 signal frames).

This is one detached cold boot of that preceding updated system. There were no
FAIL checks; overall status is PARTIAL for hardware, recovery and enrolled
streaming. Additional cold boots and interrupted-update recovery remain
separate work.

Overall status remains **PARTIAL**. Surface hardware is **UNTESTED**;
interrupted-boot and power-loss recovery are **NOT_RUN**. Enrolled streaming is
**BLOCKED**, and first-live-boot Tailscale persistence is **NOT_VERIFIED**.

## Previous revision: installation and detached boot

`20261121-110730-live` passed all implemented live checks and installed through
the production GUI into a disposable 64 GiB SATA disk. Source verification,
complete copy/readback, GPT checks, DATA expansion to 66,503,815,168 bytes and
verified local game/profile migration passed before GUI power-off.

`20261121-112520-test` then booted that installed disk through UEFI without
installation media. Existing-password login, migrated library/covers,
app/emulator checks, network, rotation, non-silent stereo audio and clean
power-off passed. These runs establish acceptance of the preceding revision,
not of the current compiled artifacts.

Earlier run `20261121-084511-live` authenticated a secondary profile and
confirmed browser real/effective/saved UID/GID 2000, global HOME return and
switching back to root. Historical runs `20261120-223547-test` and
`20261120-224052-test` passed display Apply/Keep at 1280×960, the 45-second
rollback, restoration to 1800×1200 and a detached cold boot retaining display
mode, DATA boot count and Tailscale state. These checks have not all been
repeated on the current revision.

## Current source checks

`artifacts/tests/bluetooth-correction-suite.log` records 356 passing Python
tests, including the three actual-client integration tests. The final bounded
syslog-reader change passed 19 focused Bluetooth tests, and the resolved
crypto gate passed 17 hardware-configuration tests. The crypto probe refuses
missing APIs and incorrect known-answer results.

## Earlier source checks

`artifacts/tests/system-update-final.log` records **371 Python tests**
(344 main and 27 application fixtures) and **five native unit test executables**
passing. The final root-relative DATA-mount correction passed **14 focused
tests** in `artifacts/tests/mount-data-final.log`. Two actual read-only
virtual-disk fixture tests also passed in
`artifacts/tests/vm-update-readonly-disk.log`.

The **22 updater backend tests** cover eligible-disk restrictions, explicit
confirmation, source identity/hash changes, verified private system backups,
EFI/root-only writes, unchanged fixture DATA/GPT and explicit restoration.
Restoration fixtures include a damaged root filesystem and rejected tampered
backups. They do not establish recovery after an actual power interruption.
Native UI tests cover update review, confirmation and completion validation.

Compiled kernel and BlueZ gates cover Surface LPSS/I2C/GPIO, HID-sensor/IIO,
USB/Bluetooth controller paths, classic HID and LE HOG, firmware, module
aliases and rootfs-time startup. Discovery and migration fixtures cover
read-only mounts, failed scans, source changes, file readback, ownership and
password preservation. Source fixtures supplement runtime evidence.

## Physical and release limits

Actual Surface touch, accelerometer axes, orientation/touch coordinates,
controller pairing/reconnect/gameplay, radios, speakers, battery, suspend and
installation remain unverified. The reported physical touchscreen and
Bluetooth failures still require retesting. Earlier Intel graphics and ELAN
registration logs show detection only. No universal controller-compatibility
or production-release pass is claimed. Failed earlier harness runs remain in
ignored local artifacts.
