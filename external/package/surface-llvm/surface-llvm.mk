################################################################################
# Official, isolated host LLVM/Clang development tools for Mesa Iris precompilation.
################################################################################
SURFACE_LLVM_VERSION = 22.1.8
SURFACE_LLVM_SOURCE = LLVM-$(SURFACE_LLVM_VERSION)-Linux-X64.tar.xz
SURFACE_LLVM_SITE = https://github.com/llvm/llvm-project/releases/download/llvmorg-$(SURFACE_LLVM_VERSION)
SURFACE_LLVM_DL_SUBDIR = llvm-prebuilt-$(SURFACE_LLVM_VERSION)
SURFACE_LLVM_LICENSE = Apache-2.0 with LLVM exceptions
SURFACE_LLVM_LICENSE_FILES = LICENSE.TXT
SURFACE_LLVM_ACTUAL_SOURCE_TARBALL = llvm-project-$(SURFACE_LLVM_VERSION).src.tar.xz
SURFACE_LLVM_ACTUAL_SOURCE_SITE = $(SURFACE_LLVM_SITE)
SURFACE_LLVM_EXTRA_DOWNLOADS = https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-$(SURFACE_LLVM_VERSION)/LICENSE.TXT
HOST_SURFACE_LLVM_DEPENDENCIES = host-libxml2 host-zlib host-zstd
SURFACE_LLVM_PREFIX = $(HOST_DIR)/opt/surface-llvm-$(SURFACE_LLVM_VERSION)
SURFACE_LLVM_CACHE = $(BASE_DIR)/llvm-selected-cache

define HOST_SURFACE_LLVM_EXTRACT_CMDS
	python3 $(BR2_EXTERNAL_SURFACE_PATH)/package/surface-llvm/prepare.py \
		$(HOST_SURFACE_LLVM_DL_DIR)/$(SURFACE_LLVM_SOURCE) $(SURFACE_LLVM_CACHE)
	cp $(HOST_SURFACE_LLVM_DL_DIR)/LICENSE.TXT $(@D)/LICENSE.TXT
endef

define HOST_SURFACE_LLVM_INSTALL_CMDS
	mkdir -p $(SURFACE_LLVM_PREFIX)
	cp -al $(SURFACE_LLVM_CACHE)/. $(SURFACE_LLVM_PREFIX)/
	$(INSTALL) -m 0644 $(@D)/LICENSE.TXT $(SURFACE_LLVM_PREFIX)/LICENSE.TXT
	$(BR2_EXTERNAL_SURFACE_PATH)/package/surface-llvm/install-links.sh $(HOST_DIR) $(SURFACE_LLVM_PREFIX)
	test "$$($(SURFACE_LLVM_PREFIX)/bin/llvm-config --version)" = "$(SURFACE_LLVM_VERSION)"
	$(SURFACE_LLVM_PREFIX)/bin/clang --version
endef
$(eval $(host-generic-package))
# The release contains static LLVM and Clang components, with RTTI disabled.
HOST_MESA3D_CONF_OPTS += -Dshared-llvm=disabled
# A shared SPIR-V library plus a statically linked LLVM tool registers LLVM's
# global options twice. Keep the translator static with this LLVM distribution.
HOST_SPIRV_LLVM_TRANSLATOR_CONF_OPTS += -DBUILD_SHARED_LIBS=OFF
