cmake_minimum_required(VERSION 3.18)
project(surface_rotation VERSION 0.1.0 LANGUAGES C)

find_package(PkgConfig REQUIRED)
pkg_check_modules(WESTON REQUIRED IMPORTED_TARGET
    libweston-15 wayland-server pixman-1 xkbcommon)

add_library(surface-rotation MODULE weston-rotation.c)
set_target_properties(surface-rotation PROPERTIES PREFIX "")
target_compile_features(surface-rotation PRIVATE c_std_11)
target_compile_options(surface-rotation PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(surface-rotation PRIVATE PkgConfig::WESTON)
install(TARGETS surface-rotation LIBRARY DESTINATION lib/weston)
