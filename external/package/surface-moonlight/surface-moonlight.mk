SURFACE_MOONLIGHT_VERSION = 2.7.1
SURFACE_MOONLIGHT_SITE = https://github.com/moonlight-stream/moonlight-embedded/releases/download/v$(SURFACE_MOONLIGHT_VERSION)
SURFACE_MOONLIGHT_SOURCE = moonlight-embedded-$(SURFACE_MOONLIGHT_VERSION).tar.xz
# The upstream release asset is flat and includes all pinned submodule sources.
SURFACE_MOONLIGHT_STRIP_COMPONENTS = 0
SURFACE_MOONLIGHT_LICENSE = GPL-3.0+, LGPL-2.1+, MIT, Zlib
SURFACE_MOONLIGHT_LICENSE_FILES = LICENSE third_party/moonlight-common-c/LICENSE.txt \
	third_party/h264bitstream/LICENSE third_party/moonlight-common-c/enet/LICENSE \
	third_party/SDL_GameControllerDB/LICENSE
SURFACE_MOONLIGHT_DEPENDENCIES = host-pkgconf alsa-lib avahi dbus expat ffmpeg \
	libcurl libevdev openssl opus sdl2 udev util-linux-libs
SURFACE_MOONLIGHT_CONF_OPTS = -DENABLE_SDL=ON -DENABLE_FFMPEG=ON \
	-DENABLE_X11=OFF -DENABLE_CEC=OFF -DENABLE_PULSE=OFF -DBUILD_DOCS=OFF

define SURFACE_MOONLIGHT_INSTALL_LICENSES
	$(INSTALL) -D -m 0644 $(@D)/LICENSE $(TARGET_DIR)/usr/share/licenses/moonlight/LICENSE
	$(INSTALL) -D -m 0644 $(@D)/third_party/moonlight-common-c/LICENSE.txt $(TARGET_DIR)/usr/share/licenses/moonlight/moonlight-common-c-LICENSE
	$(INSTALL) -D -m 0644 $(@D)/third_party/h264bitstream/LICENSE $(TARGET_DIR)/usr/share/licenses/moonlight/h264bitstream-LICENSE
	$(INSTALL) -D -m 0644 $(@D)/third_party/moonlight-common-c/enet/LICENSE $(TARGET_DIR)/usr/share/licenses/moonlight/enet-LICENSE
	$(INSTALL) -D -m 0644 $(@D)/third_party/SDL_GameControllerDB/LICENSE $(TARGET_DIR)/usr/share/licenses/moonlight/gamecontrollerdb-LICENSE
endef
SURFACE_MOONLIGHT_POST_INSTALL_TARGET_HOOKS += SURFACE_MOONLIGHT_INSTALL_LICENSES

$(eval $(cmake-package))
