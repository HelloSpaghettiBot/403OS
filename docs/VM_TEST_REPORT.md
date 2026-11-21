# VM test report

## Current compiled artifacts

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `8fbfb8494ae5b66896f76690fd239fcbdc1cb6abe434b1e137a45ccb2b5d91f2` |
| Pristine installation payload | `664d6c92e2132c2457696ad626c46c08167c2a4b7366ad1626a69ba95eccb7c1` |

Both are 6,510,628,864 logical bytes. Packaging passed complete embedded
payload readback, root ownership, mode 0444 and absence of a recursive payload.

## Live boot and GUI installation

`20261121-084511-live` used QEMU 11.1.3, OVMF/KVM, 4 GiB RAM, four virtual CPUs
and an 1800×1200 framebuffer. It booted through UEFI with no direct kernel
boot. All implemented checks passed; there were no FAIL checks.

- Root's first-run password enrollment and confirmation passed through HOME.
- A secondary user was created and authenticated. Its actual browser process
  reported real, effective and saved UID/GID 2000. Browser HTTPS load, global
  return to HOME, logout and switching back to root passed.
- The native Weston module rotated the real WebKit viewport from 1800×1200
  to 1200×1800 and restored it. Browser and compositor PID/start-time identity
  and saved display state stayed unchanged. This supplied no accelerometer
  readings and does not establish physical sensor behavior.
- The production GUI installer reviewed a unique disposable 64 GiB SATA disk,
  with Cancel selected by default. Explicit installation passed source
  verification, complete copy/readback, GPT checks, fresh DATA expansion and
  final GUI power-off. No host block device or physical Surface disk was used.
- UEFI graphical HOME, root/DATA mounts, keyboard/framebuffer changes, guest
  keyboard/gamepad/touch injection, DHCP/DNS/HTTPS, Bluetooth daemon, rendered
  browser/JavaScript/Ctrl+W and browser launch/global return passed.
- All eleven emulator runtime probes, NES input/SRAM reload, Wine Windows
  command exit, Moonlight binary startup and Tailscale daemon/policy routing
  passed. Captured stereo PCM was non-silent (peak 2987, 41,635 signal frames).

Overall status is PARTIAL: Surface hardware is UNTESTED, interrupted-boot
recovery and generic reboot are NOT_RUN, enrolled streaming is BLOCKED and
first-live-boot Tailscale state persistence is NOT_VERIFIED.

## Detached installed acceptance

The newly installed SATA disk is being tested without installation media.
Current-image display Apply/Keep, timed rollback and cold-boot persistence
results will be recorded here after completion.

Historical runs `20261120-223547-test` and `20261120-224052-test` passed GUI
Apply/Keep at 1280×960, 45-second rollback, restoration to 1800×1200 and a
separate cold boot retaining that mode, DATA boot count and Tailscale state.
Those results apply to the prior artifact.

## Source checks and limits

The recorded suite covers 280 Python tests (253 main and 27 application
fixtures) and seven rebuilt native test executables. Compiled kernel and
BlueZ configuration gates pass. The kernel includes Surface Go LPSS/I2C/GPIO
and HID sensor/IIO paths plus broad upstream gamepad drivers; BlueZ includes
classic HID and LE HOG. SDL mappings include the pinned Ipega entry and the
custom mapping wizard has native fixture coverage.

Actual Surface touchscreen operation, accelerometer axes, orientation/touch
coordinates, controllers, radios, speakers, battery, suspend and installation
remain unverified. The previously reported touch failure needs a physical
retest. No universal controller-compatibility or production-release pass is
claimed. Failed earlier harness runs remain in ignored local artifacts.
