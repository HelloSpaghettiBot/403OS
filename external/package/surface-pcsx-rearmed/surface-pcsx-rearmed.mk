# https://github.com/libretro/pcsx_rearmed (immutable upstream revision)
SURFACE_PCSX_REARMED_VERSION = 7b0e8f8fadfbce3d170d195b9d9eecaabfa08c8e
SURFACE_PCSX_REARMED_SITE = $(call github,libretro,pcsx_rearmed,$(SURFACE_PCSX_REARMED_VERSION))
SURFACE_PCSX_REARMED_LICENSE = GPL-2.0+
SURFACE_PCSX_REARMED_LICENSE_FILES = COPYING

define SURFACE_PCSX_REARMED_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(MAKE) -C $(@D)/. \
		-f Makefile.libretro platform=unix ARCH=x86_64 DYNAREC=lightrec BUILTIN_GPU=neon HAVE_NEON_ASM=0
endef

define SURFACE_PCSX_REARMED_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/./pcsx_rearmed_libretro.so $(TARGET_DIR)/usr/lib/libretro/pcsx_rearmed_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/COPYING $(TARGET_DIR)/usr/share/licenses/surface-pcsx-rearmed/COPYING
endef

$(eval $(generic-package))
