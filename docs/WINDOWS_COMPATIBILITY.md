# Windows application compatibility

Owner requested a Proton-style Windows compatibility layer on November 19,
2026. This extends the PDF's DW3 requirement. The default image configuration
builds Wine 11.0 for Windows x86-64 applications and the official UMU 1.4.4 launcher.
The launcher is not itself Proton. Its explicit first-run action installs the
Proton tool and Steam Linux Runtime in writable storage; no download occurs
at boot. Actual target execution remains unverified while the image builds.

[Valve Proton](https://github.com/ValveSoftware/Proton) builds on Wine and
additional gaming components. [UMU](https://github.com/Open-Wine-Components/umu-launcher)
provides the supported container-runtime launch path outside Steam. No Steam
account, Steam client, storefront credentials or game files are bundled.

The first application is the owner's unchanged Digimon World 3 Online Windows
x86-64 client. HOME offers its import, setup and runner selection. Wine and
Proton use separate private prefixes, so changing runners does not migrate an
existing Windows installation implicitly. Both run as the `gaming` user, with
private persistent HOME/runtime directories. Actual display/input/audio access
must be checked in the VM and on hardware. Device-management and
disk-installation controls stay in the shell. `services/dw3/dw3-launch` delegates
to the guarded Windows runner; it does not execute Wine as root.

Wine/OpenGL is the conservative default for this Java/LWJGL client. Proton has
an explicit OpenGL fallback using `PROTON_USE_WINED3D=1`; Vulkan/DXVK remains a
separate compatibility option requiring measured hardware support. The image
builds Intel ANV and Vulkan diagnostics alongside Iris. Mesa's source includes
Kaby Lake's generation, but driver inclusion is not proof of successful game
rendering on the physical HD 615. Current
[DXVK driver requirements](https://github.com/doitsujin/dxvk/wiki/Driver-support)
must be checked against `vulkaninfo` on that Surface before enabling DXVK.

Proton/runtime first-run setup needs at least 6 GiB free before it starts.
The current live image's 4 GiB DATA area cannot satisfy that check. A separately
validated installed-disk grow flow or sufficient compatible external storage
is required; do not silently download into a full live filesystem. Guided
setup reports the actual space constraint. Downloaded runtime versions and
receipts must be reported separately from the checksum-pinned base image.

The controller Windows applications panel registers a specific owner-selected
executable path, title and runner. It does not scan for or execute applications
automatically. Sources must be ordinary files on read-only mounted media or
below `/data/windows/apps`; symlink paths, control characters, DLLs and malformed
PE headers are rejected. Wine64 accepts AMD64 images. The Proton path also
recognizes I386 headers, with 32-bit execution still unverified. Format checks
do not prove that an application is safe, signed or compatible. Executables
remain unchanged. Applications which write beside their executable may need
an owner-prepared local copy under `/data/windows/apps` rather than read-only
external media.

The literal-argument service interface is:

```
surface-windows-runner --status
surface-windows-runner --probe-wine
surface-windows-runner --setup-proton
surface-windows-runner --launch-dw3 --runner wine
surface-windows-runner --launch-dw3 --runner proton
surface-windows-runner --register-exe /media/library/example/app.exe --title "Example" --runner wine
surface-windows-runner --list-exes
surface-windows-runner --launch-exe /media/library/example/app.exe --runner wine
```

Mutating/launch actions require the booted image marker, mounted DATA and the
expected `gaming` account. Privileges are permanently dropped to UID/GID 1000
before any user-owned prefix, log or runtime file is opened. Root only prepares
known service directories through no-follow directory descriptors. Fresh DW3
imports are published with gaming ownership; legacy root-owned installations
are preserved and must be re-imported if inaccessible. The root-owned
`/data/apps` parent permits traversal, while individual application directories
retain their privacy. Existing user-controlled trees are not recursively chowned.

DW3 uses `/data/windows/dw3-wine` and `/data/windows/dw3-proton`. General
applications use separate runner/path-derived prefix directories. Registry
references are private JSON in `/data/windows/apps/registry.json`. The runner
inherits the session's X11 `DISPLAY`, uses private `/run/surface-games`, clears
Wayland/SDL driver overrides and removes inherited library/Python/compatibility
overrides. It does not relax the compositor's private socket permissions.

The fixed Wine probe runs the built-in Windows command
`C:\windows\system32\cmd.exe /c exit 0` under `/data/windows/wine-probe`, with a
60-second limit. Its `wine-probe` JSON event records the actual exit and
`gameplay_verified:false`. Explicit Proton setup performs the same command
through UMU after checking the storage policy, with a 20-minute limit. Setup
records the selected Proton path/version, manifest SHA-256, runtime name and
probe result in private `/data/windows/proton-setup.json`; a bounded setup log
is `/data/windows/proton-setup.log`. Readiness additionally requires the
matching installed runtime marker and Proton's `pfx/system.reg`. The UMU
launcher is pinned; these runtime downloads are selected by upstream at setup
time and are not claimed to be reproducible parts of the base image.
Launch requires the matching setup receipt and installed artifacts, uses that
exact installed Proton path, and disables runtime updates. It does not perform
first-run setup or silently download a missing runtime.

Wine/UMU run under a supervising helper which becomes a child subreaper.
Closing an application terminates only tracked descendant PID/start-time/UID
identities, including detached sessions. TERM is followed by KILL and reaping
within a bounded 3.3-second cleanup window. It never kills all processes owned
by gaming. Controller command cancellation gives the supervisor a TERM window
before escalation.

Twenty-one native helper tests cover host refusal, storage/setup guards, PE
validation, literal argv, private registry/prefix handling, environment cleanup
and actual disposable child-process cleanup. The detached-child fixtures prove
that an unrelated same-UID sibling survives and a TERM-resistant descendant is
killed/reaped before HOME's five-second deadline. They do not run Wine, Proton
or a game. Native controller tests cover typed application references,
default-Cancel setup and host mutation refusal.

The implementation follows the pinned
[UMU 1.4.4 launch code](https://github.com/Open-Wine-Components/umu-launcher/tree/1.4.4),
[UMU's documented environment/setup interface](https://github.com/Open-Wine-Components/umu-launcher/blob/1.4.4/docs/umu.1.scd),
and [Microsoft's PE specification](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format).

Acceptance still required: run the fixed Windows probe in the target image, launch the exact
DW3 client to its visible start screen, verify keyboard/controller/audio and
clean HOME return, then test authorized sign-in/gameplay on hardware. A Wine
or UMU version string alone is not a DW3 compatibility pass. Windows x86
32-bit application coverage is not claimed by the pure Wine64 package;
Proton's separate runtime coverage requires its own tests.
