# Surface Go Gen 1 physical acceptance — UNTESTED

Boot the live image only after the VM boot/install gates pass. Secure Boot
must be disabled for unsigned development media. Do not replace Windows or
write the internal disk during hardware discovery. No physical test ran here.

Collect the following from a separate known-working Linux live environment
on the Surface. Redirect logs to a writable external drive, not internal disk.

```sh
uname -a
lspci -nnk
lsusb
cat /proc/bus/input/devices
cat /sys/class/dmi/id/{product_name,product_version,bios_version}
dmesg
cat /proc/asound/cards
ls /sys/class/{drm,power_supply,backlight,bluetooth}
```

| Area | Required evidence | Status |
|---|---|---|
| UEFI/live boot | Photo, serial/boot logs, firmware settings | UNTESTED |
| Intel HD 615 | Actual driver, 1800x1200 mode, acceleration | UNTESTED |
| Touch/rotation | All corners, scaling, gestures, concurrent input | UNTESTED |
| Type Cover | Hotplug, keyboard, touchpad, suspend reconnect | UNTESTED |
| USB/Bluetooth controllers | Pair, reconnect, navigation, gameplay | UNTESTED |
| WiFi | PCI/USB ID, firmware, DHCP/DNS/HTTPS, reconnect | UNTESTED |
| Audio | Speakers/headphones, levels, app switching | UNTESTED |
| Battery/brightness | Sysfs values, charging, controls, power draw | UNTESTED |
| Storage | eMMC/SSD/microSD device IDs and safe mount | UNTESTED |
| Suspend/resume | Repeated cycles, touch/radio/audio recovery | UNTESTED |
| Moonlight LAN | Sunshine pair, video/audio/input, decode/latency | UNTESTED |
| Tailscale stream | Authorized tailnet, persistence/reconnect | UNTESTED |
| GameCube | Per-game native-resolution FPS, thermal/battery | UNTESTED |

VM results cannot establish any of these hardware behaviors.

Research leads: the upstream [Surface Go notes](https://github.com/linux-surface/linux-surface/wiki/Surface-Go)
describe a firmware-dependent ath10k WiFi workaround and reports of microSD
affecting suspend power use. These are leads for testing, not measured faults
on the owner's device. Identify the actual radio/firmware and reproduce a
problem before selecting patches or overrides. The upstream
[feature matrix](https://github.com/linux-surface/linux-surface/wiki/Supported-Devices-and-Features)
is reference material; it does not replace this project's hardware results.
