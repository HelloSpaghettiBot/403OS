#pragma once
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace surface {
struct ControllerMapping {
    std::string guid, name;
    std::vector<std::pair<std::string,std::string>> fields;
    static std::optional<ControllerMapping> parse(std::string_view line);
    std::string binding(std::string_view key) const;
    void set(std::string key,std::string value);
    std::string line() const;
};
enum class ControllerInput { Button, Stick, Trigger };
// A stick prompt always asks for its positive direction (right or down).
std::optional<std::string> controller_axis_binding(ControllerInput target,int axis,int rest,int value);
bool controller_image_allowed();
// Atomic replacement preserves all other GUIDs and comments. The caller must
// enforce the image guard; a separate path makes this operation fixture-testable.
void save_controller_mapping(const std::filesystem::path& path,const ControllerMapping& mapping);
}
