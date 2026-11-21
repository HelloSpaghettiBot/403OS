# Online game setup integration

PokeMMO has a real Linux portable client. This service downloads its
unmodified official client only after an explicit HOME installation action.
It does not include a client, ROM, account or credentials in the repository
or OS image. Installation and gameplay have not yet been tested in the VM.

Install `pokemmo-setup` and `pokemmo-launch` into `/usr/bin/`, and
`pokemmo-setup.py` into `/usr/libexec/surface/`. Merge `runtime.fragment`
with the emulator fragment when building the image. HOME should run
`/usr/bin/pokemmo-setup --status` without network access, display an explicit
Install action when absent, and invoke `--install` only when that action is
selected. Capture setup output and show it as progress/errors in HOME.
Launch the installed application with `/usr/bin/pokemmo-launch`.

The installer requires the guest image marker, a mounted persistent DATA
filesystem and at least 2 GiB free. It checks HTTPS redirect hosts, archive
paths, ZIP links and expanded sizes, then installs with one atomic directory
rename to `/data/apps/pokemmo`. Existing installs are preserved. The
client's own launcher and update behavior remain intact. The official client
performs sign-in and game-file selection; HOME never handles credentials.

HOME also provides guided owner ROM import. `pokemmo-setup --scan-roms FOLDER`
reads at most 512 header bytes per candidate under `/library/roms`, `/media`,
`/run/media` or `/data/imports`, with bounded traversal. It recognizes cartridge
game codes plus internal titles, manufacturer, header checksum and geometry;
filenames are not used to identify a game. Only Black/White 1, FireRed, Emerald,
Platinum and HeartGold/SoulSilver are eligible. Nintendo logos and game content
are not included in the source or synthetic tests. A matching header cannot
prove that a dump is unmodified or compatible with the current official client.

After selecting a result and confirming its copy, `--import-rom FILE` checks the
guest marker and DATA mount, rejects symlinks/devices/pipes/outside source paths,
hashes the source, copies into a private temporary file, verifies its SHA-256,
and publishes without replacing any existing destination. Source changes abort
the import and remove staging. Identical existing files are retained; different
files with the same game/code/revision are refused without modification.
`--rom-status` reports installed headers and whether a Black/White 1 header is
present. The official client's **Client Management** screen remains responsible
for final validation and any ROM selection; automatic registration is not claimed.

The current official ZIP's top-level `roms/` directory was confirmed on
November 19, 2026 through two bounded HTTPS Range requests: a 65,557-byte tail
and an 18,217-byte central directory, containing 235 entries. No client payload
was downloaded for this metadata check. Imports require that directory already
exist in the installed official client. The
[official ROM import guide](https://support.pokemmo.com/knowledgebase/article/importing-or-changing-roms)
and [portable download guide](https://pokemmo.com/en/downloads/portable/)
identify Black/White 1 as required, and FireRed, Emerald, Platinum and
HeartGold/SoulSilver as optional region content. Header layout/checksum evidence
comes from [devkitPro ndstool](https://github.com/devkitPro/ndstool/blob/master/source/header.cpp)
and the cartridge structures in
[mGBA](https://github.com/mgba-emu/mgba/blob/master/include/mgba/internal/gba/gba.h).
Read-only header inspection recognized the owner's seven supported source files;
real importing, account sign-in and gameplay remain untested in the image.

The client endpoint is
`https://dl.pokemmo.com/download/PokeMMO-Client.zip`, currently linked from the
[official Linux download guide](https://pokemmo.com/en/downloads/linux/).
On November 19, 2026 its HEAD response was HTTP 200, application/zip,
322,543,241 bytes. This is a mutable upstream client download. Its actual
SHA-256 is recorded in `/data/apps/pokemmo/install.json` at installation;
that receipt is not a pinned client version or an independent signature.
HTTPS source validation supplies transport authenticity.

The [official Java requirement](https://pokemmo.com/en/java/) is Java 17 or
newer. Setup also downloads the official Eclipse Temurin Linux x64 headful
JRE 17.0.21.1+2 with pinned SHA-256
`5a092a0bc21de4c3d855480edd7945be9cfe157b38cdb84dac280511893bb009`.
The exact release is verified against the
[Adoptium release metadata](https://github.com/adoptium/temurin17-binaries/releases/tag/jdk-17.0.21.1%2B2).
The runtime's own `legal/` notices are retained alongside its files.
This avoids building a full JDK for this first-run service. Native library,
graphics and audio compatibility remain to be tested in the target image.

[PokeMMO Terms of Service](https://pokemmo.com/en/tos/), section 3, grants
non-commercial use and limited redistribution of unmodified client software
without distribution fees. This project uses an owner-initiated official
download. [PokeMMO's official launcher](https://github.com/PokeMMO/PokeMMO-Launcher)
provides its own update/signature functionality. The client remains subject
to its own license and first-login terms.

## Other online games

Digimon World 3 Online uses a separate Windows-client import and Wine/UMU
launch workflow. See [DW3 setup](../dw3/README.md) and
[Windows compatibility](../../docs/WINDOWS_COMPATIBILITY.md).
Clients and account data are supplied by the user and are not bundled.
