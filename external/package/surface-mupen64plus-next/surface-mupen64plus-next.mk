# https://github.com/libretro/mupen64plus-libretro-nx (immutable upstream revision)
SURFACE_MUPEN64PLUS_NEXT_VERSION = 15bada85906593376e8fd697f1b0b3af3a20f512
SURFACE_MUPEN64PLUS_NEXT_SITE = $(call github,libretro,mupen64plus-libretro-nx,$(SURFACE_MUPEN64PLUS_NEXT_VERSION))
SURFACE_MUPEN64PLUS_NEXT_LICENSE = GPL-2.0+
SURFACE_MUPEN64PLUS_NEXT_LICENSE_FILES = LICENSE
SURFACE_MUPEN64PLUS_NEXT_DEPENDENCIES = libgles host-nasm

define SURFACE_MUPEN64PLUS_NEXT_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/. \
		-f Makefile platform=unix ARCH=x86_64 WITH_DYNAREC=x86_64 FORCE_GLES3=1 HAVE_PARALLEL_RDP=0 HAVE_PARALLEL_RSP=0
endef

define SURFACE_MUPEN64PLUS_NEXT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/./mupen64plus_next_libretro.so $(TARGET_DIR)/usr/lib/libretro/mupen64plus_next_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/LICENSE $(TARGET_DIR)/usr/share/licenses/surface-mupen64plus-next/LICENSE
endef

$(eval $(generic-package))
