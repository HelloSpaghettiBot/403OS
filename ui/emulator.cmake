# Native, deliberately small libretro frontend; cores remain separate shared libraries.
add_executable(surface-emulator src/emulator.cpp)
target_include_directories(surface-emulator PRIVATE include)
target_link_libraries(surface-emulator PRIVATE SDL3::SDL3 ${CMAKE_DL_LIBS} Threads::Threads)
target_compile_options(surface-emulator PRIVATE -Wall -Wextra -Wpedantic -Werror)
install(TARGETS surface-emulator RUNTIME DESTINATION bin)
