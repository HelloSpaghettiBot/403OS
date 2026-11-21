SURFACE_DOLPHIN_VERSION = 2fe162866282fa2806d62f69aa053315d750ab91
SURFACE_DOLPHIN_SITE = $(call github,dolphin-emu,dolphin,$(SURFACE_DOLPHIN_VERSION))
SURFACE_DOLPHIN_LICENSE = GPL-3.0+
SURFACE_DOLPHIN_LICENSE_FILES = COPYING LICENSES/GPL-2.0-or-later.txt LICENSES/MIT.txt LICENSES/BSD-3-Clause.txt LICENSES/CC0-1.0.txt
SURFACE_DOLPHIN_EXTRA_DOWNLOADS = \
    https://codeload.github.com/SFML/SFML/tar.gz/0fa201c969e48ecc253581c5841ce73f44d42f49 \
    https://codeload.github.com/KhronosGroup/Vulkan-Headers/tar.gz/39f924b810e561fd86b2558b6711ca68d4363f68 \
    https://codeload.github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator/tar.gz/3bab6924988e5f19bf36586a496156cf72f70d9f \
    https://codeload.github.com/mutouyun/cpp-ipc/tar.gz/b6cbbc940e25ff898f09a40b107781cef6027b13 \
    https://codeload.github.com/weisslj/cpp-optparse/tar.gz/2265d647232249a53a03b411099863ceca35f0d3 \
    https://codeload.github.com/curl/curl/tar.gz/cfbfb65047e85e6b08af65fe9cdbcf68e9ad496a \
    https://codeload.github.com/lsalzman/enet/tar.gz/2662c0de09e36f2a2030ccc2c528a3e4c9e8138a \
    https://codeload.github.com/fmtlib/fmt/tar.gz/e424e3f2e607da02742f73db84873b8084fc714c \
    https://codeload.github.com/KhronosGroup/glslang/tar.gz/a57276bf558f5cf94d3a9854ebdf5a2236849a5a \
    https://codeload.github.com/libusb/hidapi/tar.gz/d6b2a974608dec3b76fb1e36c189f22b9cf3650c \
    https://codeload.github.com/ocornut/imgui/tar.gz/45acd5e0e82f4c954432533ae9985ff0e1aad6d5 \
    https://codeload.github.com/epezent/implot/tar.gz/3da8bd34299965d3b0ab124df743fe3e076fa222 \
    https://codeload.github.com/randy408/libspng/tar.gz/fb768002d4288590083a476af628e51c3f1d47cd \
    https://codeload.github.com/libusb/libusb/tar.gz/15a7ebb4d426c5ce196684347d2b7cafad862626 \
    https://codeload.github.com/lz4/lz4/tar.gz/5fc0630a0ed55e9755b0fe3990cd021ae1c3edc1 \
    https://codeload.github.com/zlib-ng/minizip-ng/tar.gz/55db144e03027b43263e5ebcb599bf0878ba58de \
    https://codeload.github.com/zeux/pugixml/tar.gz/ee86beb30e4973f5feffe3ce63bfa4fbadf72f38 \
    https://codeload.github.com/syoyo/tinygltf/tar.gz/c5641f2c22d117da7971504591a8f6a41ece488b \
    https://codeload.github.com/e-dant/watcher/tar.gz/b03bdcfc11549df595b77239cefe2643943a3e2f \
    https://codeload.github.com/Cyan4973/xxHash/tar.gz/e626a72bc2321cd320e953a0ccf1584cad60f363 \
    https://codeload.github.com/zlib-ng/zlib-ng/tar.gz/ce01b1e41da298334f8214389cc9369540a7560f \
    https://codeload.github.com/facebook/zstd/tar.gz/711e17da98510a3567bf47f85a08a76f64811474

define SURFACE_DOLPHIN_UNPACK_DEPENDENCIES
	mkdir -p $(@D)/Externals/SFML/SFML
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/0fa201c969e48ecc253581c5841ce73f44d42f49 --strip-components=1 -C $(@D)/Externals/SFML/SFML
	mkdir -p $(@D)/Externals/Vulkan-Headers
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/39f924b810e561fd86b2558b6711ca68d4363f68 --strip-components=1 -C $(@D)/Externals/Vulkan-Headers
	mkdir -p $(@D)/Externals/VulkanMemoryAllocator
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/3bab6924988e5f19bf36586a496156cf72f70d9f --strip-components=1 -C $(@D)/Externals/VulkanMemoryAllocator
	mkdir -p $(@D)/Externals/cpp-ipc/cpp-ipc
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/b6cbbc940e25ff898f09a40b107781cef6027b13 --strip-components=1 -C $(@D)/Externals/cpp-ipc/cpp-ipc
	mkdir -p $(@D)/Externals/cpp-optparse/cpp-optparse
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/2265d647232249a53a03b411099863ceca35f0d3 --strip-components=1 -C $(@D)/Externals/cpp-optparse/cpp-optparse
	mkdir -p $(@D)/Externals/curl/curl
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/cfbfb65047e85e6b08af65fe9cdbcf68e9ad496a --strip-components=1 -C $(@D)/Externals/curl/curl
	mkdir -p $(@D)/Externals/enet/enet
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/2662c0de09e36f2a2030ccc2c528a3e4c9e8138a --strip-components=1 -C $(@D)/Externals/enet/enet
	mkdir -p $(@D)/Externals/fmt/fmt
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/e424e3f2e607da02742f73db84873b8084fc714c --strip-components=1 -C $(@D)/Externals/fmt/fmt
	mkdir -p $(@D)/Externals/glslang/glslang
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/a57276bf558f5cf94d3a9854ebdf5a2236849a5a --strip-components=1 -C $(@D)/Externals/glslang/glslang
	mkdir -p $(@D)/Externals/hidapi/hidapi-src
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/d6b2a974608dec3b76fb1e36c189f22b9cf3650c --strip-components=1 -C $(@D)/Externals/hidapi/hidapi-src
	mkdir -p $(@D)/Externals/imgui/imgui
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/45acd5e0e82f4c954432533ae9985ff0e1aad6d5 --strip-components=1 -C $(@D)/Externals/imgui/imgui
	mkdir -p $(@D)/Externals/implot/implot
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/3da8bd34299965d3b0ab124df743fe3e076fa222 --strip-components=1 -C $(@D)/Externals/implot/implot
	mkdir -p $(@D)/Externals/libspng/libspng
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/fb768002d4288590083a476af628e51c3f1d47cd --strip-components=1 -C $(@D)/Externals/libspng/libspng
	mkdir -p $(@D)/Externals/libusb/libusb
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/15a7ebb4d426c5ce196684347d2b7cafad862626 --strip-components=1 -C $(@D)/Externals/libusb/libusb
	mkdir -p $(@D)/Externals/lz4/lz4
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/5fc0630a0ed55e9755b0fe3990cd021ae1c3edc1 --strip-components=1 -C $(@D)/Externals/lz4/lz4
	mkdir -p $(@D)/Externals/minizip-ng/minizip-ng
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/55db144e03027b43263e5ebcb599bf0878ba58de --strip-components=1 -C $(@D)/Externals/minizip-ng/minizip-ng
	mkdir -p $(@D)/Externals/pugixml/pugixml
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/ee86beb30e4973f5feffe3ce63bfa4fbadf72f38 --strip-components=1 -C $(@D)/Externals/pugixml/pugixml
	mkdir -p $(@D)/Externals/tinygltf/tinygltf
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/c5641f2c22d117da7971504591a8f6a41ece488b --strip-components=1 -C $(@D)/Externals/tinygltf/tinygltf
	mkdir -p $(@D)/Externals/watcher/watcher
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/b03bdcfc11549df595b77239cefe2643943a3e2f --strip-components=1 -C $(@D)/Externals/watcher/watcher
	mkdir -p $(@D)/Externals/xxhash/xxHash
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/e626a72bc2321cd320e953a0ccf1584cad60f363 --strip-components=1 -C $(@D)/Externals/xxhash/xxHash
	mkdir -p $(@D)/Externals/zlib-ng/zlib-ng
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/ce01b1e41da298334f8214389cc9369540a7560f --strip-components=1 -C $(@D)/Externals/zlib-ng/zlib-ng
	mkdir -p $(@D)/Externals/zstd/zstd
	$(TAR) -xf $(SURFACE_DOLPHIN_DL_DIR)/711e17da98510a3567bf47f85a08a76f64811474 --strip-components=1 -C $(@D)/Externals/zstd/zstd
endef
SURFACE_DOLPHIN_POST_EXTRACT_HOOKS += SURFACE_DOLPHIN_UNPACK_DEPENDENCIES
SURFACE_DOLPHIN_DEPENDENCIES = surface-sdl3 alsa-lib udev libevdev libusb \
    libcurl bzip2 libegl libgl xlib_libX11 xlib_libXi xlib_libXrandr host-pkgconf host-python3
SURFACE_DOLPHIN_CONF_OPTS = -DENABLE_QT=OFF -DENABLE_NOGUI=ON -DENABLE_X11=ON \
    -DENABLE_EGL=ON -DENABLE_VULKAN=OFF -DENABLE_TESTS=OFF -DENABLE_LTO=OFF \
    -DENABLE_ALSA=ON -DENABLE_PULSEAUDIO=OFF -DENABLE_CUBEB=OFF \
    -DENABLE_SDL=ON -DENABLE_EVDEV=ON -DENABLE_HWDB=ON -DENABLE_LLVM=OFF \
    -DUSE_UPNP=OFF -DUSE_MGBA=OFF -DUSE_DISCORD_PRESENCE=OFF \
    -DUSE_RETRO_ACHIEVEMENTS=OFF -DENABLE_AUTOUPDATE=OFF -DENABLE_ANALYTICS=OFF \
    -DENCODE_FRAMEDUMPS=OFF -DENABLE_CLI_TOOL=OFF -DUSE_SYSTEM_LIBS=AUTO \
    -DUSE_SYSTEM_SDL3=ON -DUSE_SYSTEM_BZIP2=ON -DUSE_SYSTEM_LIBUSB=ON \
    -DUSE_SYSTEM_CURL=ON -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF

define SURFACE_DOLPHIN_INSTALL_LICENSE
	$(INSTALL) -D -m 0644 $(@D)/COPYING $(TARGET_DIR)/usr/share/licenses/surface-dolphin/COPYING
	cp -a $(@D)/LICENSES $(TARGET_DIR)/usr/share/licenses/surface-dolphin/
endef
SURFACE_DOLPHIN_POST_INSTALL_TARGET_HOOKS += SURFACE_DOLPHIN_INSTALL_LICENSE
$(eval $(cmake-package))
