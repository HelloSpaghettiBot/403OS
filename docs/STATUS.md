# Status — November 26, 2026

403 OS is a development preview. The current revision classifies separate
iPega keyboard, mouse and gamepad interfaces by their actual capabilities and
adds HOME left-stick navigation. Physical iPega HOME/gameplay acceptance remains
pending; the earlier physical report was still failing.

The storage screen opens in update mode. It identifies a running internal disk
and explains how to boot the USB, instead of presenting it as a missing target.
Source discovery recognizes ext4 installer USBs inserted after startup. A
system-only update can preserve DATA with a pending internal ext4 journal;
it does not mount, resize or write DATA, and filesystem error flags remain a
refusal. Fresh-install growth retains its strict clean-filesystem requirements.

Settings also checks GitHub for stable releases, after startup, daily and on
request. Checks do not install software. No downloadable stable release has
been published; installation of updates continues to use the live USB.

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `44b486340364fcef8769a1b21427ed13da9063ade2dc3a9c74178e9af7c418f3` |
| Pristine internal-install image | `72dafbc2f3f4d576ce33d2b3a76b132b44d6eeb754748877acf18a7b31944fa5` |

Both images are 6,510,628,864 logical bytes. Packaging verified the complete
embedded installation payload. The current-image UEFI run passed simulated
controller input and the graphical update. Independent complete reads verified
that all installed DATA and GPT bytes stayed unchanged, and the replacement
EFI/root bytes matched the payload. The updated SATA system cold-booted with
its existing password and library, detected an installer USB inserted after
HOME, displayed its running internal drive, and shut down cleanly.

Source validation: 463 main Python tests completed with 23 environment-dependent
skips; the 23 updater tests also passed after the final journal regression fix.
All nine compiled native test programs and 49 VM-harness tests passed.
The prior revision also passed 27 application setup tests. See
[VM results](VM_TEST_REPORT.md) and [controllers](BLUETOOTH.md) for the distinction
between simulated input and physical Bluetooth acceptance.

## Earlier pairing and power corrections

The pairing path now keeps a real authentication agent alive and presents PIN,
passkey, confirmation and authorization requests in HOME. The preceding
one-shot command did not register its agent; physical pairing was reported
failing despite working discovery. Twelve actual shipped-client tests now pass
on a private D-Bus, including real agent callbacks and explicit rejection.
Pairing succeeds only after paired/trusted/connected state readback. Failed
pairing and connection attempts save fresh private daemon diagnostics.

Tap-to-sleep runs in an independent input service, also used by the Settings
suspend action. Sixteen focused tests cover taps, holds, repeats, dropped/wake
events, hotplug and storage-operation locks. Physical Surface sleep/wake,
controller pairing and reconnect remain acceptance work.

The shipped BlueZ 5.86 client reproduced successful but empty adapter, power
and device-command output against a valid isolated adapter. Upstream fixes
are backported. The preceding five actual-client tests covered listing,
power readback, discovery, pairing, trust, connect, disconnect, removal, delayed
name resolution and devices without a remote name.
The helper reports silent client failures separately and saves bounded daemon
errors alongside transport diagnostics. See [Bluetooth](BLUETOOTH.md).

Discovery now runs for up to 20 seconds and applies arriving Name/Alias changes
to the visible device list. Friendly unnamed-device labels replace address-only
names, and selecting a device reads its latest name and alias. Native tests pass
for partial events, byte-by-byte updates and retention of resolved names when
a cached address alias is returned. The Bluetooth name-display binary passed
the earlier UEFI live-boot acceptance; real-radio name presentation still
needs a physical test.

Settings now labels controller setup as **Controllers and button mapping**.
Its SDL device list polls for hotplug, offers manual refresh, and labels detected
inputs as connected/mapped or connected/unmapped. If Bluetooth reports a link
without SDL input, the screen gives a gamepad-mode recovery hint. The focused
virtual-controller mapping test passed in the Buildroot runtime. Physical Surface controller detection and
button mapping remain unverified.

The earlier iPega rule handled keyboard-only and mixed keyboard/joystick tagging,
including generic names and product variants from the same vendor. The mapper
uses a friendly iPega name when the HID identity is known. Physical pairing and
button mapping on the Surface still need confirmation.

Earlier-image UEFI run `20261121-225552-live` passed all implemented runtime
checks, including a power-button suspend/wake cycle, existing-password login, the
28-game library and covers,
eleven emulator engines, NES input/SRAM, browser/global return, live rotation,
networking, non-silent audio and clean GUI shutdown. It selected USB root and
DATA correctly with an older internal installation attached. Overall status is
PARTIAL for remaining hardware, recovery and enrolled-streaming acceptance.
The VM does not provide the Surface radio; name-delivery behavior is covered
by the native and isolated actual-client tests described above.

Sleep acceptance used virtual firmware with S3 disabled. The helper remained
blocked for 5.02 seconds, then returned after the next ACPI power event; HOME
accepted fresh input and the remaining runtime checks passed. Virtual clocks
kept ticking during s2idle, so this proves the suspend/wake path without
establishing physical CPU sleep residency or battery consumption. Earlier
virtual S3 wake attempts failed. The Surface kernel sleep policy is unchanged.

AF_ALG hash/cipher APIs and AES/ECB/CMAC are built into the kernel. The new
runtime gates check real client shell output and public crypto known answers.
The preceding revision's UEFI run `20261121-191015-live` passed client output, AES and CMAC known
answers, module/service startup, login, library/covers, all emulator probes,
rotation, network, audio and GUI power-off. Root and DATA came from the USB
with an older internal disk attached. Overall status remains PARTIAL; Surface
pairing, reconnect and resume still require physical testing. The owner now
reports that device discovery works on the Surface.

The preceding full Python suite passed 356 tests. The final diagnostic-log change
passed 19 Bluetooth tests, and the resolved crypto configuration passed
17 focused tests. Source and fixture results do not prove physical pairing.

## Previously validated installation and updates

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

The preceding revision’s VM exercised the production update review, verified system backup,
copy/readback and GUI power-off against a disposable older installation.
Independent direct virtual-disk reads compared the complete 64 GiB target:
DATA and both GPT areas were byte-for-byte unchanged, and EFI/root matched
the new pristine payload exactly.

Detached UEFI run `20261121-175027-test` then booted the updated SATA disk
without its installation USB. Existing-password login, the retained 28-game
library and covers, all emulator/app checks, rotation, network, non-silent
audio and clean power-off passed. This establishes one detached cold boot of that revision; additional boots and recovery remain separate acceptance work.
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

The preceding revision’s VM passed module/service startup, private diagnostic permissions,
existing-profile login, library/covers, all eleven emulator probes, NES
input/SRAM, browser/global HOME return, rotation, networking, non-silent stereo
audio and clean power-off. VM evidence does not establish physical Bluetooth
operation. Surface discovery is supported by the owner's report; pairing,
gameplay, reconnect and resume remain unverified.

Earlier physical logs showed Intel HD 615 initialization and ELAN touch-device
registration. Those are detection evidence only. The reported touchscreen
failure remains unresolved; the updated Bluetooth name display still requires
physical testing. Accelerometer
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
