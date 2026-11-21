SURFACE_FLYCAST_VERSION = 5aa091fde632fb332c8d8c34e280d62dc951954c
SURFACE_FLYCAST_SITE = $(call github,flyinghead,flycast,$(SURFACE_FLYCAST_VERSION))
SURFACE_FLYCAST_LICENSE = GPL-2.0+, BSD-3-Clause, MIT
SURFACE_FLYCAST_LICENSE_FILES = LICENSE
SURFACE_FLYCAST_EXTRA_DOWNLOADS = \
    https://codeload.github.com/flyinghead/libchdr/tar.gz/5f82799f2c8cad1e9cd26d39a0f8d36369a5534b \
    https://codeload.github.com/flyinghead/tinygettext/tar.gz/41572a67f96013691685a38f0032f3c97aa34f79 \
    https://codeload.github.com/Grumbel/tinycmmc/tar.gz/9a51e13802d930feb7261ba8876940659b258cb7 \
    https://codeload.github.com/flyinghead/asio/tar.gz/d3402006e84efb6114ff93e4f2b8508412ed80d5 \
    https://codeload.github.com/herumi/xbyak/tar.gz/0d67fd1530016b7c56f3cd74b3fca920f4c3e2b4

define SURFACE_FLYCAST_UNPACK_DEPENDENCIES
	mkdir -p $(@D)/core/deps/libchdr
	$(TAR) -xf $(SURFACE_FLYCAST_DL_DIR)/5f82799f2c8cad1e9cd26d39a0f8d36369a5534b --strip-components=1 -C $(@D)/core/deps/libchdr
	mkdir -p $(@D)/core/deps/tinygettext
	$(TAR) -xf $(SURFACE_FLYCAST_DL_DIR)/41572a67f96013691685a38f0032f3c97aa34f79 --strip-components=1 -C $(@D)/core/deps/tinygettext
	mkdir -p $(@D)/core/deps/tinygettext/external/tinycmmc
	$(TAR) -xf $(SURFACE_FLYCAST_DL_DIR)/9a51e13802d930feb7261ba8876940659b258cb7 --strip-components=1 -C $(@D)/core/deps/tinygettext/external/tinycmmc
	mkdir -p $(@D)/core/deps/asio
	$(TAR) -xf $(SURFACE_FLYCAST_DL_DIR)/d3402006e84efb6114ff93e4f2b8508412ed80d5 --strip-components=1 -C $(@D)/core/deps/asio
	mkdir -p $(@D)/core/deps/xbyak
	$(TAR) -xf $(SURFACE_FLYCAST_DL_DIR)/0d67fd1530016b7c56f3cd74b3fca920f4c3e2b4 --strip-components=1 -C $(@D)/core/deps/xbyak
endef
SURFACE_FLYCAST_POST_EXTRACT_HOOKS += SURFACE_FLYCAST_UNPACK_DEPENDENCIES
SURFACE_FLYCAST_DEPENDENCIES = libgles libzip libminiupnpc host-pkgconf
SURFACE_FLYCAST_CONF_OPTS = -DLIBRETRO=ON -DUSE_GLES=ON -DUSE_GLES2=OFF \
    -DUSE_VULKAN=OFF -DUSE_DX9=OFF -DUSE_DX11=OFF -DUSE_OPENMP=OFF \
    -DUSE_LUA=OFF -DUSE_BREAKPAD=OFF -DUSE_DISCORD=OFF -DUSE_LIBCDIO=OFF \
    -DUSE_HOST_LIBZIP=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DENABLE_CTEST=OFF -DTEST_AUTOMATION=OFF -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF

define SURFACE_FLYCAST_INSTALL_LICENSE
	$(INSTALL) -D -m 0644 $(@D)/LICENSE $(TARGET_DIR)/usr/share/licenses/surface-flycast/LICENSE
endef
SURFACE_FLYCAST_POST_INSTALL_TARGET_HOOKS += SURFACE_FLYCAST_INSTALL_LICENSE
$(eval $(cmake-package))
