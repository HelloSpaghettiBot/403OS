# Digimon World 3 Online owner-client import

The inspected owner directory is **Digimon World 3 Online**, distributed by
[Digitalna](https://digitalna.com.br/). Its [official downloads](https://digitalna.com.br/baixar)
offer a Windows 64-bit client and Android application. The Windows installation
contains a Java 17 runtime, LWJGL and Windows native libraries, including
`app/dwcofre.dll`. Standard PE architecture headers identify the launcher,
native library and bundled Java as AMD64. A Linux Java runtime alone cannot
load those Windows native libraries.

The separate `surface-wine64` Buildroot package builds the actual Wine 11.0
source for Windows x86-64 applications. It uses X11/OpenGL through Weston
Xwayland, ALSA, SDL2 controllers, TLS and the matching Buildroot host-Wine tools.
Upstream's `--enable-win64` profile supports 64-bit applications; this package
does not build 32-bit Wine or a WoW64 environment. Wine 11 uses `/usr/bin/wine`.
Source and both license-file hashes are pinned in the package. See the
[Wine 11.0 release](https://gitlab.winehq.org/wine/wine/-/releases/wine-11.0),
[upstream source](https://dl.winehq.org/wine/source/11.0/) and
[Buildroot's Wine 11.0 update](https://lists.buildroot.org/pipermail/buildroot/2026-February/796236.html).

No proprietary client or credentials are bundled. The
[official terms](https://digitalna.com.br/termos) govern the owner's use of
their client; redistribution permission has not been established. The helper
copies the existing installation unchanged and does not extract its opaque
game archive, modify the client, inspect account data or implement its protocol.
The original launcher and updater remain responsible for the application.

Install `dw3-setup` to `/usr/bin/dw3-setup`, `dw3-setup.py` to
`/usr/libexec/surface/dw3-setup.py` and `dw3-launch` to `/usr/bin/dw3-launch`.
Add the Wine package's `Config.in` to the external tree and merge
`runtime.fragment` with `external/configs/emulators.fragment` before building.
The image needs mounted persistent DATA and an active Xwayland `DISPLAY`.

HOME's DW3 panel can use these commands:

```sh
dw3-setup --status
dw3-setup --import '/library/roms/DW3 Online'
dw3-launch
```

Status prints `IMPORTED` with exit 0 or `IMPORT_REQUIRED` with exit 1, and never
uses the network. Import is an explicit action that accepts a directory below
`/library/roms`, `/media`, `/run/media` or `/data/imports`. The VM's read-only
owner Games share supplies the example above. Required files are
`DW3 Online.exe`, `app/client.jar`, `app/dwcofre.dll`,
`runtime/bin/java.dll` and `data/data.pak`.

Import preserves the `app`, `runtime` and `data` directories, the launcher and
its optional icon. Root-level personal exports and unrelated files stay on the
source media. Imports reject links, special files and non-AMD64 native headers;
files are opened without following links in any path component. Supported
client size is at most 2 GiB and 50,000 files. DATA must have room for the copy
plus 512 MiB for a Wine prefix. The helper uses an exclusive lock and publishes
from fresh staging via rename, preserving an existing `/data/apps/dw3`.
Before publication, only the new unpublished tree is assigned to gaming
UID/GID 1000 using no-follow file descriptors. Directories are 0755 and
ordinary client files 0644. The root-owned staging parent stays private until
publication; existing installations are never adopted or recursively chowned.
The image provisions the gaming account and a traversable `/data/apps` parent.
Progress is printed as `Importing DW3 installation: N/M files`; failures go to
stderr as `DW3 import failed: ...` with exit 1. A receipt records the imported
launcher SHA-256 and explicitly marks runtime compatibility `UNTESTED`.

Launch calls `/usr/bin/surface-windows-runner --launch-dw3 --runner wine`.
That runner drops permanently to gaming UID/GID 1000 before opening state or
executing the original imported `DW3 Online.exe`, using the persistent
`/data/windows/dw3-wine` prefix. Optional Proton uses a separate
`/data/windows/dw3-proton` prefix after explicit setup. It does not log in or
store credentials. The client may
perform its own normal network updates and login after the owner launches it.

Validation so far covers source hashes/options, shell syntax and synthetic
import fixtures. Target compilation, Wine prefix initialization, client startup,
graphics, input, sound and online gameplay have not yet been tested. HOME must
not describe this as preinstalled or confirmed playable until those tests pass.

```sh
python3 -m unittest discover -s services/dw3 -p 'test_*.py' -v
```
