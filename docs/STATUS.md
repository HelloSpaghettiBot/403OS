# Development status

**Development preview; current installer/display acceptance is incomplete.**
The self-contained image pipeline compiles and verifies its embedded payload.
New display settings are being integrated and require a rebuilt image and
runtime checks. No current production-readiness claim is made.

## Artifacts

| File under `output/images/` | Role |
| --- | --- |
| `surface-go.img` | Bootable USB with the installation payload in DATA |
| `surface-go-base.img` | Pristine installation image with empty DATA |
| `SHA256SUMS`, `surface-go-base.img.sha256` | Outer/base checksum records |
| `installer-media.json` | Image identities and payload-readback result |

The USB contains `/data/SurfaceGoGamingOS/surface-go.img` and `.sha256`,
root owned with read-only file permissions. The installer discovers this
source automatically. No extra media or manual staging is required.
The base contains no nested installer; installed DATA is expanded from 4 GiB.

The last completed pair predates the display update; both are 6,510,628,864 bytes:

- USB SHA-256: `517d91b831a276431b304329e2cc632dac4260198f4cf6ba31df0e80ca08f58c`
- Base SHA-256: `9b930ccd1e89288daf7e84302f2ca093731bd6b6baeff513c9f29e2f9f5eceeb`

Build-time embedded-file readback, root ownership and mode 0444 pass.
Guest source discovery/full-payload hashing also ran successfully, but visible
live acceptance failed display/input assertions. The last attempt reached
1800×1200 and timed out waiting for a keyboard action. Complete revised-image
live, embedded-file installation and display trials remain pending.

## Completed evidence

The preceding pristine image
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`
passed guarded 64 GiB installation and DATA growth. Three detached UEFI boots
advanced DATA counts 1–3, including a 3 GiB/two-vCPU/1280×800/AHCI profile.
Unenrolled Tailscale state persisted across repeat boots. Its live run passed
all implemented checks; boot results remain PARTIAL for external services
and physical hardware. See the [VM report](VM_TEST_REPORT.md) for exact runs.

Verified scope includes graphical HOME, virtual input/framebuffer changes,
DHCP/DNS/HTTPS, Bluetooth daemon, Cog rendering/HTTPS/close and HOME return,
eleven emulator probes, MIT NES input/SRAM reload, Wine Windows command exit,
Moonlight help/version, Tailscale daemon and non-silent captured PCM.
These tests do not establish every game's compatibility or Surface performance.

The recorded source suite passes 210 Python tests, five native C++ executables
and shell syntax checks. New display checks are still in progress. Evidence:
`artifacts/tests/self-contained-python.log` and `artifacts/tests/final-complete.log`.

## Remaining work

- Rebuild and validate detected display modes, Apply/Keep, 45-second timeout
  rollback, layout/input after restart and persisted settings after boot.
- Complete matching self-contained live, embedded-payload installation and
  repeat installed-boot checks.
- Test actual Surface graphics, Type Cover, touch, speakers, Wi-Fi/Bluetooth,
  battery, brightness, microSD and suspend/resume.
- Test interrupted-boot recovery, authorized streaming/enrollment, explicit
  Proton setup and PokeMMO/DW3 login/gameplay.
- Review legal-info and demonstrate an independent clean build.

The source is available on [GitHub](https://github.com/HelloSpaghettiBot/403OS).
Images and private test artifacts are not included in the repository. There
is no finished binary release. Physical hardware remains **UNTESTED**.
