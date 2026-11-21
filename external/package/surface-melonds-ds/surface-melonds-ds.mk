SURFACE_MELONDS_DS_VERSION = f394adbacb5722ee97c1b37c8064da9a25818310
SURFACE_MELONDS_DS_SITE = $(call github,JesseTG,melonds-ds,$(SURFACE_MELONDS_DS_VERSION))
SURFACE_MELONDS_DS_LICENSE = GPL-3.0+, BSL-1.0, MIT, BSD-3-Clause, Zlib
SURFACE_MELONDS_DS_LICENSE_FILES = LICENSE
SURFACE_MELONDS_DS_SUPPORTS_IN_SOURCE_BUILD = NO
SURFACE_MELONDS_DS_EXTRA_DOWNLOADS = \
    https://codeload.github.com/JesseTG/melonDS/tar.gz/16f127dbb587a73371827ce79689c5d237c4b59b \
    https://codeload.github.com/libretro/libretro-common/tar.gz/fa8a1b572a444fcb552007019f8caf68281108aa \
    https://codeload.github.com/andoalon/embed-binaries/tar.gz/078b62beba97e8192c99bfb16d5e17220cfc7598 \
    https://codeload.github.com/g-truc/glm/tar.gz/6f14f4792a0cde5d0cf2c910506724d61cb95834 \
    https://codeload.github.com/JesseTG/libslirp-mirror/tar.gz/e61dbd459c8c06607b3a84694489427e8ec60f17 \
    https://codeload.github.com/robloach/pntr/tar.gz/a47fe167703b0ac3bfad00785b20b0dce6712509 \
    https://codeload.github.com/fmtlib/fmt/tar.gz/1be298e1bd68957e4cd352e1f676f00e07dcfb57 \
    https://codeload.github.com/yohhoy/yamc/tar.gz/4e015a7e8eb0d61c34e6928676c8c78881a72d73 \
    https://codeload.github.com/martinmoene/span-lite/tar.gz/f63fe2974943b5f555b928b10d910e457fbb93d4 \
    https://codeload.github.com/HowardHinnant/date/tar.gz/179a6b921ccd6179b5b04dce812a89f71a51b4df \
    https://codeload.github.com/madler/zlib/tar.gz/da607da739fa6047df13e66a2af6b8bec7c2a498

define SURFACE_MELONDS_DS_UNPACK_DEPENDENCIES
	mkdir -p $(@D)/deps/melonds-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/16f127dbb587a73371827ce79689c5d237c4b59b --strip-components=1 -C $(@D)/deps/melonds-src
	mkdir -p $(@D)/deps/libretro-common-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/fa8a1b572a444fcb552007019f8caf68281108aa --strip-components=1 -C $(@D)/deps/libretro-common-src
	mkdir -p $(@D)/deps/embed-binaries-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/078b62beba97e8192c99bfb16d5e17220cfc7598 --strip-components=1 -C $(@D)/deps/embed-binaries-src
	mkdir -p $(@D)/deps/glm-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/6f14f4792a0cde5d0cf2c910506724d61cb95834 --strip-components=1 -C $(@D)/deps/glm-src
	mkdir -p $(@D)/deps/libslirp-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/e61dbd459c8c06607b3a84694489427e8ec60f17 --strip-components=1 -C $(@D)/deps/libslirp-src
	mkdir -p $(@D)/deps/pntr-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/a47fe167703b0ac3bfad00785b20b0dce6712509 --strip-components=1 -C $(@D)/deps/pntr-src
	mkdir -p $(@D)/deps/fmt-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/1be298e1bd68957e4cd352e1f676f00e07dcfb57 --strip-components=1 -C $(@D)/deps/fmt-src
	mkdir -p $(@D)/deps/yamc-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/4e015a7e8eb0d61c34e6928676c8c78881a72d73 --strip-components=1 -C $(@D)/deps/yamc-src
	mkdir -p $(@D)/deps/span-lite-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/f63fe2974943b5f555b928b10d910e457fbb93d4 --strip-components=1 -C $(@D)/deps/span-lite-src
	mkdir -p $(@D)/deps/date-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/179a6b921ccd6179b5b04dce812a89f71a51b4df --strip-components=1 -C $(@D)/deps/date-src
	mkdir -p $(@D)/deps/zlib-src
	$(TAR) -xf $(SURFACE_MELONDS_DS_DL_DIR)/da607da739fa6047df13e66a2af6b8bec7c2a498 --strip-components=1 -C $(@D)/deps/zlib-src
endef
SURFACE_MELONDS_DS_POST_EXTRACT_HOOKS += SURFACE_MELONDS_DS_UNPACK_DEPENDENCIES
SURFACE_MELONDS_DS_DEPENDENCIES = host-pkgconf
SURFACE_MELONDS_DS_CONF_OPTS = -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
    -DFETCHCONTENT_UPDATES_DISCONNECTED=ON -DFETCHCONTENT_BASE_DIR=$(@D)/deps \
    -DENABLE_JIT=ON -DENABLE_OPENGL=OFF -DENABLE_COMPUTE_RENDERER=OFF \
    -DENABLE_THREADED_RENDERER=ON -DBUILD_TESTING=OFF -DENABLE_TESTING=OFF \
    -DTRACY_ENABLE=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DBUILD_SHARED_LIBS=OFF \
    -DENABLE_LTO_RELEASE=OFF -DENABLE_LTO=OFF
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_MELONDS=$(@D)/deps/melonds-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_LIBRETRO_COMMON=$(@D)/deps/libretro-common-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_LIBRETRO-COMMON=$(@D)/deps/libretro-common-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_EMBED_BINARIES=$(@D)/deps/embed-binaries-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_EMBED-BINARIES=$(@D)/deps/embed-binaries-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_GLM=$(@D)/deps/glm-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_LIBSLIRP=$(@D)/deps/libslirp-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_PNTR=$(@D)/deps/pntr-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_FMT=$(@D)/deps/fmt-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_YAMC=$(@D)/deps/yamc-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_SPAN_LITE=$(@D)/deps/span-lite-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_SPAN-LITE=$(@D)/deps/span-lite-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_DATE=$(@D)/deps/date-src
SURFACE_MELONDS_DS_CONF_OPTS += -DFETCHCONTENT_SOURCE_DIR_ZLIB=$(@D)/deps/zlib-src

define SURFACE_MELONDS_DS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(shell find $(@D)/buildroot-build -name melondsds_libretro.so -type f -print -quit) $(TARGET_DIR)/usr/lib/libretro/melondsds_libretro.so
	$(INSTALL) -D -m 0644 $(@D)/buildroot-build/melondsds-LICENSE.txt $(TARGET_DIR)/usr/share/licenses/surface-melonds-ds/LICENSE
endef
$(eval $(cmake-package))
