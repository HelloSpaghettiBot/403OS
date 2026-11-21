# https://github.com/libretro/libretro-fceumm (immutable upstream revision)
SURFACE_FCEUMM_VERSION = 77468e568d9fe9c48d684ca911f1a3fe7259fdf7
SURFACE_FCEUMM_SITE = $(call github,libretro,libretro-fceumm,$(SURFACE_FCEUMM_VERSION))
SURFACE_FCEUMM_LICENSE = GPL-2.0+
SURFACE_FCEUMM_LICENSE_FILES = src/fceu.c README.md

define SURFACE_FCEUMM_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/. \
		-f Makefile.libretro platform=unix
endef

define SURFACE_FCEUMM_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/./fceumm_libretro.so $(TARGET_DIR)/usr/lib/libretro/fceumm_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/src/fceu.c $(TARGET_DIR)/usr/share/licenses/surface-fceumm/fceu.c
	$(INSTALL) -D -m 0644 $(@D)/README.md $(TARGET_DIR)/usr/share/licenses/surface-fceumm/README.md
endef

$(eval $(generic-package))
