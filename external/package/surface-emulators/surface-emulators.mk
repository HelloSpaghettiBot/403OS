SURFACE_EMULATORS_VERSION = 1
SURFACE_EMULATORS_SOURCE =
SURFACE_EMULATORS_LICENSE = MIT
SURFACE_EMULATORS_DEPENDENCIES = surface-fceumm surface-snes9x surface-gambatte \
    surface-mgba surface-genesis-plus-gx surface-pcsx-rearmed surface-mupen64plus-next \
    surface-melonds-ds surface-flycast surface-ppsspp surface-dolphin
$(eval $(generic-package))
