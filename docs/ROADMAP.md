# Roadmap

Completed source work and executed VM evidence are distinguished from pending
runtime and hardware acceptance. Details are in [STATUS.md](STATUS.md) and the
[VM report](VM_TEST_REPORT.md).

## Implemented and tested

- [x] Pinned Buildroot/toolchain/kernel and capped build tooling.
- [x] Original C++/SDL3 HOME, carousel, dock and real status information.
- [x] Game library, search, favorites/history and per-game saves/presets.
- [x] Keyboard/gamepad/touch navigation and controller-mapping fixtures.
- [x] Eleven emulator engines with real runtime probes and NES input/SRAM test.
- [x] Browser render/HTTPS/close and HOME/global-return checks.
- [x] Moonlight/Tailscale packaging and bounded runtime; Wine Windows command test.
- [x] PokeMMO setup, validated imports and Windows application controls.
- [x] Guarded installation and pristine DATA growth on a disposable 64 GiB disk.
- [x] Three detached baseline UEFI boots, DATA/Tailscale persistence and 3 GiB/AHCI.
- [x] Baseline non-silent virtual audio and UI shutdown.
- [x] Self-contained outer/base image pipeline with sparse payload readback checks.
- [x] Recorded suite: 210 Python tests and five native C++ executables.

## Current validation

- [ ] Complete the revised image's live input/framebuffer acceptance.
- [ ] Validate detected display modes, Apply/Keep, timeout rollback and persistence.
- [ ] Install from the embedded base file and repeat detached cold boots.
- [ ] Verify current-artifact USB image writing and readback.

## Hardware and release

- [ ] Surface live boot, Intel acceleration and video decoding.
- [ ] Type Cover, touch, controllers, speakers, radios, battery and microSD.
- [ ] Brightness, display modes, suspend/resume and interrupted-boot recovery.
- [ ] Legal-game and Moonlight UI launch/return, pairing and streaming.
- [ ] Authorized Tailscale enrollment and remote streaming.
- [ ] Explicit Proton setup and online-client launch/login/gameplay.
- [ ] Additional per-system game compatibility and measured GameCube performance.
- [ ] Legal-info review, independent clean build and backup/recovery validation.
- [ ] Release only after VM and separate hardware acceptance gates pass.
