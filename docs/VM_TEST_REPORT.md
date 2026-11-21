# VM test report

November 20, 2026: **PARTIAL overall; all implemented live checks PASS**.
`20261120-173635-live` completed with no failed checks. Installed-disk
persistence, recovery, physical Surface hardware and enrolled streaming remain
outside this completed live result.

Tested image: `output/images/surface-go.img`, 6,510,628,864 bytes, SHA-256
`73242f0c64f9a00447805c32ff3ced643619f8d5b357e6365c5e3f2a8008e635`.
Build log: `artifacts/build/tailscale-idempotent-image.log`.

- [Structured result](../artifacts/vm-tests/20261120-173635-live/result.json)
- [Serial log](../artifacts/vm-tests/20261120-173635-live/serial.log)
- [HOME screenshot](../artifacts/vm-tests/20261120-173635-live/home.png)
- [Browser launched from HOME](../artifacts/vm-tests/20261120-173635-live/browser-from-home.png)
- [HOME after browser return](../artifacts/vm-tests/20261120-173635-live/home-after-browser.png)
- [Original audio recording](../artifacts/vm-tests/20261120-173635-live/audio.wav)
- [Exact QEMU command](../artifacts/vm-tests/20261120-173635-live/command.json)

The run used QEMU 11.1.3/KVM, four vCPUs, 4096 MiB RAM, virtio storage,
an 1800×1200 display and unsigned OVMF. It booted through UEFI without a
direct kernel, from a disposable live qcow2 backed by the image. This was
not an internal installation or detached installed-disk cold boot. No owner
game-library share was attached. SDL3 used Wayland/GLES2 with software
rendering; earlier VM renderer identification was Mesa softpipe. Intel
acceleration remains unverified.

| Check | Result |
| --- | --- |
| UEFI graphical HOME; root and separate DATA mounts | PASS |
| QMP keyboard focus/framebuffer and SDL virtual gamepad/touch injection | PASS |
| DHCP/DNS/HTTPS and Bluetooth daemon startup | PASS |
| Cog rendering/JavaScript/CA-validated HTTPS and Ctrl+W exit | PASS |
| Actual HOME dock browser launch/global Ctrl+Alt+Home return | PASS |
| Eleven emulator binary/core probes; real NES input/SRAM reload | PASS |
| Moonlight help/version process launch/exit | PASS; stream/pairing not established |
| Unprivileged Wine64 Windows `cmd.exe` launch/exit | PASS |
| Tailscale daemon and temporary IPv4/IPv6 policy-routing runtime | PASS; unenrolled |
| Non-silent captured audio | PASS |
| UI poweroff and QEMU exit | PASS |
| Tailscale state across cold boots | NOT_VERIFIED: first DATA boot |
| Reboot/interrupted-boot recovery | NOT_RUN |
| 64 GiB installation/final DATA validation | FAIL in 173902; details below |
| Detached installed cold boots/persistence | NOT_RUN |
| 3 GiB/2-vCPU and AHCI/alternate-resolution profiles | NOT_RUN |
| Proton runtime setup | NOT_REQUESTED |
| Physical Surface/controllers/touch/speakers/Bluetooth pairing | UNTESTED |
| Sunshine stream/tailnet enrollment | BLOCKED: prerequisites unavailable |

Audio acceptance required both successful guest ALSA playback and at least
0.25 seconds of non-silent captured PCM. The final recording contains:

| Measurement | Result |
| --- | --- |
| Format | 44,100 Hz, 16-bit stereo PCM |
| Frames/duration | 42,523 / 0.96424 seconds |
| Peak/non-silent frames | 2,987 / 41,617 |
| Raw SHA-256 | `90a7b7994d925339a936c1333f8637afe0fce70a617271bbae8db850c3e9f227` |
| Header | Validated QEMU zero-size RIFF/data form; original bytes retained |

Earlier runs exposed silent/missing-mixer audio and a duplicate Tailscale
start race before the local socket appeared. Mixer initialization, target
audio-tool presence checks and idempotent daemon startup are included in the
final tested image. Historical failures/recordings remain retained; the final
pass does not rewrite them or establish physical speakers/network enrollment.

The final guest reported 4,026,832 kB total RAM, 3,754,280 kB available RAM
and 127,876 kB HOME process RSS after idle. Available memory includes reclaimable
cache and is not total OS RSS. SDL/QMP injection does not establish physical
input, and a running Bluetooth daemon does not establish a working radio.
Serial warnings, including busy `/data`/`/run` unmounts, require follow-up in
installed filesystem recovery/persistence tests despite completed UI poweroff.

The corrected-source Python suite passed all 197 tests in
`artifacts/tests/final-corrected-python.log`. Five native C++ executables and
shell syntax checks also passed, with full-script evidence retained in
`artifacts/tests/final-complete.log`. Source tests remain distinct from guest
runtime, game compatibility and Surface acceptance.

Builds/VMs are resource capped; test disks are disposable regular files under
`artifacts/vm-tests/`. Separately, the authorized whole-USB flash completed
PASS: complete image readback matched the tested SHA-256, GPT CRCs passed and
old physical-end GPT metadata was cleared. UDisks power-off also completed
successfully. Its private receipt is
`artifacts/usb-execution/full-flash-1795225100224929858/result.json`. USB DATA
remains 4 GiB; no secure full-media erasure or physical Surface boot is claimed.
No Surface internal-disk erase is authorized.

The 64 GiB disposable installation run
[`20261120-173902-install`](../artifacts/vm-tests/20261120-173902-install/result.json)
failed. It verified the complete source clone SHA-256, relocated GPT, expanded
the DATA partition and ran `resize2fs`/`e2fsck` successfully. The final guard
then reported `Filesystem did not expand to the verified DATA partition or
its identity changed`; the installer did not emit `SURFACE_VM_INSTALL_PASS`.
QEMU's clean exit code 0 does not turn this into an installation pass.

Diagnosis points to a stale whole-device cache read after resize through the
partition device. A fix using a fresh validated partition descriptor is now
compiled into image SHA-256
`3cdb38b2a39c96b06ba8a08573a08a8898ff0624a4477161d1873d4446da091d`.
The new `20261120-174857-install` run is in progress on
`artifacts/vm-tests/corrected-verified-install.qcow2`; its log is
`artifacts/build/corrected-vm-install.log`. No pass is inferred from compilation. No installed boot, constrained-memory/AHCI
profile or persistence result is claimed. Failure evidence remains in
`artifacts/vm-tests/20261120-173902-install/` and
`artifacts/build/final-vm-install.log`. The flashed live-tested image is not a
validated internal installer.

After a corrected installation, the planned boots are a default
4 GiB/four-vCPU run, a 3 GiB/two-vCPU/1280×800 AHCI run and another default
run on the same DATA. Remaining commands are examples for that new test disk:

```sh
scripts/vm-install.sh
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2 --cpus 2 --memory 3072
scripts/vm-boot-test.sh --disk artifacts/vm-tests/INSTALL-RUN/installed.qcow2 --sata --width 1280 --height 800
```

Optional Proton setup requires explicit `--test-proton` with an existing
installed VM disk and sufficient DATA space. Downloads occur only when
requested; runner setup cannot establish DW3 gameplay.
