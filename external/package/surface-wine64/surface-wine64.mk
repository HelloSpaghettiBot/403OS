################################################################################
# Wine's stock Buildroot target is restricted to i386. This package keeps
# its pinned upstream release and host tools, and builds a pure AMD64 target.
################################################################################

SURFACE_WINE64_VERSION = 11.0
SURFACE_WINE64_SOURCE = wine-$(SURFACE_WINE64_VERSION).tar.xz
SURFACE_WINE64_SITE = https://dl.winehq.org/wine/source/11.0
SURFACE_WINE64_DL_SUBDIR = wine
SURFACE_WINE64_LICENSE = LGPL-2.1+
SURFACE_WINE64_LICENSE_FILES = COPYING.LIB LICENSE
SURFACE_WINE64_DEPENDENCIES = host-bison host-flex host-wine host-pkgconf \
    alsa-lib fontconfig freetype gnutls libusb sdl2 udev libgl vulkan-headers vulkan-loader \
    xlib_libX11 xlib_libXcomposite xlib_libXcursor xlib_libXext xlib_libXfixes \
    xlib_libXi xlib_libXinerama xlib_libXrandr xlib_libXrender xlib_libXxf86vm
SURFACE_WINE64_CONF_OPTS = \
    --with-wine-tools=$(BUILD_DIR)/host-wine-$(SURFACE_WINE64_VERSION) \
    --enable-win64 --disable-win16 --disable-tests --enable-tools --enable-nls \
    --with-x --with-opengl --with-alsa --with-fontconfig --with-freetype \
    --with-gnutls --with-usb --with-sdl --with-udev \
    --with-xcomposite --with-xcursor --with-xfixes --with-xinerama \
    --with-xinput --with-xinput2 --with-xrandr --with-xrender \
    --with-xshape --with-xshm --with-xxf86vm \
    --without-mingw --with-vulkan --without-wayland --without-ffmpeg \
    --without-capi --without-coreaudio --without-cups --without-dbus \
    --without-gettext --without-gettextpo --without-gphoto --without-gssapi \
    --without-gstreamer --without-krb5 --without-netapi --without-opencl \
    --without-oss --without-pcap --without-pcsclite --without-pulse \
    --without-sane --without-v4l2

# Buildroot's generic --disable-nls also disables Wine's mandatory Unicode
# tables, not just translations. The explicit --enable-nls above overrides it;
# --without-gettext/--without-gettextpo still avoid translation dependencies.

# Match Buildroot's Wine wrapper handling for Bootlin/external toolchains.
ifeq ($(BR2_TOOLCHAIN_EXTERNAL),y)
SURFACE_WINE64_CONF_OPTS += TARGETFLAGS="-b $(TOOLCHAIN_EXTERNAL_PREFIX)"
endif

define SURFACE_WINE64_INSTALL_LICENSE
	$(INSTALL) -D -m 0644 $(@D)/COPYING.LIB $(TARGET_DIR)/usr/share/licenses/surface-wine64/COPYING.LIB
	$(INSTALL) -D -m 0644 $(@D)/LICENSE $(TARGET_DIR)/usr/share/licenses/surface-wine64/LICENSE
endef
SURFACE_WINE64_POST_INSTALL_TARGET_HOOKS += SURFACE_WINE64_INSTALL_LICENSE

$(eval $(autotools-package))
