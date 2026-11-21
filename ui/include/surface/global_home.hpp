#pragma once
#include <array>
#include <cstdint>
#include <memory>

namespace surface {
namespace global_home_detail {
// Pure parser: accepts synthetic Linux input events and never opens devices.
class EventParser {
public:
    void setApplicationRunning(bool running);
    bool consume(std::uint16_t type, std::uint16_t code, std::int32_t value);
private:
    void clearState();
    bool neutral() const;
    std::array<bool, 5> keys_{};
    std::array<bool, 64> contacts_{};
    int slot_ = 0;
    bool running_ = false;
    bool wait_for_neutral_ = false;
    bool dropped_ = false;
    bool keyboard_latched_ = false;
    bool touch_latched_ = false;
};
}

// Image-only, read-only evdev observer. No grabs, event injection or logging.
class GlobalHomeInput {
public:
    GlobalHomeInput();
    ~GlobalHomeInput();
    GlobalHomeInput(const GlobalHomeInput&) = delete;
    GlobalHomeInput& operator=(const GlobalHomeInput&) = delete;
    bool poll(bool application_running);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
