# Status — November 20, 2026

403 OS remains a development preview. The current public image compiled with
click-through installation and persistent display-resolution controls.

| Artifact | SHA-256 |
| --- | --- |
| Public live USB | `5c374e5e6de71d2f2153611ce7e9336a67b152f2c8575ebb8acb4396ff0c5b99` |
| Pristine internal-install image | `55caaa5b0f45e6ec56d74c47cc0c78cf189d20e9ad33ffaab5a71c7f1f09f7ba` |

The live image discovers its embedded pristine installer automatically. The
GUI now provides disk selection, an erase review with Cancel selected by
default, a single explicit Install action, four progress stages, verified
completion and power-off. It retains backend hardware identity, full-source
checksum, exclusive target, complete readback and DATA-expansion checks.

The `20261120-221932-live` run exercised this actual GUI against a disposable
64 GiB SATA disk. Copy/readback/DATA growth and clean shutdown passed. All
implemented live checks passed; overall status is PARTIAL because physical
hardware, recovery and external streaming services are outside that evidence.

All **222 Python tests** pass in `artifacts/tests/click-installer-python.log`.
The changed native control-center/parser checks also pass with `-Werror`.
Eleven emulator runtime probes, NES input/SRAM, browser/HOME return, Wine,
Tailscale daemon, network and non-silent PCM checks pass in the live VM.

Settings → Display resolution discovers connected DRM modes. Apply starts a
45-second trial; Keep persists the choice, and timeout restores the previous
mode. Ten focused backend fixtures pass. Installed SATA run
`20261120-223547-test` passed actual GUI mode selection, Apply/Keep at
1280×960, timeout rollback and restoration to 1800×1200. It booted through
UEFI without installer media; all implemented checks passed, with overall
status PARTIAL. A separate cold boot, `20261120-224052-test`, verified the
saved 1800×1200 mode, matching active mode/framebuffer and no pending trial.
Display persistence is PASS in this installed VM; Surface display hardware
remains untested.

Reported Surface issues include nonworking touchscreen input and missing
automatic rotation. Password-protected profiles and Ipega-specific controller
validation remain pending. VM input tests do not resolve these hardware issues.
Proton setup, online-game login/gameplay and enrolled streaming remain separate
acceptance work. No production-readiness or physical-device installation pass
is claimed.

Public source and base images contain no game files, credentials or private
device records. See the [VM report](VM_TEST_REPORT.md),
[installation guide](INSTALL_RECOVERY.md) and [roadmap](ROADMAP.md).
