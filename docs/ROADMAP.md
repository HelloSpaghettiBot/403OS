# Roadmap

Completed source work and executed VM evidence are distinguished from pending
runtime and hardware acceptance. Details are in [STATUS.md](STATUS.md) and the
[VM report](VM_TEST_REPORT.md).

## Implemented source and fixture checks

- [x] Pinned Buildroot/toolchain/kernel and capped build tooling.
- [x] Original C++/SDL3 HOME, carousel, dock and real status information.
- [x] Game library, search, favorites/history and per-game saves/presets.
- [x] Keyboard/gamepad/touch navigation and controller-mapping fixtures.
- [x] First-run administrator setup, password-protected profiles, login/logout
  and private per-profile application storage with unprivileged launches.
- [x] USB/Bluetooth controller driver configuration and BlueZ classic HID/HOG
  support; physical pairing and reconnect behavior still require testing.
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
- [x] Recorded suite: 280 Python tests (253 main plus 27 app fixtures) and
  seven native test executables.

## Historical VM and media evidence

- [x] Eleven emulator runtime probes and NES input/SRAM test.
- [x] Browser render/HTTPS/close, HOME/global return, bounded Moonlight/Tailscale
  runtime and the Wine Windows command test.
- [x] Guarded installation and pristine DATA growth on a disposable 64 GiB disk.
- [x] Three detached baseline UEFI boots, DATA/Tailscale persistence and 3 GiB/AHCI.
- [x] Baseline non-silent virtual audio and UI shutdown.
- [x] Installed-VM display modes, Apply/Keep, 45-second rollback and cold-boot persistence.
- [x] Previous USB image write, complete readback and GPT verification.

## Current validation

- [x] Complete the revised image's live input/framebuffer acceptance.
- [x] Verify password enrollment, secondary-profile switch, unprivileged browser,
  global return and switch back to the administrator in the current VM.
- [x] Verify live rotation/restoration with unchanged browser/compositor PIDs
  and resolution settings in the current VM.
- [x] Install from the embedded base file through the GUI, verifying full copy,
  readback, DATA growth and power-off on a disposable 64 GiB SATA target.
- [ ] Repeat detached cold boots of the newly installed target.
- [ ] Refresh USB media with the revised image after its acceptance checks.

## Hardware and release

- [ ] Surface live boot, Intel acceleration and video decoding.
- [ ] Type Cover, touch, controllers, speakers, radios, battery and microSD.
- [ ] Brightness, display modes, suspend/resume and interrupted-boot recovery.
- [ ] Resolve the reported Surface touch failure and verify four screen
  orientations, touch coordinates and sensor recovery after suspend.
- [ ] Verify Ipega USB/Bluetooth pairing, mappings, reconnect and real gameplay.
- [ ] Legal-game and Moonlight UI launch/return, pairing and streaming.
- [ ] Authorized Tailscale enrollment and remote streaming.
- [ ] Explicit Proton setup and online-client launch/login/gameplay.
- [ ] Additional per-system game compatibility and measured GameCube performance.
- [ ] Legal-info review, independent clean build and backup/recovery validation.
- [ ] Release only after VM and separate hardware acceptance gates pass.

## Hardware feedback

Touch/controller driver configuration and sensor rotation now have source
checks and fixture coverage. Ipega-specific behavior remains unverified.
The reported Surface touchscreen failure remains unresolved until
physical testing confirms the new drivers. The current live VM passed profile,
rotation and GUI installation checks. Detached installed-system cold boots
remain pending; physical hardware, recovery and enrolled streaming are outside
the live VM evidence.
