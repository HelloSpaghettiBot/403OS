# Sleep and the power button

Tap and release the power button to put 403 OS to sleep. The handler runs
independently of HOME, including while a game or browser is open.
**Settings → Suspend device** uses the same sleep service. Press the power
button to wake the device when supported by its firmware and hardware.

The system uses the kernel's available suspend state without overriding its
firmware sleep policy. Games and applications remain running across sleep;
save your progress regularly. Physical Surface wake, controller reconnection
and battery use during sleep require hardware testing.

Button repeats and holds longer than two seconds do not request sleep. Wake
releases and duplicate events are ignored to prevent immediate re-sleep.
Installation, migration and system-update locks defer sleep while storage work
is running. A long emergency hold remains controlled by the device firmware.
