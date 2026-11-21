# Status — November 21, 2026

403 OS remains a development preview. The current image includes automatic
game discovery, title search, live-USB data migration, password-protected
profiles, Bluetooth recovery controls and native output rotation.

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `0e0b0a8574b6f0a2750216852011890d4eca713e9f1342c488f9886131122197` |
| Pristine internal-install image | `51a695ef6b740e0e227b423b2c1e82d7e6a2f152a086f8f3e99919fff2f0161e` |

Both images are 6,510,628,864 logical bytes. Packaging verified the complete
embedded installation payload and its root-owned read-only metadata.

## Implemented changes

- The first HOME profile is root (UID zero), enrolled with a new password.
  Cold boots require login. Root can add users; their applications permanently
  drop to separate Unix identities with private homes, browser data and saves.
- The resolved kernel includes Surface Go LPSS/I2C/GPIO touch paths, HID-sensor
  and IIO accelerometer support, and broad upstream USB/Bluetooth gamepad
  drivers. BlueZ classic HID and Bluetooth LE HOG plugins are compiled in.
  SDL mappings and the custom mapping wizard are included. Individual physical
  controller models still need pairing, reconnect and gameplay tests.
- A native Weston module rotates the internal output without restarting the
  compositor or applications. The sensor service applies scale and mount
  matrices with debounce, reports missing readings and pauses during display
  resolution trials. The automatic rotation toggle is persistent.
- The GUI installer discovers the pristine embedded image, reviews the selected
  disk with Cancel selected by default, then verifies source, copy, complete
  readback, GPT and fresh DATA expansion. It copies supported local live-USB
  games, saves, settings and profiles with ownership/permissions and file
  readback checks, retaining existing password hashes before offering power-off.
- A guarded service discovers local games and read-only external game volumes;
  HOME reloads the catalog and title search accepts keyboard or on-screen input.
  Ordinary discovery preserves cached covers and makes no network requests.
- Bluetooth Scan can clear Bluetooth-only software blocks and start an
  unavailable daemon, with power-state readback and specific diagnostics.
  Hardware blocks are retained; no speculative USB reset or pairing-policy
  weakening is applied.

The recorded suite covers **327 Python tests** (300 main and 27 application
fixtures) and **five native unit test executables**, recorded in
`artifacts/tests/bluetooth-final.log`. The profile fixtures
include password hashing, login refusal, identity separation, socket
permissions and first-boot directory permissions. Compiled kernel and BlueZ
configuration checks passed.

## Current Bluetooth correction

The USB Bluetooth transport now loads as `btusb` after the real root filesystem
and firmware are available, following Pop!_OS’s modular configuration. The
build checks the installed module, dependency/USB aliases, rootfs startup and
firmware. Scan waits for an actual BlueZ controller; empty successful lists do
not establish readiness. Private boot/failure diagnostics retain bounded
transport, firmware and kernel evidence. See [Bluetooth](BLUETOOTH.md).

The firmware matches Pop!_OS’s corresponding files. This removes an early
firmware-availability risk; the reported Surface failure still needs hardware
logs and a physical cold-boot test. No physical Bluetooth success is claimed.

## Current live acceptance

UEFI run `20261121-135952-live` passed the implemented live checks on the
updated personal-media variant: Bluetooth module/service startup, root-only
boot diagnostics, password login, library/covers, all eleven emulator probes,
homebrew input/SRAM, browser/global HOME return, live rotation, network, audio
and GUI power-off. Its compiled root filesystem and pristine installation
payload match the public images above; the exact public outer image was not
booted separately.

The host prepared a synthetic root password only in the disposable qcow2
write overlay so the test could exercise login. The backing image and the
personal password record were unchanged. Packaging separately verified file
contents and preserved profile/save ownership and permissions. Overall VM
status is PARTIAL because physical hardware, recovery and enrolled streaming
remain outside the test. Bluetooth adapter detection and pairing on the
Surface remain unconfirmed.

## Previous revision’s installation acceptance

Live run `20261121-110730-live` passed every implemented check, including
root enrollment, automatic library/cover loading, browser rotation without
restarting its processes, all eleven emulator probes, audio and global HOME
return. GUI installation into a disposable 64 GiB SATA disk passed complete
copy/readback, GPT checks, DATA expansion and verified local game/profile
migration, followed by clean power-off.

Detached UEFI run `20261121-112520-test` booted that installed disk with no
installation media. Login with the existing password, migrated library/cover
loading, app/runtime checks, audio and clean power-off passed. Those runs used
a local test-media variant of the preceding revision; they do not establish
acceptance of the current public outer-image SHA.
Overall status remains PARTIAL for hardware, recovery and enrolled streaming.
See the [VM report](VM_TEST_REPORT.md).

Earlier runs separately established secondary-profile UID/GID dropping and
browser return (`20261121-084511-live`), plus installed display Apply/Keep,
45-second rollback and cold-boot persistence on November 20. Those checks were
not repeated as part of the new migration acceptance.

## Physical and release limits

Earlier physical boot logs showed Intel HD 615 graphics initialization and
ELAN touch-device registration. This is detection evidence only. The reported
touch and Bluetooth failures still need physical retesting. Accelerometer
axes, four orientations, touch coordinates, pairing/reconnect, radios, audio,
battery and suspend remain physically unverified. VM input injection does not
establish compatibility with every real controller.

Proton setup, online-game login/gameplay and enrolled streaming remain
separate acceptance work. No production-readiness or physical-device
installation pass is claimed. Public source and base images contain no game
files, credentials or private device records.

See [installation](INSTALL_RECOVERY.md), [roadmap](ROADMAP.md) and
[test results](VM_TEST_REPORT.md).
