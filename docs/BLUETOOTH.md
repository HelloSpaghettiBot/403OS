# Bluetooth on Surface Go

Open **Controllers**, put the controller in pairing mode, then choose
**Scan for controllers**. Select the controller to pair and connect. Saved
pairings are retained across shutdown and installation. Bluetooth-only
software radio blocks are cleared when you explicitly start a scan;
hardware radio blocks remain under the device's control.

Pairing keeps an authentication agent running until the operation finishes.
If the controller requests a PIN or passkey, enter it using the on-screen
keyboard. Confirm a displayed passkey only when it matches your device.
Confirmation starts on **Reject**; rejection or cancellation does not trust
or connect the device. Success requires the device to report that it is paired,
trusted and connected. **Connect** reconnects without pairing again.

Scanning runs for 20 seconds and fills in device names as they arrive. Keep
your controller in pairing mode until the scan finishes. Named devices appear
first, with a short address suffix to distinguish identical controllers.
Devices that do not advertise a name appear as **Unnamed Bluetooth device**;
their full address is available when selected. Refresh reads saved devices;
start another scan to discover a name that becomes available later.

## Start the updated image

When testing from USB, select USB startup in the Surface firmware. An older
installed copy can still show its previous Bluetooth driver behavior when
the firmware starts that copy. See
[Surface firmware and USB boot](INSTALL_RECOVERY.md#surface-firmware-and-usb-boot)
for the Volume Down and Power sequence and the Secure Boot setting required
by this unsigned build.

Newly packaged USBs use separate root and DATA partition identifiers from
installed systems, with a matching EFI root selector. Startup also mounts
DATA from the running system's own disk. These corrections prevent an
updated USB from selecting an older installed root or its saved settings.
Surface adapter discovery has been reported working after the client update.
Controller pairing, gameplay, reconnect and resume still require physical tests.

## Bluetooth client and kernel fixes

BlueZ 5.86 had a noninteractive shell regression: `bluetoothctl list`,
`show` and power commands could exit successfully without printing their
results. This made a detected adapter appear stuck during initialization.
The shipped client reproduced that failure against a valid isolated test
adapter. The image now includes the upstream
[output correction](https://github.com/bluez/bluez/commit/b33e923b55e4d0e9d78a83cfcb541fd1f687ef54)
and its [management-tool follow-up](https://github.com/bluez/bluez/commit/21e13976f2e375d701b8b7032ba5c1b2e56c305f).
Regression checks use the actual built client for adapter listing, power
readback, discovery, pairing, trust, connect, disconnect and removal on a
private D-Bus fixture. They never access the host radio.

The kernel also includes the user-space AF_ALG hash and cipher interfaces
required by BlueZ's AES and CMAC operations. Image checks require those
interfaces and algorithms. VM acceptance executes public AES and CMAC
known-answer vectors, as well as a real `bluetoothctl version` shell command;
`bluetoothctl --version` alone would miss the output regression.

The USB transport remains a `btusb` module loaded after the real root
filesystem and firmware are available, through udev and `S11modules`.
Packaging checks module dependencies, USB aliases and the Qualcomm USB 0302
rampatch/NVM files. Those files match the corresponding
[Pop!_OS firmware](https://github.com/pop-os/linux-firmware/tree/master/qca).
USB Bluetooth autosuspend stays disabled by default. Hardware blocks, saved
bonds and pairing policy are preserved. No speculative device quirk or USB
reset is applied.

The controller screen waits for an actual BlueZ adapter and verifies its
powered state before scanning. A silent command-client failure is reported
separately from missing hardware or unfinished initialization.

The original one-command pairing path supplied an agent option that BlueZ's
noninteractive mode ignored. The replacement uses a persistent interactive
client and waits for actual agent registration before pairing. Isolated tests
exercise the shipped client's PIN, passkey, confirmation and authorization
callbacks, rejection, command order and final state readback.

`br-connection-create-socket` is BlueZ's generic label for an I/O error; it can
also represent remote connection or authentication failure. Pairing and
connection errors now save a fresh private diagnostic record containing the
daemon's more specific error. This build does not disable authentication,
erase existing bonds automatically or accept pairing questions silently.

## Diagnostic records

Every boot saves `/data/logs/bluetooth-boot.json`. A failed enable request saves
`/data/logs/bluetooth-last-error.json`. These bounded files contain firmware
hashes, Bluetooth USB transport details, radio-block state, client version, BlueZ daemon errors and
relevant kernel messages. They are root-readable only. Adapter addresses are
redacted; USB serial numbers, saved bonds and passwords are not collected.

VM checks establish that the module loads, firmware ships, the daemon runs
and the diagnostic record is saved. QEMU does not supply the physical Surface
radio. The owner reports working discovery on a Surface; that report does not
establish controller pairing, gameplay, reconnect or resume behavior.
