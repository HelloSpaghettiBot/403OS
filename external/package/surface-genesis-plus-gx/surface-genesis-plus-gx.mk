# https://github.com/libretro/Genesis-Plus-GX (immutable upstream revision)
SURFACE_GENESIS_PLUS_GX_VERSION = b9c2316441978d4015ba30e9a95d9b0e6f8ee981
SURFACE_GENESIS_PLUS_GX_SITE = $(call github,libretro,Genesis-Plus-GX,$(SURFACE_GENESIS_PLUS_GX_VERSION))
SURFACE_GENESIS_PLUS_GX_LICENSE = Genesis-Plus-GX (non-commercial), LGPL-2.1+
SURFACE_GENESIS_PLUS_GX_LICENSE_FILES = LICENSE.txt

define SURFACE_GENESIS_PLUS_GX_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/. \
		-f Makefile.libretro platform=unix
endef

define SURFACE_GENESIS_PLUS_GX_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/./genesis_plus_gx_libretro.so $(TARGET_DIR)/usr/lib/libretro/genesis_plus_gx_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/LICENSE.txt $(TARGET_DIR)/usr/share/licenses/surface-genesis-plus-gx/LICENSE.txt
endef

$(eval $(generic-package))
