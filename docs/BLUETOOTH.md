# Bluetooth on Surface Go

Open **Controllers**, put the controller in pairing mode, then choose
**Scan for controllers**. Select the controller to pair and connect. Saved
pairings are retained across shutdown and installation. Bluetooth-only
software radio blocks are cleared when you explicitly start a scan;
hardware radio blocks remain under the device's control.

## Adapter detection correction

The November 21 update changes the USB Bluetooth transport from a built-in
driver to the `btusb` module. Firmware is stored on the real root filesystem,
and this image has no firmware-bearing initramfs. Loading the module through
udev and `S11modules` makes that firmware available before the USB adapter is
probed. The image build checks the resolved module setting, installed object,
dependency and USB alias records, startup configuration and Qualcomm firmware.

This follows the modular transport configuration in the
[Pop!_OS Ubuntu-based kernel](https://github.com/pop-os/linux/blob/3bf1542e961154e94381c05c778819335218bad5/debian.master/config/annotations).
The shipped Qualcomm USB 0302 rampatch and NVM files have the same Git blob
hashes as the corresponding files in
[Pop!_OS's firmware repository](https://github.com/pop-os/linux-firmware/tree/master/qca).
The kernel documents why drivers needing early firmware must have that
firmware [available before filesystem access](https://docs.kernel.org/driver-api/firmware/built-in-fw.html).

For adapters needing a firmware download, the previous built-in transport
could request it before the root filesystem was mounted. An HCI controller
left in setup is omitted from BlueZ's adapter list. This correction removes
that startup risk; no kernel failure log or USB identification was retained
from the reported Surface boot, so its original physical cause has not been
confirmed. Some Surface Go USB revisions use firmware already in flash.

The controller screen now waits for an actual BlueZ adapter before requesting
power. A successful but empty adapter list does not count as readiness.
USB Bluetooth autosuspend remains disabled by default. No untested device-ID
quirk, forced USB reset or relaxed pairing policy is applied.

## Diagnostic records

Every boot saves `/data/logs/bluetooth-boot.json`. A failed enable request saves
`/data/logs/bluetooth-last-error.json`. These bounded files contain firmware
hashes, Bluetooth USB transport details, radio-block state, BlueZ state and
relevant kernel messages. They are root-readable only. Adapter addresses are
redacted; USB serial numbers, saved bonds and passwords are not collected.

VM checks establish that the module loads, firmware ships, the daemon runs
and the diagnostic record is saved. QEMU does not supply the physical Surface
radio. Adapter detection, controller pairing, gameplay, reconnect and resume
still require a Surface cold boot and physical tests.
