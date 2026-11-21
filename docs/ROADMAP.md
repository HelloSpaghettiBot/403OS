# Roadmap

Checked items have executed evidence or completed source work explicitly
identified below. Runtime and hardware acceptance remain separate gates.

- [x] Read all eight PDF pages and inspect the design reference.
- [x] Verify FILES mount and create the separate repository.
- [x] Pin Buildroot/toolchain/kernel; implement capped, logged build tooling.
- [x] Compile and live-boot the original HOME with actual library covers.
- [x] Verify UEFI, 1800x1200, virtual keyboard/gamepad/touch, network and UI poweroff.
- [x] Compile all eleven engines and pass real binary/core runtime probes in VM.
- [x] Execute real MIT NES homebrew input and SRAM reload in VM.
- [x] Render owner GBA/SNES/N64 games in earlier native tests; persist saves/states.
- [x] Implement favorites, recents, search, scanner and per-game native-1x presets.
- [x] Implement controller mapping; pass virtual joystick mapping/cancel tests.
- [x] Compile the complete browser/Moonlight/Tailscale/Wine64/UMU application image.
- [x] Verify Cog render/HTTPS/Ctrl+W and actual HOME launch/global keyboard return.
- [x] Verify bounded Moonlight/Tailscale runtime and real Wine Windows command exit.
- [x] Implement WiFi/BlueZ/Tailscale/Moonlight/settings UI and guarded commands.
- [x] Implement PokeMMO setup and guided validated owner ROM import.
- [x] Identify exact DW3 Online client and implement unchanged owner-client import.
- [x] Add Wine64/UMU, private unprivileged prefixes and Windows application picker.
- [x] Implement guarded physical installer; pass disposable safety fixtures.
- [x] Implement pristine cloned-DATA growth helper; pass regular-file GPT tests.
- [x] Implement authorized whole-USB flasher and pass guard/disposable-file tests.
- [x] Verify actual non-silent VM PCM after first-boot mixer/package corrections.
- [x] Pass five native C++ executables, 210 Python tests and shell syntax checks.
- [x] Pass every implemented live check on image 73242f, including audio.
- [x] Repeat live acceptance for corrected pristine image 3cdb38b2.
- [x] Correct final DATA verification; pass guarded 64 GiB VM install and DATA growth.
- [x] UEFI-boot the corrected installed disk without installer media.
- [x] Obtain two installed cold-boot persistence passes after the initial boot.
- [x] Test 3 GiB/2-vCPU with AHCI/1280x800.
- [ ] Test interrupted-boot recovery.
- [ ] Test Proton runtime setup and DW3 visible launch/clean HOME return.
- [ ] Test legal-game and Moonlight UI launch/return, pairing and configuration.
- [ ] Test additional emulator engines with suitable legal game data.
- [ ] Generate/review legal-info and verify clean-build reproducibility.
- [x] Flash/readback-verify the earlier authorized whole-USB image.
- [x] Implement self-contained outer USB with one sparse pristine inner payload.
- [x] Compile outer/base images and verify embedded full SHA/ownership.
- [ ] Validate installed-OS detected mode selection, 45-second rollback and Keep persistence.
- [ ] Rebuild both images with display settings and validate responsive layout/input.
- [ ] Validate live source discovery and embedded-file VM installation.
- [ ] Reflash/readback-verify the self-contained USB after matching acceptance.
- [ ] Surface live boot/device inventory; Intel/touch/Type Cover/audio/radios/power.
- [ ] Authorized Sunshine LAN stream, tailnet enrollment and remote streaming.
- [ ] Physical 4 GiB/8 GiB acceptance, backup/recovery and separately confirmed installation.
- [ ] Release after VM and separate physical acceptance gates pass.

The preceding pristine image `3cdb38b2…` passed guarded VM installation,
three detached UEFI boots and live acceptance; boot results are PARTIAL for
physical hardware/recovery/streaming scope. The self-contained two-image
pipeline compiled both artifacts and verified embedded SHA/ownership. New
live acceptance last failed an input acknowledgment wait despite confirming
1800×1200. Display-settings implementation/rebuild, mode trial/rollback/Keep
tests, complete live/embedded install checks and USB reflash remain pending. No Surface internal-disk erase is authorized.

Initial source commit `6e4f165` is published on GitHub, not a binary release.
