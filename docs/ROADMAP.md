# Roadmap

Completed source work and executed VM evidence are distinguished from pending
runtime and hardware acceptance. Details are in [STATUS.md](STATUS.md) and the
[VM report](VM_TEST_REPORT.md).

## Current follow-up

- [x] Capability-based iPega composite-interface classification, including
  excluding keyboard/mouse reports from gamepad slots.
- [x] HOME left-stick navigation with a dead zone and per-device reset.
- [x] Open storage settings in existing-installation update mode by default.
- [x] Read-only GitHub stable-release checks, daily schedule, offline retry,
  explicit Settings check and official release details.
- [x] Distinguish a detected running internal disk from a missing disk, and
  discover ext4 installer media inserted after startup.
- [x] Preserve pending DATA journal replay during system-only update inspection;
  retain strict clean-filesystem requirements for partition growth.
- [x] Revised composite-controller and updater VM acceptance, including complete
  unchanged-DATA/GPT byte comparison (`20261126-143828-live`).
- [x] Updated SATA cold boot, existing password/library, late USB discovery and
  visible running internal disk (`20261126-181352-test`).
- [ ] Retain HOME focus across automatic library refresh.
- [ ] Physical iPega HOME/gameplay and update acceptance on Surface.

## Implemented source and fixture checks

- [x] Pinned Buildroot/toolchain/kernel and capped build tooling.
- [x] Original C++/SDL3 HOME, carousel, dock and real status information.
- [x] Game library, search, favorites/history and per-game saves/presets.
- [x] Automatic local/read-only game-volume discovery, live HOME catalog reload,
  title input and preservation of cached covers during offline refresh.
- [x] Keyboard/gamepad/touch navigation, SDL controller mapping, hotplug refresh
  and virtual-controller mapping fixtures; physical devices remain unverified.
- [x] iPega PG-9023S keyboard-named D-input classification and model-name
  fallback, covered by focused udev, Bluetooth-name and mapper fixtures.
- [x] Mixed keyboard/gamepad iPega permissions, automatic defaults and exact-image
  QEMU evdev acceptance through unprivileged SDL, mapper capture, HOME and a
  real NES core (`20261126-080750-live`); physical radio pairing remains pending.
- [x] First-run administrator setup, password-protected profiles, login/logout
  and private per-profile application storage with unprivileged launches.
- [x] USB/Bluetooth controller driver configuration and BlueZ classic HID/HOG
  support; physical pairing and reconnect behavior still require testing.
- [x] Guarded Bluetooth daemon/software-block recovery with verified power-on
  and specific diagnostics, preserving hardware blocks and pairing policy.
- [x] Rootfs-time `btusb` module loading with module/dependency/alias/firmware
  image gates, actual-controller readiness waits and private boot diagnostics.
- [x] Surface Go I2C HID, LPSS/GPIO and HID-sensor/IIO accelerometer kernel paths,
  checked against the resolved configuration; physical touch/sensors are unverified.
- [x] Live Weston rotation module, sensor scale/mount-matrix normalization,
  debounce, persisted toggle and resolution-trial pause, covered by fixtures.
- [x] Rotation acceptance probe for the real browser's logical geometry and
  preserved compositor/client processes; current live VM execution passed.
- [x] Eleven emulator engines and their runtime probes; browser, Moonlight,
  Tailscale and Wine packaging.
- [x] PokeMMO setup, validated imports and Windows application controls.
- [x] Self-contained outer/base image pipeline with sparse payload readback checks.
- [x] Installer migration of local live-USB games, saves, settings and profiles,
  preserving password hashes, ownership and permissions with file readback.
- [x] Recorded suite: 371 Python tests (344 main plus 27 app fixtures) and
  five native unit test executables; 14 final DATA-mount tests also pass.
- [x] Distinct live root/DATA partition identifiers and root-relative DATA
  selection, including Linux 6.12, NVMe/eMMC and guarded mount fixtures.
- [x] Offline updater with guarded EFI/root-only writes, verified system backups
  and explicit restoration; 22 backend tests and native UI review checks.

## Historical VM and media evidence

- [x] Eleven emulator runtime probes and NES input/SRAM test.
- [x] Browser render/HTTPS/close, HOME/global return, bounded Moonlight/Tailscale
  runtime and the Wine Windows command test.
- [x] Guarded installation and pristine DATA growth on a disposable 64 GiB disk.
- [x] Three detached baseline UEFI boots, DATA/Tailscale persistence and 3 GiB/AHCI.
- [x] Baseline non-silent virtual audio and UI shutdown.
- [x] Installed-VM display modes, Apply/Keep, 45-second rollback and cold-boot persistence.
- [x] Earlier secondary-profile browser launch under UID/GID 2000 and global
  return/switch to the administrator (`20261121-084511-live`).

## Bluetooth client correction

- [x] Persistent authentication agent with actual shipped-client PIN, passkey,
  confirmation, authorization, rejection and verified connection readback tests.
- [x] Pairing/connection failure diagnostics and radio-free socket API probe.
- [x] Independent power-button tap-to-sleep handler, repeat/hold/wake-event
  guards and storage-operation inhibition, covered by sixteen source fixtures.
- [x] Same-image UEFI ACPI button suspend/wake with a five-second blocked interval
  and renewed HOME input (`20261121-225552-live`, virtual s2idle).
- [ ] Physical Surface pairing, controller reconnect and button sleep/wake.

- [x] Reproduce BlueZ 5.86 empty noninteractive output using the shipped client
  against a valid isolated adapter; backport both upstream corrections.
- [x] Verify actual client list/show, power readback, discovery, pairing, trust,
  connect/disconnect and removal with isolated D-Bus fixtures.
- [x] Preserve asynchronously resolved device names during discovery, allow a
  20-second scan and label unnamed devices without duplicating their addresses.
  Native fixtures cover split events, alias changes and cached-name retention;
  the shipped client passes delayed-name and missing-name D-Bus fixtures.
- [x] Enable and gate AF_ALG hash/cipher interfaces and AES/ECB/CMAC.
- [x] Current-image UEFI client-output and AES/CMAC known-answer acceptance,
  with existing-password login and clean shutdown (`20261121-194547-live`).
- [x] Physical Surface device discovery reported working after the client fix.
- [ ] Physical Surface pairing, gameplay, reconnect, resume and updated name display.

## Previous revision’s boot/update validation

- [x] Updated test-media UEFI USB boot with an older internal SATA installation
  attached; root and DATA both selected from the USB (`20261121-173759-live`).
- [x] Production GUI update, verified EFI/root backup, copy/readback and power-off.
- [x] Bluetooth module/firmware/service startup and root-only diagnostic file.
- [x] Disposable-overlay login fixture, library/covers, all emulator probes,
  browser/global return, rotation, network and non-silent audio.
- [x] Private refresh retains profile/save file contents and original modes.
- [x] One detached UEFI boot of the current updated SATA system, retaining the
  existing password and library; runtime checks and power-off passed
  (`20261121-175027-test`).
- [ ] Physical Surface adapter detection, pairing, gameplay and reconnect.

## Previous revision’s installation acceptance

- [x] Complete live input/framebuffer acceptance on a local test-media variant
  sharing the preceding compiled root and base (`20261121-110730-live`).
- [x] Verify root password enrollment, library/cover loading and global HOME return.
- [x] Verify live rotation/restoration with unchanged browser/compositor PIDs
  and resolution settings in the current VM.
- [x] Install from the embedded base file through the GUI, verifying full copy,
  readback, DATA growth, local games/profile migration and power-off on a
  disposable 64 GiB SATA target.
- [x] Detached installed UEFI boot with existing-password login, migrated library,
  app/runtime checks, audio and clean power-off (`20261121-112520-test`).
- [ ] Repeat secondary-profile and display-trial acceptance on this revision.
- [ ] Repeat detached cold boots and recovery/persistence checks.

## Offline USB updates

- [x] Update an existing compatible 403 OS installation from a newer live USB,
  with a separate Cancel-default review and verified system backup before
  replacing EFI/root while retaining installed DATA and its partition layout.
- [x] Verify update copy/readback and unchanged installed games, saves,
  profiles, password hashes and settings on a disposable existing VM disk.
  Complete direct byte comparison passed for all DATA/GPT and new EFI/root.
- [x] Cold-boot the updated internal VM without its installer USB and verify
  existing-password login, game library, saved data and clean shutdown.
- [ ] Verify recovery after an update failure before physical acceptance.

## Hardware and release

- [ ] Surface live boot, Intel acceleration and video decoding.
- [ ] Type Cover, touch, controllers, speakers, radios, battery and microSD.
- [ ] Brightness, display modes, suspend/resume and interrupted-boot recovery.
- [ ] Resolve the reported Surface touch failure and verify four screen
  orientations, touch coordinates and sensor recovery after suspend.
- [ ] Verify Ipega USB/Bluetooth pairing, mappings, reconnect and real gameplay.
- [ ] Verify physical Bluetooth pairing, reconnect, resume and updated device
  names after the confirmed discovery recovery.
- [ ] Legal-game and Moonlight UI launch/return, pairing and streaming.
- [ ] Authorized Tailscale enrollment and remote streaming.
- [ ] Explicit Proton setup and online-client launch/login/gameplay.
- [ ] Additional per-system game compatibility and measured GameCube performance.
- [ ] Legal-info review, independent clean build and backup/recovery validation.
- [ ] Release only after VM and separate hardware acceptance gates pass.

## Hardware feedback

Touch/controller driver configuration and sensor rotation now have source
checks and fixture coverage. Ipega-specific behavior remains unverified.
The reported Surface touchscreen failure remains unresolved. The owner reports
Bluetooth discovery now works on the Surface; physical pairing, gameplay,
reconnect and resume have not been verified. The device-name display correction
has native and actual-client fixture coverage and still needs physical testing.
Earlier Intel HD 615 initialization
and ELAN registration show detection only. Current VM evidence covers root
authentication, rotation, correct USB root/DATA selection with an older internal
disk attached, the offline update UI and one detached updated-system boot.
The preceding revision separately
passed installation/migration and a detached boot with the retained password
and library. Physical hardware, recovery and enrolled
streaming remain outside that evidence.
