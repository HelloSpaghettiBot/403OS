# https://github.com/libretro/snes9x (immutable upstream revision)
SURFACE_SNES9X_VERSION = 204ea1c0b851f960cbf223075eb1c25641cb483c
SURFACE_SNES9X_SITE = $(call github,libretro,snes9x,$(SURFACE_SNES9X_VERSION))
SURFACE_SNES9X_LICENSE = Snes9x
SURFACE_SNES9X_LICENSE_FILES = LICENSE

define SURFACE_SNES9X_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/libretro \
		-f Makefile platform=unix
endef

define SURFACE_SNES9X_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/libretro/snes9x_libretro.so $(TARGET_DIR)/usr/lib/libretro/snes9x_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/LICENSE $(TARGET_DIR)/usr/share/licenses/surface-snes9x/LICENSE
endef

$(eval $(generic-package))
