# VM test report

## Current click-through installer

Public outer image SHA-256:
`5c374e5e6de71d2f2153611ce7e9336a67b152f2c8575ebb8acb4396ff0c5b99`.
Pristine installation payload SHA-256:
`55caaa5b0f45e6ec56d74c47cc0c78cf189d20e9ad33ffaab5a71c7f1f09f7ba`.
Both are 6,510,628,864 logical bytes. Embedded full-file readback and
root-owned read-only payload metadata passed at build time.

`20261120-221932-live` used OVMF/KVM, 4 GiB RAM, four CPUs and an 1800×1200
framebuffer. It exercised the production GUI installer against a newly
created 64 GiB SATA disk, including the read-only disk review, Cancel default,
explicit Install action, source verification, complete copy/readback,
partition validation, pristine DATA growth and final UI shutdown. The
`click_installer_copy_readback_data_growth` check is PASS. No host block device
or physical Surface storage was involved.

All implemented live checks passed: UEFI graphical HOME, root/DATA mounts,
keyboard/framebuffer changes, guest keyboard/gamepad/touch injection,
DHCP/DNS/HTTPS, Bluetooth daemon, rendered browser/JavaScript/Ctrl+W,
browser HOME launch/global return, eleven engine probes, NES input/SRAM
reload, Wine Windows command exit, Moonlight binary startup, Tailscale daemon
and policy routing, and non-silent PCM output. Overall status is PARTIAL for
hardware, recovery, first-boot Tailscale persistence and enrolled streaming.

The first GUI harness run `20261120-221727-live` stopped during read-only
review because its serial assertion did not account for ATA WWID formatting.
No target install started in that failed run. The corrected harness accepts
only the uniquely serialized disposable target and the successful rerun is
listed above. Historical failures remain in artifacts.

## Display and physical limits

The display service has ten passing fixtures for mode discovery, confirmed
persistence, timed rollback, safe fallback and session restart.

Installed SATA run `20261120-223547-test` booted through UEFI with 4 GiB RAM
and four CPUs, without installer media or a direct kernel boot. Actual GUI
mode discovery and Apply/Keep at 1280×960 passed, an unconfirmed 45-second
trial rolled back, and restoration to 1800×1200 passed. The combined
`display_apply_confirm_timeout_rollback` check is PASS. Display persistence
was then checked in a separate cold boot below.

All implemented checks in that installed run passed, including Tailscale
state persistence, application/runtime checks, audio and clean UI shutdown;
there were no FAIL checks. Overall status remains PARTIAL: hardware is
UNTESTED, interrupted-boot recovery is NOT_RUN, and enrolled streaming is
BLOCKED.

## Cold-boot display persistence

`20261120-224052-test` cold-booted the same installed SATA disk through UEFI,
without installer media or a direct kernel boot. The saved `Virtual-1`
setting was 1800×1200, the active mode and framebuffer both matched, and no
trial was pending. DATA boot count advanced from two to three and the
Tailscale state checksum remained identical. These explicit assertions
passed; there were no FAIL checks and overall status remained PARTIAL.

This verifies saved display-mode persistence in the installed VM. The
harness's generic `reboot` and `interrupted_boot_recovery` checks remain
NOT_RUN; Surface hardware remains UNTESTED.

Surface touchscreen input is reported nonfunctional. Automatic orientation,
password-protected accounts and Ipega controller validation remain work.
Physical graphics, radios, speakers, battery and suspend are not established
by VM results. No Surface hardware/install pass is claimed.

All 222 Python tests pass; changed native C++ controls/parser checks pass with
`-Werror`.
