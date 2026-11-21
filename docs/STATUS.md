# Status — November 20, 2026

**Development build; self-contained images compiled, live acceptance incomplete.**
The new pipeline built an outer live USB and a separate pristine payload;
full embedded-file readback and ownership checks pass. Each image is
6,510,628,864 bytes. The last live attempt, `20261120-201812-live`, confirmed
1800×1200 but failed waiting for the expected keyboard action acknowledgment.
Visible keyboard interference is a possible cause, not an established diagnosis.
Earlier visible attempts captured lower modes than requested. No complete
self-contained live/install pass or corrected USB flash is claimed. Build log:
`artifacts/build/self-contained-usb-image.log`.

The owner subsequently requested installed-OS display resolution controls
before USB replacement. The new flow lists connected DRM output modes, offers
native/preferred, restarts compositor/HOME for a 45-second trial, automatically
restores the previous mode on timeout, and persists only after **Keep**.
Its implementation/rebuild and actual apply/Keep/rollback/cold-boot tests are
in progress. Earlier image hashes below precede this new feature.

| Artifact under `output/images/` | Role |
| --- | --- |
| `surface-go.img` | Outer bootable USB with embedded installer in its 4 GiB DATA |
| `surface-go-base.img` | Pristine installation image with empty 4 GiB DATA |
| `surface-go-base.img.sha256`, `SHA256SUMS` | Separate payload/outer checksums |
| `installer-media.json` | Both image identities and embedded readback evidence |

The sparse base is embedded as `/data/SurfaceGoGamingOS/surface-go.img` and
`.sha256`, root owned and read only. The real installer discovers it on `/data`;
no additional source media or manual staging is required. The inner base has
no nested installer. The guarded VM installation path now mounts serialized
outer-media DATA read only and clones the embedded file, not the outer image.
The failed visible-run serial log does contain successful guest source
discovery/full-payload SHA and application checks. A complete host live
acceptance result is still required before delivery.

Last completed packaging SHA-256 values, before the display-settings rebuild:

- Outer `surface-go.img`: `517d91b831a276431b304329e2cc632dac4260198f4cf6ba31df0e80ca08f58c`
- Pristine `surface-go-base.img`: `9b930ccd1e89288daf7e84302f2ca093731bd6b6baeff513c9f29e2f9f5eceeb`

Completed evidence for the preceding pristine image SHA-256
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`:

| Run | Result |
| --- | --- |
| `20261120-174857-install` | PASS: 64 GiB clone/readback/GPT and pristine DATA growth |
| `20261120-175146-test` | PARTIAL; all implemented checks PASS, detached UEFI, DATA count 1 |
| `20261120-175257-test` | PARTIAL; all implemented checks PASS, count 2, 3 GiB/two CPUs/AHCI/1280×800 |
| `20261120-175359-test` | PARTIAL; all implemented checks PASS, count 3, default profile |
| `20261120-175512-live` | PARTIAL; all implemented live checks PASS |

DATA grew from 4 GiB to 66,503,815,168 filesystem bytes while preserving its
identity and EFI/root payloads. Repeat boots verified unchanged unenrolled
Tailscale state. These tests establish the prior image's VM behavior, not
validation of the newly embedded-payload media path.

Verified runtime scope includes UEFI HOME, root/DATA mounts, actual framebuffer
focus changes, guest SDL/QMP input injection, DHCP/DNS/HTTPS, Bluetooth daemon,
Cog HTTPS/render/Ctrl+W and HOME/global-return flow, eleven engine/core probes,
real MIT NES input/SRAM reload, unprivileged Wine Windows command exit,
Moonlight help/version, Tailscale daemon and non-silent captured audio.
They do not establish physical controllers/radios, game compatibility across
all systems, streaming, online-client gameplay or Surface performance.
SDL3 uses Wayland/GLES2 with Mesa software rendering in the VM; Intel
acceleration is packaged but not verified on hardware.

Current source verification: **210 Python tests PASS** in
`artifacts/tests/self-contained-python.log`; five native C++ executables and
shell syntax checks previously pass. The image pipeline fixtures prove sparse
large-file import, root ownership/modes, pristine nonrecursive DATA and guarded
source discovery. Source tests do not replace actual new-image acceptance.
Builds use ten workers, 24 GiB throttle, 32 GiB hard cap/container cap and
1 GiB swap; VM host limit remains 6 GiB with its existing guest profiles.

The image packages eleven emulator engines, Cog/WPE, Moonlight, Tailscale,
Wine64/UMU and Intel graphics/firmware support. PokeMMO setup and DW3 import
are implemented, but target login/gameplay and explicit Proton setup remain
untested. No ROMs, BIOS, proprietary clients, accounts or keys ship.

Remaining gates are display-settings build/apply/rollback/persistence checks,
the complete self-contained live source-discovery/install path and
verified USB replacement; actual Surface boot/input/graphics/audio/radios/power;
interrupted-boot recovery; authorized streaming/enrollment; and legal-info/
clean-build reproducibility. Physical Surface behavior is **UNTESTED**.
No Surface internal-disk erase has been authorized.

An earlier 73242f image was flashed to the authorized external H USB and its
complete image readback/GPT verification/power-off passed. Its old H files were
replaced. That delivery lacks the new embedded payload and must be replaced
after the new artifact passes validation. Normal flashing is not secure
full-media erasure; USB DATA remains 4 GiB. Private identity/receipts stay in
ignored `artifacts/usb-execution/`.

Initial source commit `6e4f165` is published at
[HelloSpaghettiBot/403OS](https://github.com/HelloSpaghettiBot/403OS).
This is source publication, not a binary release. A source follow-up with the
self-contained pipeline and completed new evidence is pending.
