# Architecture

Buildroot owns the pinned toolchain, kernel, root filesystem, GRUB EFI binary
and GPT image. The br2-external tree adds the original C++/SDL3 shell and
emulator/application packages. Exact versions, source hashes and licenses live
in package recipes and Buildroot's hash database. A checksum-pinned upstream
LLVM binary distribution supplies isolated host tools needed by Mesa; target
LLVM is not installed. The host-toolchain override records the substitutions.

Weston kiosk mode runs directly over DRM/KMS without a desktop. The session
selects GL when the actual DRM driver is i915, with Iris/EGL/GLES, Intel ANV
and VA-API packaged. QEMU uses the Pixman/software fallback. Inclusion of the
Intel stack does not establish physical Surface acceleration or decoding.
Xwayland supports Dolphin NoGUI and Windows applications; Cog and the custom
shell use Wayland. SDL3's Wayland presentation needs EGL/GLES even with a
software renderer.

The shell separates navigation/layout, sysfs status, supervised argv process
launches, library/progress persistence, rendering and service panels. Status
sampling is asynchronous. Event waits and redraw-on-change avoid a busy idle
loop. Controller, keyboard and touch share navigation actions. The controller
wizard saves a private SDL mapping database for SDL2/3 and Dolphin. Favorites,
history and per-game GameCube native-1x presets persist atomically. The UI
hides while an application runs; Guide or Start exposes HOME controls and
termination gives the child time to save. A guarded read-only evdev observer
provides Ctrl+Alt+Home and a three-contact touch return while another app has
focus. It never grabs devices or records input; synthetic tests are separate
from physical touchscreen acceptance.

Ten libretro cores use the original SDL3 host; standalone Dolphin is the
eleventh engine. Save RAM, states, BIOS and configs are separate from
read-only game sources. The library scanner detects actual files and can
fetch matching art on explicit request. System posters and dock graphics are
original assets; commercial covers stay in private test/data directories.
External library mounting excludes system/data parents and active storage,
uses read-only filesystem-specific options and never treats an existing
read-write mount as a verified read-only source.

WiFi uses wpa_supplicant/DHCP; Bluetooth uses BlueZ. Their UI commands use
bounded literal argv and private state. Tailscaled starts with private DATA
state and enrolls only on explicit action. Moonlight discovery/manual host,
pairing and profile controls use the maintained Embedded client. Actual
Sunshine streaming and enrolled identity require authorized external systems.
Cog/WPE WebKit starts on demand and closes before HOME resumes.

PokeMMO setup explicitly fetches the official client and pinned Java runtime;
its ROM importer validates owner-supplied files. DW3 imports an unchanged
complete owner installation. Wine64 and UMU run as UID/GID 1000, with private
persistent prefixes and a separate runtime directory. UMU setup is the only
path allowed to download Proton/runtime. Windows execution uses Xwayland and
an OpenGL default. The Windows supervisor tracks its own descendants,
including detached Wine processes, and cleans them before returning. No game
compatibility follows from successful runner setup alone.

The layout has 64 MiB EFI, 2 GiB ext4 root and 4 GiB ext4 DATA. Root is writable
for development diagnostics; recoverable read-only system and signed updates
remain release work. Internal DATA expansion is an explicit installer stage
under validation, not a general boot-time resize. It accepts only the exact
freshly cloned layout and preserves EFI/root data. The whole-device USB
flasher keeps the source image's three-partition layout and 4 GiB DATA; it
does not perform the internal installer's DATA expansion.

The host VM harness creates only new regular qcow2 files below
artifacts/vm-tests. The disposable guest installer requires explicit VM
confirmation, exact DMI, boot opt-in, disk serial and geometry. Live and cold
boots use unsigned OVMF and GRUB from the image; installed boots detach the
installer. Serial readiness, framebuffer changes, browser rendering, Windows
command execution, PCM capture and homebrew SRAM checks supply evidence.
Synthetic input and virtual devices remain distinct from Surface behavior.

The physical installer reviews internal storage, requires an exact typed erase
phrase, verifies source SHA-256 before writable open, rechecks hardware
identity and mounts, then verifies clone/GPT results. The separate whole-USB
flasher uses a private stable-identity/checksum plan, completed live-boot
evidence, exclusive UDisks access and complete image readback. It erases all
old USB partitions/files under the latest owner authorization and clears the
obsolete physical-end GPT. It cannot substitute for deliberate internal-disk
confirmation; no Surface internal erase is authorized.
