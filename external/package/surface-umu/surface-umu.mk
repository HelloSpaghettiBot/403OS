SURFACE_UMU_VERSION = 1.4.4
SURFACE_UMU_SITE = https://github.com/Open-Wine-Components/umu-launcher/releases/download/$(SURFACE_UMU_VERSION)
SURFACE_UMU_SOURCE = umu-launcher-$(SURFACE_UMU_VERSION)-zipapp.tar
SURFACE_UMU_LICENSE = GPL-3.0, LGPL-2.1+, MIT
# All exact notices ship inside the upstream executable Python zipapp.
SURFACE_UMU_LICENSE_FILES = licenses/umu_launcher-1.4.4.dist-info/licenses/LICENSE \
    licenses/python_xlib-0.33.dist-info/LICENSE licenses/six-1.17.0.dist-info/LICENSE \
    licenses/urllib3-2.7.0.dist-info/licenses/LICENSE.txt licenses/truststore-0.10.4.dist-info/licenses/LICENSE
SURFACE_UMU_ACTUAL_SOURCE_TARBALL = $(SURFACE_UMU_VERSION).tar.gz
SURFACE_UMU_ACTUAL_SOURCE_SITE = https://github.com/Open-Wine-Components/umu-launcher/archive/refs/tags
SURFACE_UMU_STRIP_COMPONENTS = 0

define SURFACE_UMU_EXTRACT_NOTICES
	python3 $(BR2_EXTERNAL_SURFACE_PATH)/package/surface-umu/notices.py $(@D)/umu/umu-run $(@D)/licenses
endef
SURFACE_UMU_POST_EXTRACT_HOOKS += SURFACE_UMU_EXTRACT_NOTICES

define SURFACE_UMU_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/umu/umu-run $(TARGET_DIR)/usr/bin/umu-run
	$(INSTALL) -D -m 0755 $(STAGING_DIR)/sbin/ldconfig $(TARGET_DIR)/sbin/ldconfig
	mkdir -p $(TARGET_DIR)/usr/share/licenses/surface-umu
	cp -a $(@D)/licenses/. $(TARGET_DIR)/usr/share/licenses/surface-umu/
endef

define SURFACE_UMU_INSTALL_INIT_SYSV
	$(INSTALL) -D -m 0755 $(SURFACE_UMU_PKGDIR)/S23loader-cache $(TARGET_DIR)/etc/init.d/S23loader-cache
endef
$(eval $(generic-package))
