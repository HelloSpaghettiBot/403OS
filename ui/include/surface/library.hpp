#pragma once
#include "surface/model.hpp"
#include <filesystem>
namespace surface {
struct Library {
    std::vector<App> games;
    std::vector<App> systems;
};
Library read_library(const std::filesystem::path& catalog);
}
