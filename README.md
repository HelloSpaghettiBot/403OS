# 403 OS

A controller-first Linux gaming OS for Microsoft Surface Go, with a native
C++/SDL3 interface for games, emulators, streaming and system settings.

![403 OS HOME running at 1800×1200](docs/images/home.png)

*Actual HOME interface in QEMU. Game artwork appears when a library is added.*

![Browse emulator systems in 403 OS](docs/images/systems.png)

*Actual emulator browser with the included original system artwork.*

## Features

- Automatically find games in local storage and connected game drives; search
  by title, browse systems, and keep cover art, favorites and play history.
- Eleven bundled emulator engines: FCEUmm, Snes9x, Gambatte, mGBA,
  Genesis Plus GX, PCSX ReARMed, Mupen64Plus Next, melonDS DS, Flycast,
  PPSSPP and Dolphin.
- Moonlight streaming, Tailscale, a WebKit browser and Wine64/UMU integration.
- Gamepad, keyboard and touch navigation, with audio, network and display controls.
- Broad USB/Bluetooth controller drivers, SDL mappings and a custom mapping wizard.
- Password-protected profiles, a first-run root administrator and separate user app identities.
- Display resolution trials and sensor-driven rotation of the internal screen.
- Live USB and an internal-storage installer that carries over local games,
  saves, settings and password-protected profiles from the live USB.

Supply your own games and required BIOS files. Public builds contain no ROMs,
commercial game artwork, proprietary clients or account credentials.

Put local games in `/data/roms`, or connect a supported USB/SD game drive.
HOME updates automatically. Open **Games → Search games** to search titles or
choose **Find games** to refresh; downloading missing covers is a separate
action. Connected game drives are mounted read only.

Installing erases the selected internal disk. The installer then copies and
verifies supported live-USB DATA, including local games, saves and profiles;
keep using the same profile passwords after installation. Files already on the
target disk need a separate backup. External game drives remain separate.

![403 OS installer verifying games and profiles in QEMU](docs/images/installer.png)

## Getting started

[Installation and recovery](docs/INSTALL_RECOVERY.md) ·
[Remote play](docs/REMOTE_PLAY.md) ·
[Windows applications](docs/WINDOWS_COMPATIBILITY.md) ·
[Build from source](docs/BUILD.md)

**Development preview.** VM boot and runtime checks cover the included
components; they do not establish performance or compatibility on Surface
hardware. Surface touchscreen, accelerometer, Bluetooth pairing and individual
controller models still require physical testing.
See [status](docs/STATUS.md),
[roadmap](docs/ROADMAP.md) and [test results](docs/VM_TEST_REPORT.md).

Original project code is MIT licensed. Dependencies retain their own licenses;
see [third-party notices](THIRD_PARTY_NOTICES.md).
