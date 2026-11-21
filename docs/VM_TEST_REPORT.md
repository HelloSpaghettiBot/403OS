# VM validation report

**Current scope: development validation, not hardware certification.**
The current self-contained pipeline has build-time payload verification, but
its complete live/install/display acceptance is pending. A preceding pristine
image has completed installation and repeated detached UEFI boots.

## Current self-contained artifacts

The USB image embeds the pristine base at
`/data/SurfaceGoGamingOS/surface-go.img` with a checksum sidecar. The installer
uses source discovery and verifies the file before copying it. The VM path
mounts serialized media DATA with `ro,noload,nosuid,nodev,noexec`; it does not
fall back to cloning the outer image when the payload is absent.

The last completed build, before new display settings, produced two
6,510,628,864-byte artifacts:

| Artifact | SHA-256 |
| --- | --- |
| USB | `517d91b831a276431b304329e2cc632dac4260198f4cf6ba31df0e80ca08f58c` |
| Pristine base | `9b930ccd1e89288daf7e84302f2ca093731bd6b6baeff513c9f29e2f9f5eceeb` |

The build verified the embedded full-file SHA and root-owned mode 0444.
Guest source discovery/full hashing and application checks have serial
evidence. Complete host acceptance did not pass: visible runs first captured
smaller display modes; [201812](../artifacts/vm-tests/20261120-201812-live/result.json)
confirmed 1800×1200 but timed out on the expected keyboard acknowledgment.
No complete acceptance pass is inferred from those partial observations.

The new display flow lists actual DRM modes, offers preferred/native,
restarts compositor/HOME for a 45-second trial, reverts on timeout and persists
only after Keep. Rebuild, Apply/Keep/rollback and reboot-persistence tests are
pending; earlier image hashes do not identify that revision.

## Completed baseline

Pristine image SHA-256:
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`.

| Run | Configuration | Result |
| --- | --- | --- |
| [174857 install](../artifacts/vm-tests/20261120-174857-install/result.json) | 64 GiB disposable disk | PASS: clone/readback/GPT/DATA growth |
| [175146 boot](../artifacts/vm-tests/20261120-175146-test/result.json) | 4 GiB/four vCPUs/1800×1200/virtio | PARTIAL; implemented checks PASS, DATA count 1 |
| [175257 boot](../artifacts/vm-tests/20261120-175257-test/result.json) | 3 GiB/two vCPUs/1280×800/AHCI | PARTIAL; implemented checks and persistence PASS, count 2 |
| [175359 boot](../artifacts/vm-tests/20261120-175359-test/result.json) | Default profile, same disk | PARTIAL; implemented checks and persistence PASS, count 3 |
| [175512 live](../artifacts/vm-tests/20261120-175512-live/result.json) | 4 GiB/four vCPUs/1800×1200 | PARTIAL; implemented live checks PASS |

Tests used QEMU 11.1.3/KVM and unsigned OVMF. Provisioning uses the guarded
VM-only direct kernel; acceptance boots use UEFI, and installed boots detach
the media. DATA expanded from 4 GiB to 66,503,815,168 filesystem bytes while
preserving identity and EFI/root payloads. This baseline predates embedded-file
installation and cannot validate the new media path.

Implemented runtime checks cover graphical HOME, mounts, SDL/QMP virtual
input and framebuffer focus, DHCP/DNS/HTTPS, Bluetooth daemon, Cog render/
JavaScript/CA-validated HTTPS/Ctrl+W, HOME launch/global return, eleven engine
probes, legal NES input/SRAM reload, Wine command exit, Moonlight help/version,
Tailscale runtime/state persistence, non-silent PCM and UI poweroff.
Tailscale remained unenrolled; no pairing, tailnet or streaming claim follows.

The 175512 recording contains 42,714 frames at 44,100 Hz (0.968571 seconds),
peak 2,987 and 41,803 non-silent frames. Raw SHA-256:
`f420cb449e21b14be193554f47bad2947f7620ba1e77ac9af04200c9e973f261`.
QEMU's unset header sizes are validated without changing the recording.
HOME RSS was 128,012 kB; 3,762,720 kB available RAM includes reclaimable cache,
not total OS RSS. SDL3 Wayland/GLES2 with Mesa software rendering does not
establish Intel acceleration or physical audio/input.

## Reproduce

```sh
scripts/vm-create.sh
scripts/vm-install.sh --disk artifacts/vm-tests/example-installed.qcow2
scripts/vm-boot-test.sh --disk artifacts/vm-tests/example-installed.qcow2
scripts/vm-boot-test.sh --disk artifacts/vm-tests/example-installed.qcow2 --memory 3072 --cpus 2 --sata --width 1280 --height 800
scripts/vm-boot-test.sh --disk artifacts/vm-tests/example-installed.qcow2
```

Use `--visible` for an interactive window and `--ovmf /path/to/OVMF_CODE.fd`
for a different firmware location. Logs, screenshots, audio, commands and
results stay under `artifacts/vm-tests/`; they are excluded from source Git.
A running QEMU process or screenshot alone is not a pass.

The recorded source suite passes 210 Python tests, five native C++ executables
and shell syntax. Current display tests and full revised-image runs remain
pending. Physical Surface hardware, interrupted-boot recovery, Proton setup,
online-game gameplay and enrolled streaming are unverified. See
[SURFACE_HARDWARE_TESTS.md](SURFACE_HARDWARE_TESTS.md).
