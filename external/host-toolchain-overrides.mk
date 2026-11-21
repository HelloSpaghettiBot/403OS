# Read via BR2_PACKAGE_OVERRIDE_FILE before Buildroot's stock LLVM recipes.
# Stock host-LLVM/Clang package names are fulfilled by the exact upstream binary
# development distribution, not by compiling the same tools a second time.
override HOST_LLVM_SOURCE =
override HOST_LLVM_DEPENDENCIES = host-surface-llvm
override HOST_LLVM_POST_INSTALL_HOOKS =
override HOST_LLVM_POST_BUILD_HOOKS =
override HOST_LLVM_LICENSE_FILES =
define HOST_LLVM_CONFIGURE_CMDS
	test -f $(HOST_DIR)/opt/surface-llvm-22.1.8/lib/libLLVMCore.a
endef
define HOST_LLVM_BUILD_CMDS
	test "$$($(HOST_DIR)/bin/llvm-config --version)" = "22.1.8"
endef
define HOST_LLVM_INSTALL_CMDS
	$(HOST_DIR)/bin/llvm-config --version
endef

override HOST_CLANG_SOURCE =
override HOST_CLANG_DEPENDENCIES = host-surface-llvm
override HOST_CLANG_POST_INSTALL_HOOKS =
override HOST_CLANG_POST_BUILD_HOOKS =
override HOST_CLANG_LICENSE_FILES =
define HOST_CLANG_CONFIGURE_CMDS
	test -f $(HOST_DIR)/opt/surface-llvm-22.1.8/lib/libclangAST.a
endef
define HOST_CLANG_BUILD_CMDS
	$(HOST_DIR)/bin/clang --version
endef
define HOST_CLANG_INSTALL_CMDS
	test -x $(HOST_DIR)/bin/clang
endef
