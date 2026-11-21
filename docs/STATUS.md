# Status — November 20, 2026

**Development build; live VM checks pass, acceptance incomplete.** The latest
completed run, `artifacts/vm-tests/20261120-173635-live/`, reports **PARTIAL**
with every implemented live check **PASS** and no failed checks. It tested
`output/images/surface-go.img`, 6,510,628,864 bytes, SHA-256
`73242f0c64f9a00447805c32ff3ced643619f8d5b357e6365c5e3f2a8008e635`.
Build log: `artifacts/build/tailscale-idempotent-image.log`.

**Internal installation is blocked.** The 64 GiB VM install
`20261120-173902-install` verified the clone checksum, then failed the final
DATA-growth validation after `resize2fs` and `e2fsck` completed. A fresh
partition-descriptor validation fix is compiled into corrected image SHA-256
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`.
Its new `20261120-174857-install` run is in progress; no corrected install,
cold-boot or matching live pass is claimed yet. The flashed 73242f USB remains
live-test media with the earlier installer failure; corrected delivery needs
new test evidence and a verified reflash.

The remaining partial scope includes physical Surface hardware, installed-disk
cold-boot persistence, recovery and enrolled network/streaming operation.
Compilation and virtual-device checks do not establish those capabilities.
Build output filenames are mutable; retain the checksum and matching evidence.

Verified evidence:

- Pinned Buildroot 2026.08.2, Bootlin x86-64 glibc 2025.08-1, Linux 6.12.118,
  SDL3 3.4.20 and Mesa 26.1.8. Builds use ten workers, a 24 GiB memory throttle,
  32 GiB hard cap and 1 GiB swap, with a separate 32 GiB container cap.
  VM wrappers retain their 6 GiB host cap and shared heavy-task lock.
- All eleven emulator engines compiled and installed: FCEUmm, Snes9x,
  Gambatte, mGBA, Genesis Plus GX, PCSX ReARMed, Mupen64Plus Next,
  melonDS DS, Flycast, PPSSPP and standalone Dolphin. The completed live runs
  passed the actual eleven-engine binary/core runtime probes and real NES
  homebrew input/SRAM save/reload. A binary probe does not establish
  gameplay compatibility or performance for each system.
- The image includes Cog/WPE WebKit, Moonlight Embedded, official Tailscale
  binaries, Wine64, UMU, Mesa Iris/ANV and the Intel media driver.
  `artifacts/build/packages.json` currently contains 278 package records.
- The completed 173635 run passed QEMU/KVM UEFI graphical HOME at 1800×1200,
  4096 MiB and four vCPUs; root/DATA mounts; QMP keyboard framebuffer changes;
  guest SDL keyboard/virtual-gamepad/touch injection; DHCP/DNS/HTTPS;
  Bluetooth daemon startup; and confirmed UI poweroff/QEMU exit.
  SDL3 used Wayland/GLES2 with software rendering, not verified Intel graphics.
- Cog passed real rendered-page/JavaScript checks, CA-validated HTTPS and
  keyboard Ctrl+W exit. The harness also launched the browser from the actual
  HOME dock and returned through the global Ctrl+Alt+Home input path.
  Physical three-finger touch and controller operation remain untested.
- Wine64 passed an actual unprivileged Windows `cmd.exe` launch/exit.
  Moonlight passed bounded help/version process launch/exit. Tailscale passed
  daemon startup and temporary IPv4/IPv6 policy-routing checks in the final run
  after the duplicate-start race was corrected. Its state remains unenrolled
  (`NeedsLogin`); no stream, pairing or enrollment is claimed.
- Original mountain wallpaper, original dock/system illustrations and licensed
  Cantarell font atlases. Earlier private-library evidence includes 28 games
  and 18 actual covers from the owner's Games drive, shared read only.
  The completed November 20 runs had no owner game-library share. ROMs and
  commercial cover art are absent from source and release images.
- Earlier native tests rendered the owner's GBA/SNES games and N64 3D intro;
  save persistence, state roundtrip and graceful signal/SRAM checks passed.
  These are not Surface performance results.
- Final-run audio passed strict captured-PCM verification: 42,523 frames at
  44,100 Hz (0.96424 seconds), peak 2,987 and 41,617 non-silent frames.
  Raw WAV SHA-256:
  `90a7b7994d925339a936c1333f8637afe0fce70a617271bbae8db850c3e9f227`.
  The QEMU header sizes remain unset; observed PCM was validated without
  rewriting the recording. This does not verify physical speakers.
- The corrected-source Python suite passed all 197 tests; evidence:
  `artifacts/tests/final-corrected-python.log`. Five native C++ executables and
  shell syntax checks also passed; full-script evidence remains in
  `artifacts/tests/final-complete.log`. These do not substitute for the pending
  corrected-image installation/cold-boot/live tests.

Implemented; remaining acceptance limits:

- Dolphin/Xwayland uses native 1x presets with explicit compatibility tradeoffs.
  Full-speed GameCube operation and real controller bindings need hardware tests.
- Mesa Iris, Intel media driver, redistributable Intel/Qualcomm firmware and
  regulatory data are packaged; actual Surface device IDs remain unverified.
- WiFi/BlueZ/Tailscale/Moonlight service UI, masked on-screen keyboard, volume,
  brightness, library search, favorites/history and per-game state are present.
  Guided SDL mapping and global-return parser fixtures pass. Pairing/configuration
  UI acceptance and physical touch/controller tests remain separate work.
- Official PokeMMO first-run setup uses an official client download and pinned
  Java runtime; neither is bundled. Digimon World 3 Online imports an unchanged
  owner client. Neither online game has passed target launch/login/gameplay.
  UMU 1.4.4 and explicit Proton/Steam-runtime setup are packaged. Wine/Proton
  run as gaming UID 1000 in separate private prefixes. The Wine command probe
  passed; Proton setup was not requested in the completed live run.
- Internal installation has source checksum, exact typed erase confirmation,
  identity recheck, clone/readback/GPT checks and pristine-DATA expansion.
  Eighteen physical-installer fixtures, eleven growth fixtures and six VM-flow
  fixtures pass. The real installed-guest attempt failed final DATA validation;
  installation completion and detached cold boots remain unverified. No
  Surface internal-disk erase is authorized.

Blocking release acceptance:

- Correct the final DATA verification failure, install a rebuilt image to a
  fresh disposable 64 GiB VM disk, then cold-boot twice after its initial boot
  without installer media. Validate persistence, constrained-memory,
  AHCI and recovery behavior. The completed live shutdown reached poweroff
  but logged busy `/data` and `/run` unmount warnings; filesystem recovery/persistence
  has not been established by that UI poweroff check.
- Actual Surface live boot, Intel acceleration/video decode, Type Cover, touch,
  speakers, WiFi, Bluetooth pairing/reconnect, battery, brightness, microSD and
  suspend/resume are **UNTESTED**. See `SURFACE_HARDWARE_TESTS.md`.
- Live Sunshine streaming and Tailscale enrollment are **BLOCKED** without an
  authorized host/test account; no credentials have been used or stored.
- Independent clean-build reproducibility and legal-info review remain pending.
- The owner Games drive previously disconnected repeatedly through its USB
  bridge. Earlier game evidence remains recorded; fresh owner-game tests need
  a stable connection.

USB delivery: the owner-authorized whole-USB flash completed **PASS** on
November 20. All 6,510,628,864 image bytes were read back and matched the
73242f image SHA-256; image GPT CRCs passed and obsolete end-GPT metadata was
cleared. UDisks power-off then completed successfully. The old H
partitions/files were replaced. This was a normal flash,
not secure full-media erasure; USB DATA remains 4 GiB. The private receipt is
`artifacts/usb-execution/full-flash-1795225100224929858/result.json`.
Exact hardware identity remains only in ignored private plans. This verifies
delivery bytes for the live-tested 73242f image, not Surface boot or a working
internal installer. A corrected image requires another verified flash. No
Surface internal-disk erase is authorized.
