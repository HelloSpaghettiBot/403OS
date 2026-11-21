# Status — November 21, 2026

403 OS remains a development preview. The current image compiles with the
GUI installer, password-protected profiles, expanded controller drivers and
native output rotation.

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `8fbfb8494ae5b66896f76690fd239fcbdc1cb6abe434b1e137a45ccb2b5d91f2` |
| Pristine internal-install image | `664d6c92e2132c2457696ad626c46c08167c2a4b7366ad1626a69ba95eccb7c1` |

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
  readback, GPT and fresh DATA expansion before offering power-off.

The recorded suite covers **280 Python tests** (253 main and 27 application
fixtures) and **seven native test executables**. The 19 profile fixtures
include password hashing, login refusal, identity separation, socket
permissions and first-boot directory permissions. Compiled kernel and BlueZ
configuration checks passed.

## Current artifact validation

Live run `20261121-084511-live` passed every implemented check, including
root enrollment, a secondary user's browser with all real/effective/saved
UIDs and GIDs at 2000, global return and switching back to root. Actual browser
geometry rotated from 1800×1200 to 1200×1800 and back with unchanged browser
and compositor processes and unchanged resolution settings.

The same run completed GUI installation into a disposable 64 GiB SATA disk,
including full copy/readback, GPT checks, DATA growth and clean power-off.
Detached installed-boot and display tests are in progress. Overall status is
PARTIAL for untested hardware, recovery and enrolled streaming. See the
[VM report](VM_TEST_REPORT.md).

Historical November 20 VM runs passed GUI installation into a disposable
64 GiB SATA disk, detached UEFI boot, display Apply/Keep, 45-second rollback
and cold-boot resolution/DATA/Tailscale persistence. Those results do not
establish the additions in this image.

## Physical and release limits

The reported Surface touch failure needs a physical retest with the new
kernel. Accelerometer axes, four orientations, touch coordinates, controllers,
radios, audio, battery and suspend remain physically unverified. VM controller
injection does not establish compatibility with every real controller.

Proton setup, online-game login/gameplay and enrolled streaming remain
separate acceptance work. No production-readiness or physical-device
installation pass is claimed. Public source and base images contain no game
files, credentials or private device records.

See [installation](INSTALL_RECOVERY.md), [roadmap](ROADMAP.md) and
[test results](VM_TEST_REPORT.md).
