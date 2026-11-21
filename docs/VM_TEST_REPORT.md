# VM test report

## Current compiled artifacts

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `999818d5a11e549b8b289620367cac69476ba8437c5f318566f3f2b58f3f9647` |
| Pristine installation payload | `600dfc4a2b1fd6fde6a1adfe54ccd5fb53aa74324ea3f891f00ffa72b644acaf` |

Both are 6,510,628,864 logical bytes. Packaging passed complete embedded
payload readback, root ownership, mode 0444 and absence of a recursive payload.
The current runtime runs used a local test-media variant with supplied library
data, the same compiled root filesystem and the public base payload above.
They do not establish a byte-identical boot of the public outer image.

## Live boot and GUI installation

`20261121-110730-live` used QEMU 11.1.3, OVMF/KVM, 4 GiB RAM, four virtual CPUs
and an 1800×1200 framebuffer. It booted through UEFI with no direct kernel
boot. All implemented checks passed; there were no FAIL checks.

- Root's first-run password enrollment and confirmation passed through HOME.
- Supplied local games and cover textures loaded into HOME. Current discovery,
  title-input and Bluetooth recovery paths also have focused source fixtures;
  this run does not establish physical external-drive or Bluetooth pairing.
- The native Weston module rotated the real WebKit viewport from 1800×1200
  to 1200×1800 and restored it. Browser and compositor PID/start-time identity
  and saved display state stayed unchanged. This supplied no accelerometer
  readings and does not establish physical sensor behavior.
- The production GUI installer reviewed a unique disposable 64 GiB SATA disk,
  with Cancel selected by default. Explicit installation passed source
  verification, complete copy/readback, GPT checks and fresh DATA expansion to
  66,503,815,168 bytes. Local live-DATA game files and root authentication were
  copied and verified before final GUI power-off. No host block device or
  physical Surface disk was used.
- UEFI graphical HOME, root/DATA mounts, keyboard/framebuffer changes, guest
  keyboard/gamepad/touch injection, DHCP/DNS/HTTPS, Bluetooth daemon, rendered
  browser/JavaScript/Ctrl+W and browser launch/global return passed.
- All eleven emulator runtime probes, NES input/SRAM reload, Wine Windows
  command exit, Moonlight binary startup and Tailscale daemon/policy routing
  passed. Captured stereo PCM was non-silent (peak 2987, 41,711 signal frames).

Overall status is PARTIAL: Surface hardware is UNTESTED, interrupted-boot
recovery and generic reboot are NOT_RUN, enrolled streaming is BLOCKED and
first-live-boot Tailscale state persistence is NOT_VERIFIED.

## Detached installed acceptance

`20261121-112520-test` booted the newly installed SATA disk through UEFI,
without installation media or a direct kernel boot, at 1800×1200. It passed
login with the existing root password and loaded the migrated game library
and covers. All implemented app/emulator, HOME input/return, network, rotation,
audio and clean-power-off checks passed; there were no FAIL checks. Non-silent
audio had peak 2987 and 41,647 signal frames. Overall status remained PARTIAL
for the same hardware/recovery/enrollment limits above.

This is one detached installed boot. Current-image display Apply/Keep, timed
rollback, additional cold boots and a fresh secondary-profile acceptance run
remain separate work.

Earlier run `20261121-084511-live` created and authenticated a secondary user;
its browser reported real/effective/saved UID/GID 2000, then returned globally
to HOME and switched back to root. It applies to the earlier artifact.

Historical runs `20261120-223547-test` and `20261120-224052-test` passed GUI
Apply/Keep at 1280×960, 45-second rollback, restoration to 1800×1200 and a
separate cold boot retaining that mode, DATA boot count and Tailscale state.
Those results apply to the prior artifact.

## Source checks and limits

The recorded suite covers 315 Python tests (288 main and 27 application
fixtures) and five rebuilt native unit test executables, recorded in
`artifacts/tests/seamless-final.log`. Compiled kernel and
BlueZ configuration gates pass. The kernel includes Surface Go LPSS/I2C/GPIO
and HID sensor/IIO paths plus broad upstream gamepad drivers; BlueZ includes
classic HID and LE HOG. SDL mappings include the pinned Ipega entry and the
custom mapping wizard has native fixture coverage.

Automatic discovery keeps the prior catalog after inventory/scan failures;
fixtures cover read-only mount safeguards, restrictive startup permissions,
per-file deadlines and title input. Migration fixtures cover unchanged-source
manifests, fresh destinations, file readback, ownership and password hashes.
Bluetooth recovery fixtures keep hardware blocks and unrelated radios intact.

Actual Surface touchscreen operation, accelerometer axes, orientation/touch
coordinates, controllers, radios, speakers, battery, suspend and installation
remain unverified. The previously reported touch failure needs a physical
retest; the reported physical Bluetooth failure remains unconfirmed and
pairing/reconnect has not passed hardware acceptance. Earlier Intel graphics
initialization and ELAN registration are detection evidence only. No universal
controller-compatibility or production-release pass is
claimed. Failed earlier harness runs remain in ignored local artifacts.
