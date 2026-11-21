# https://github.com/libretro/gambatte-libretro (immutable upstream revision)
SURFACE_GAMBATTE_VERSION = 77f2567d718b8973d02caa2daa8dce4953faf811
SURFACE_GAMBATTE_SITE = $(call github,libretro,gambatte-libretro,$(SURFACE_GAMBATTE_VERSION))
SURFACE_GAMBATTE_LICENSE = GPL-2.0
SURFACE_GAMBATTE_LICENSE_FILES = COPYING

define SURFACE_GAMBATTE_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/. \
		-f Makefile.libretro platform=unix
endef

define SURFACE_GAMBATTE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/./gambatte_libretro.so $(TARGET_DIR)/usr/lib/libretro/gambatte_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/COPYING $(TARGET_DIR)/usr/share/licenses/surface-gambatte/COPYING
endef

$(eval $(generic-package))
