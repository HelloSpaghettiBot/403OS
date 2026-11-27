# VM test report

## November 26: current image live boot and update

UEFI run `20261126-185544-live` tested the current image and embedded payload
listed below, using OVMF/KVM, four virtual CPUs, 4 GiB RAM, a 1800×1200 virtio
framebuffer and an older disposable SATA installation. Root and DATA selected
the USB correctly. Every executed check passed; overall status is PARTIAL for
physical hardware, additional boots, recovery and enrolled streaming.

The simulated iPega's separate keyboard and mouse interfaces stayed outside
the gamepad list. Its default profile and real evdev input worked in the
unprivileged SDL mapper and a real NES core. HOME responded to D-pad and analog
navigation; the graphical mapper opened and returned with the controller B
button. This validates the shipped HID/input path, not a Bluetooth radio.

The production updater completed its Cancel-default review, verified the
source and recovery backup, and copied/read back both system partitions. After
the completion event, one explicit Enter on the default Finish action powered
off QEMU cleanly. Completion and shutdown were verified in the runtime log.

An independent complete read-only byte comparison verified the updated disk:

| Compared range | Bytes | Result |
| --- | ---: | --- |
| Disk prefix and primary GPT | 1,048,576 | Unchanged |
| Entire DATA region and backup GPT | 66,503,835,648 | Unchanged |
| EFI and root system partitions | 2,214,592,512 | Exact match to current payload |

All eleven emulator probes, NES input/SRAM reload, browser/global HOME return,
networking, live rotation and non-silent audio passed. BlueZ service, transport,
client output, crypto, local sockets and private diagnostics also passed.
Physical controller service discovery, pairing, reconnect and gameplay remain
unverified. Late USB insertion evidence below belongs to the preceding
revision's `20261126-181352-test` run.

Detached UEFI run `20261126-190631-test` booted an overlay of the updated SATA
disk with no installer USB attached. The existing root password authenticated
without enrollment, and the retained 28-game catalog and covers loaded. Games
launched through HOME and its profile supervisor using the expected cores:

| System | Core | Rendered frames | SRAM written | HOME return |
| --- | --- | ---: | --- | --- |
| GBA | mGBA | 32 | Yes | Clean |
| N64 | Mupen64Plus-Next | 57 | Yes | Clean |
| SNES | Snes9x | 39 | Yes | Clean |

These are bounded launch/save checks, not gameplay or performance certification.
The detached run also passed the browser/global return, all eleven engine
probes, network, audio and normal GUI poweroff. All executed checks passed;
physical hardware, interrupted-update recovery and enrolled streaming remain
separate acceptance work.

Current focused source validation passed 24 pairing tests within 72 unique
Bluetooth tests, native core and control tests, and 14 VM safety tests. These
fixture checks supplement the current runtime results without simulating a
physical Bluetooth radio.

## November 26: game launch and Bluetooth discovery follow-up

Detached UEFI run `20261126-184143-test` booted the previously delivered update
on a disposable SATA installation, without installer media. Its existing root
password and 28-game catalog were retained. Games launched through HOME and
the profile supervisor using mGBA, Mupen64Plus-Next and Snes9x; all three
rendered frames, wrote SRAM and returned cleanly through the global HOME
shortcut. The harness checks the actual core identity, avoiding a false pass
from selecting a different system. This establishes the installed launch path
in QEMU, not the state of a physical Surface installation.

The Bluetooth service-discovery failure was reproduced independently on a
private D-Bus. An already-paired disconnected fixture left ServicesResolved
false until Connect, but the old helper never issued Connect. The corrected
helper passed 24 pairing tests. Across compatible host interpreters, 72 unique
Bluetooth tests passed; these include the protocol fixtures and existing
recovery/input tests. They do not emulate a radio or certify a physical pad.

## November 26: controller and update follow-up

UEFI run `20261126-143828-live` tested the preceding image with a disposable
64 GiB SATA installation attached. All executed checks passed. The graphical
updater detected the installed disk, defaulted its review to Cancel, verified
the source, created and verified an EFI/root recovery copy, replaced and read
back both system partitions, and shut down cleanly.

An independent offline comparison read every byte of both virtual disks:
1,048,576 bytes of prefix/GPT and 66,503,835,648 bytes of DATA plus backup GPT
were unchanged. The new 2,214,592,512 bytes of EFI/root exactly matched the
pristine installation payload. The test deliberately retained a pending DATA
journal; system replacement neither mounted nor modified that filesystem.

Detached UEFI run `20261126-181352-test` then booted an overlay of that
updated SATA system. The existing root password authenticated without another
setup prompt; the 28-game fixture library and covers loaded. Attaching the
installer USB after HOME produced a valid source while the disk list identified
the running internal disk and prevented writes to it. The visible page gave
USB-boot instructions, and the GUI powered the VM off cleanly.

The insertion harness waits for the automatic catalog refresh before navigating
HOME: rebuilding the current library model resets focus. Retaining focus across
library refresh is a remaining usability improvement. Earlier insertion runs
reached login/library/discovery successfully but failed their automated shutdown
sequence; those incomplete runs are not reported as complete acceptance.

The revised iPega fixture exposed separate keyboard and mouse collections
alongside the gamepad. Guest udev excluded both sibling collections from the
gamepad list. The unprivileged SDL mapper opened the actual gamepad with its
default profile and captured real evdev button input. A real NES core received
that input, HOME responded to D-pad and analog-stick navigation, and the
shipped graphical mapper opened and returned with the controller B button.
This tests the guest HID/input path, not Bluetooth radio pairing.

The run also passed all eleven emulator probes, NES SRAM/reload, browser and
global HOME return, networking, live rotation, non-silent audio, BlueZ service,
client output, transport, crypto and local socket checks. Its overall status
is PARTIAL for explicitly untested physical hardware, recovery and enrolled
streaming. Physical iPega behavior was previously reported failing and remains
pending confirmation on the Surface.

Source validation completed 463 main Python tests with 23 environment-dependent
skips, followed by 23 passing updater tests including the new execution-stage
journal regression. All nine native test programs and 49 VM-harness tests
passed. The prior revision also passed 27 application setup tests.

## November 26: default controller input

UEFI run `20261126-080750-live` used the preceding public image,
OVMF/KVM, four virtual CPUs, 4 GiB RAM and a 1800×1200 virtio framebuffer.
The run completed with no failed implemented checks; overall status is PARTIAL
for the explicitly untested physical hardware, additional boots, recovery and
enrolled-streaming checks.

A host uinput fixture advertised Bluetooth bus 5, iPega VID/PID `1949:0402`,
`PG-9023S Keyboard`, keyboard keys, gamepad buttons, sticks and a D-pad hat.
QEMU virtio-input-host preserved its identity, capabilities and real evdev
events in the guest. The test temporarily bound the previous udev rule over
the packaged file and reproduced unprivileged SDL enumeration failure with
both keyboard and joystick tags. Restoring the packaged rule cleared the
keyboard tag and granted the gamepad group access.

The same target mapper process then opened the device as UID 1000, reported
15 available mapped buttons and six mapped axes, listed iPega as connected and
mapped, and captured its raw south button. A separately launched real FCEUmm
core received the controller through the shared frontend and produced a changed
framebuffer compared with the no-input baseline. This used the original test
cartridge and omitted the frontend synthetic-input override. Controller D-pad
events changed HOME focus; the shipped graphical mapper launched from Settings
and returned successfully after the controller B event.

The image also passed root/DATA mounting, profile enrollment, keyboard/framebuffer
input, all eleven emulator binary probes, NES SRAM/reload, browser/global HOME
return, live rotation, networking, non-silent audio and GUI power-off. BlueZ
daemon, transport loading, client output, crypto and local socket checks passed;
no physical Bluetooth adapter or real controller was passed through. Over-the-air
pairing and individual games remain unverified.

Build provenance: full capped Buildroot compilation was followed by an offline
refresh of the VM acceptance helper. Its image contents, root ownership and
executable mode were read back; ext4 integrity and the rebuilt installation
payload were checked before this exact-image VM run. Runtime controller
binaries were unchanged by that helper refresh. Source checks passed 435 main
Python tests, four additional VM-scope tests, 27 application tests and eight
native test executables. Private logs and disks remain in ignored artifacts.

## Pairing and power source checks

The actual shipped BlueZ client reproduces the missing agent in one-shot
pairing. Its replacement keeps an interactive agent alive and waits for
registration before pairing. Twelve private D-Bus tests pass, including legacy
PIN, numeric passkey, confirmation, authorization, rejection and final
paired/trusted/connected readback. No host Bluetooth radio is used.

Current native tests cover prompt validation, positive completion requirements
and a live child receiving multiple replies on a persistent input channel.
Fifty-two focused Bluetooth tests and sixteen power tests pass. All 412 main-suite tests passed, including sixteen power tests and twelve
actual-client integration tests. The VM harness has 45 passing source tests.
Power tests use temporary state files and real lock contention; they never
suspend the host. Source checks do not establish Surface sleep or pairing.

## Current compiled artifacts

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `5ced07f48837bb826a330de08a0f83a4206690024c64af0b098bb6737c5ca617` |
| Pristine installation payload | `44838eeffed3f3a5fcb79a681f5d5a0de78db9f075817c0f1f5035ce0d28f50d` |

Both are 6,510,628,864 logical bytes. Packaging passed complete embedded
payload readback, root ownership, mode 0444 and absence of a recursive payload.
Current-image live runtime and complete update byte comparison passed as
described above. Earlier runs retain their original hashes and apply only to
their recorded revisions.

## Previous verified revision: live boot

UEFI run `20261121-225552-live` booted the updated compiled UI and pristine
installation payload through OVMF/KVM, with 4 GiB RAM, four CPUs and an
1920×1080 Bochs framebuffer, host CPU model and PS/2 keyboard. An older SATA installation was attached; root and DATA
came from the selected USB. All implemented checks passed: power-button suspend/wake, existing-password
login, the 28-game library and covers, eleven emulator runtime probes, NES
input/SRAM, real client output and AES/CMAC known answers, browser/global
return, live rotation, network, non-silent audio and GUI power-off. Overall
status remains PARTIAL for physical hardware, recovery and enrolled streaming.
The updater operation was not repeated for this pairing/power revision.

The packaged UI binary matches the compiled target binary. The live test uses
a disposable overlay; its synthetic login fixture leaves source authentication
unchanged. No physical Bluetooth adapter was passed through to QEMU.

The real ACPI button passed through the shipped evdev power service. With
virtual S3 disabled, the kernel selected s2idle without a guest policy override.
The helper stayed blocked for the complete 5.02-second host interval, then
reported matching entry/resume identifiers after a second ACPI button event.
Fresh HOME keyboard input and the remaining runtime checks passed afterward.
The reported BOOTTIME-minus-MONOTONIC difference was 0.0 seconds: virtual idle
can leave timekeeping running while userspace/devices suspend. This is a
suspend/wake check, not proof of deep CPU residency or physical power savings.
Earlier virtual S3 attempts entered sleep but did not recover; S3 resume is not
claimed. Physical Surface tap/wake and controller reconnect remain untested.

## Bluetooth device-name source checks

Discovery now runs for up to 20 seconds, applies asynchronous Name/Alias events
to visible rows and retains resolved names when a cached listing contains an
address alias. Unnamed devices have a friendly fallback label; selecting a
device reads its latest name and alias. Current native parser tests pass for
split events, byte-by-byte delivery, alias changes and cached-name retention.
Native ControlCenter checks also pass.

The preceding five shipped-client integration tests passed against isolated D-Bus fixtures,
including a name arriving during discovery and an absent optional remote name
with BlueZ's address alias. These checks access no host radio. The name-display
binary completed the live-boot checks above. Discovery name updates remain
covered by native and actual-client fixtures; physical display acceptance
is still pending.

The owner reports that device discovery now works on the physical Surface.
This confirms reported discovery recovery; physical pairing, gameplay,
reconnect, resume and the updated name display remain unverified.

## Preceding revision: Bluetooth correction acceptance

The actual pre-patch BlueZ 5.86 client returned empty output for list, show and
power against a valid private D-Bus Adapter1 fixture. All three integration
tests failed before the backports and passed afterward. Tests cover adapter
listing, power off/on with readback, discovery, pairing, trust, connection,
disconnection and removal. They access no host radio.

The kernel enables AF_ALG hash/cipher APIs plus AES/ECB/CMAC. The preceding revision's UEFI run
`20261121-191015-live` passed actual shell output and both crypto known-answer
checks, transport/service startup, private diagnostics, existing-profile login,
library/covers, all eleven emulator runtimes, input/SRAM, browser/global return,
live rotation, network, non-silent audio and GUI power-off. It used OVMF/KVM,
4 GiB RAM, four CPUs, 1800×1200 and an older SATA installation attached. Root
and DATA selected the USB correctly; no direct kernel boot was used.
Every implemented check passed; overall status is PARTIAL for hardware,
recovery and enrolled streaming. The exact public outer image was not booted
separately; this test variant shares its compiled root and pristine payload.
This run does not cover the subsequent device-name display changes. Physical
Surface pairing, gameplay, reconnect and resume remain unverified.

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

Overall status remains **PARTIAL**. Comprehensive Surface hardware acceptance is pending;
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

`artifacts/tests/bluetooth-correction-suite.log` records the preceding revision's 356 passing Python
tests, including the three actual-client integration tests. The final bounded
syslog-reader change passed 19 focused Bluetooth tests, and the resolved
crypto gate passed 17 hardware-configuration tests. The crypto probe refuses
missing APIs and incorrect known-answer results.

The current device-name checks pass in
`artifacts/tests/bluetooth-device-names-native-json.log`,
`artifacts/tests/bluetooth-device-names-native-controls.log` and
`artifacts/tests/bluetooth-device-names-bluez-client.log` (five integration tests).

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
installation remain unverified. Surface Bluetooth discovery is now reported
working by the owner; pairing, gameplay, reconnect, resume and the updated name
display still need physical tests. The reported touchscreen failure remains
unresolved. Earlier Intel graphics and ELAN
registration logs show detection only. No universal controller-compatibility
or production-release pass is claimed. Failed earlier harness runs remain in
ignored local artifacts.
