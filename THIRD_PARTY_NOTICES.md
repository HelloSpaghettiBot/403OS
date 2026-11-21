# Dependencies and distribution gates

Original project code is MIT licensed (ui/LICENSE). Do not distribute images
without Buildroot legal-info output, source offers where required and a
reviewed license manifest.

| Component | Pin/source | License |
|---|---|---|
| Buildroot | [2026.08.2](https://buildroot.org/downloads/buildroot-2026.08.2.tar.xz) | GPL-2.0 and package licenses |
| Linux | 6.12.118; official kernel.org release SHA-256 | GPL-2.0 |
| SDL3 | [3.4.20](https://github.com/libsdl-org/SDL/releases/tag/release-3.4.20) | Zlib |
| Weston | 15.0.1, Buildroot hash database | MIT |
| Mesa | Version/hash in pinned Buildroot package | MIT and included notices |
| BlueZ | Version/hash in pinned Buildroot package | GPL-2.0+/LGPL-2.1+ |
| GRUB | Version/hash in pinned Buildroot package | GPL-3.0+ |
| Bootlin toolchain | x86-64 glibc stable 2025.08-1 | Per-component GPL/LGPL |
| Debian builder | Containerfile digest | Per-package licenses |
| FCEUmm, Snes9x, Gambatte, mGBA, Genesis Plus GX, PCSX ReARMed, Mupen64Plus Next | Exact revisions and hashes in external/package | Per-engine licenses, including noncommercial restrictions for Genesis Plus GX |
| melonDS DS, Flycast, PPSSPP, Dolphin | Exact release/revision and vendored dependency hashes in external/package | GPL and included dependency notices |
| WPE WebKit/Cog | Pinned Buildroot packages | LGPL/BSD and included notices |
| Moonlight Embedded | 2.7.1 release archive | GPL-3.0+, LGPL-2.1+, MIT, Zlib |
| Tailscale | Official 1.104.7 amd64 release | BSD-3-Clause and dependency notices |
| Wine64 | Official Wine 11.0 source | LGPL-2.1+ and included notices |
| UMU launcher | Official 1.4.4 checksum-pinned zipapp/source | GPL-3.0, LGPL-2.1+, MIT; bundled module notices installed |
| LLVM host tools | Official 22.1.8 Ubuntu 22.04 archive; isolated host prefix | Apache-2.0 with LLVM exception and included notices |
| Hardware firmware | Pinned linux-firmware, selected Intel/Qualcomm files | Per-file redistribution terms recorded by Buildroot |
| Cantarell | Local font asset provenance in ui/assets | SIL Open Font License; license ships with assets |

No commercial console game assets, ROMs, console BIOS or encryption keys ship.
User-library tests and their covers remain private artifacts. The MIT NES test
cartridge is original project code. Emulator engines retain their upstream
terms; inclusion is not permission to sell every engine commercially.

PokeMMO client and Java runtime setup occurs only on an explicit first-run
action under their upstream terms. The owner's Digimon World 3 Online client
is imported unchanged from owner storage and is not redistributed. Proton
and Steam Linux Runtime are explicit mutable upstream downloads into DATA;
their versions/receipts and notices are distinct from the pinned base image.
Neither online game has passed target gameplay acceptance.

Published SDL3 archive hash is pinned. Buildroot archive hash was recorded
from the official TLS download; its detached signature has not been verified.
Container base is digest-pinned, but apt package versions are not yet locked
to a snapshot: the builder is repeatable tooling, not a proven hermetic build.
