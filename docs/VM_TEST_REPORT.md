# VM test report

**Self-contained packaging compiled; full live acceptance and display-settings rebuild pending.**
The outer `surface-go.img` now carries a sparse pristine `surface-go-base.img`
inside its DATA as `SurfaceGoGamingOS/surface-go.img` plus `.sha256`.
Checksums for the outer and payload are distinct. Build outputs include
`SHA256SUMS`, `surface-go-base.img.sha256` and `installer-media.json`.
The last completed pair, before display-settings changes, is
6,510,628,864 bytes per image. The build verified the embedded file
checksum and root-owned 0444 mode; no runtime pass is inferred from that check.

- Outer SHA-256: `517d91b831a276431b304329e2cc632dac4260198f4cf6ba31df0e80ca08f58c`
- Pristine payload SHA-256: `9b930ccd1e89288daf7e84302f2ca093731bd6b6baeff513c9f29e2f9f5eceeb`

The physical finder discovers `/data/SurfaceGoGamingOS/surface-go.img`.
The new VM installer mounts the serialized outer media's DATA with
`ro,noload,nosuid,nodev,noexec`, verifies the embedded sidecar/full SHA, clones
the pristine file and verifies target readback before DATA expansion.
It refuses a missing payload; it does not fall back to cloning the outer image.
A live acceptance check also exercises actual source discovery/full hashing.
The latest visible run,
[`201812`](../artifacts/vm-tests/20261120-201812-live/result.json), reached
1800×1200 and passed UEFI/root/SDL injection before failing its wait for the
expected keyboard action. Visible keyboard interference is a possible cause;
it has not been proven. Earlier visible runs failed display-size assertions.
Serial evidence includes guest source discovery/full-payload verification and
application checks, but the host did not complete remaining live assertions.
None of these runs is counted as a full media acceptance pass.

A new OS display-settings flow is now being integrated. It uses actual DRM
mode discovery with a native/preferred choice; Apply restarts compositor/HOME,
a 45-second trial reverts automatically unless kept, and Keep persists the
selection. The revised image must test mode listing, apply, Keep, timeout
rollback, responsive input/layout and reboot persistence. New image hashes,
complete live/embedded-file installation and USB reflash remain pending.

The preceding pristine image SHA-256
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`
has completed this evidence:

| Run | Configuration | Recorded result |
| --- | --- | --- |
| [174857 install](../artifacts/vm-tests/20261120-174857-install/result.json) | 64 GiB disposable target | PASS: guarded clone, readback, GPT and DATA growth |
| [175146 boot](../artifacts/vm-tests/20261120-175146-test/result.json) | 4 GiB/four CPUs/1800×1200/virtio | PARTIAL, all implemented checks PASS; DATA count 1 |
| [175257 boot](../artifacts/vm-tests/20261120-175257-test/result.json) | 3 GiB/two CPUs/1280×800/AHCI | PARTIAL, all implemented checks PASS; count 2; Tailscale persistence PASS |
| [175359 boot](../artifacts/vm-tests/20261120-175359-test/result.json) | Default profile, same installed disk | PARTIAL, all implemented checks PASS; count 3; Tailscale persistence PASS |
| [175512 live](../artifacts/vm-tests/20261120-175512-live/result.json) | 4 GiB/four CPUs/1800×1200 | PARTIAL, all implemented live checks PASS |

The installation grew DATA from 4 GiB to 66,503,815,168 filesystem bytes with
identity and EFI/root payloads preserved. Provisioning used the guarded
VM-only direct kernel; all installed boots used UEFI without installer media
or direct-kernel boot. The installed disk was
`artifacts/vm-tests/corrected-verified-install.qcow2`. This earlier test path
predates the outer media's embedded-file cloning and cannot validate it.

All completed runtime suites cover graphical HOME, root/DATA, framebuffer
focus changes, virtual keyboard/gamepad/touch injection, DHCP/DNS/HTTPS,
Bluetooth daemon, Cog rendering/JavaScript/CA-validated HTTPS/Ctrl+W,
HOME dock launch/global keyboard return, eleven emulator runtime probes,
MIT NES input/SRAM reload, Wine Windows command exit, Moonlight help/version,
Tailscale daemon/policy routing, non-silent PCM and UI shutdown.
Repeat boots retain the same SHA of unenrolled Tailscale state; they do not
establish enrollment, pairing or streaming.

The 175512 live recording contains 42,714 frames at 44,100 Hz, 0.968571 seconds,
peak 2,987 and 41,803 non-silent frames. Its raw SHA-256 is
`f420cb449e21b14be193554f47bad2947f7620ba1e77ac9af04200c9e973f261`.
The verifier validates QEMU's unset size-header form without changing bytes.
This is virtual output, not physical speakers. The live HOME RSS was
128,012 kB; 3,762,720 kB available RAM includes reclaimable cache and is not
whole-OS RSS. SDL3 Wayland/GLES2 and Mesa software rendering do not establish
Intel acceleration.

The current source passes **210 Python tests** in
`artifacts/tests/self-contained-python.log`. Five native C++ executables and
shell syntax checks also pass. Sparse-file/ownership/source guards use fixtures;
actual new-image results are required separately. Historical failures remain
in artifacts without rewriting; the current report lists final relevant scope.

Interrupted-boot recovery, actual Surface hardware, explicit Proton setup,
DW3/PokeMMO login/gameplay and enrolled Sunshine/Tailscale streaming remain
unverified. Boot results are PARTIAL for this scope. No Surface internal erase
has been authorized.

The earlier authorized USB flash verified a different, non-self-contained
73242f image. Its private readback/GPT/power-off receipt does not establish new
media delivery or physical Surface boot. A new outer image must pass matching
live/payload checks and verified reflash. Initial source commit `6e4f165` is
published on [GitHub](https://github.com/HelloSpaghettiBot/403OS); there is no
binary release, and private images/test artifacts are excluded.
