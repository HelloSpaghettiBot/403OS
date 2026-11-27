// Standalone diagnostic for replaying a real HID descriptor after SDL starts.
// This observes the SDL Linux input path; it does not emulate Bluetooth radio
// transport or claim that a physical controller has passed hardware testing.
#include <SDL3/SDL.h>
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include <vector>

namespace {
struct Device {
    SDL_Joystick* joystick = nullptr;
    SDL_Gamepad* gamepad = nullptr;
};

std::string printable(const char* value) {
    std::string result = value ? value : "";
    for (char& character : result) {
        const auto byte = static_cast<unsigned char>(character);
        if (byte < 32 || byte == 127 || character == '"' || character == '\\') character = '_';
    }
    if (result.size() > 160) result.resize(160);
    return result;
}

bool number(const char* value, unsigned& result, int base, unsigned maximum) {
    errno = 0;
    char* end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, base);
    if (errno || end == value || *end || parsed > maximum) return false;
    result = static_cast<unsigned>(parsed);
    return true;
}
}

int main(int argc, char** argv) {
    unsigned seconds = 20, vendor = 0x1949, product = 0x0402;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        unsigned* destination = argument == "--seconds" ? &seconds :
            argument == "--vendor" ? &vendor : argument == "--product" ? &product : nullptr;
        if (!destination || ++index == argc ||
            !number(argv[index], *destination, argument == "--seconds" ? 10 : 16,
                    argument == "--seconds" ? 120 : 0xffff) || !seconds) {
            std::fprintf(stderr, "Usage: %s [--seconds 1..120] [--vendor hex] [--product hex]\n", argv[0]);
            return 2;
        }
    }

    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (!SDL_Init(SDL_INIT_GAMEPAD)) {
        std::fprintf(stderr, "SURFACE_CONTROLLER_HOTPLUG_ERROR init=\"%s\"\n", SDL_GetError());
        return 1;
    }
    SDL_AddGamepadMappingsFromFile("/usr/share/surface/gamecontrollerdb.txt");
    SDL_AddGamepadMappingsFromFile("/data/config/gamecontrollerdb.txt");
    if (const auto* personal = std::getenv("SDL_GAMECONTROLLERCONFIG_FILE"))
        SDL_AddGamepadMappingsFromFile(personal);

    std::map<SDL_JoystickID, Device> devices;
    bool present_at_start = false, seen_after_start = false;
    bool raw_button = false, raw_axis = false, raw_hat = false;
    bool mapped_face = false, mapped_dpad = false, mapped_stick = false;
    unsigned raw_events = 0, mapped_events = 0;
    const auto started = SDL_GetTicks();
    auto inventory = [&](bool initial) {
        int count = 0;
        auto* ids = SDL_GetJoysticks(&count);
        std::vector<SDL_JoystickID> present;
        std::printf("SURFACE_CONTROLLER_INVENTORY elapsed_ms=%llu count=%d\n",
                    static_cast<unsigned long long>(SDL_GetTicks() - started), count);
        for (int index = 0; ids && index < count; ++index) {
            const auto id = ids[index];
            const auto device_vendor = SDL_GetJoystickVendorForID(id);
            const auto device_product = SDL_GetJoystickProductForID(id);
            const bool target = device_vendor == vendor && device_product == product;
            const bool mapped = SDL_IsGamepad(id);
            char guid[33]{};
            SDL_GUIDToString(SDL_GetJoystickGUIDForID(id), guid, sizeof(guid));
            Device* device = nullptr;
            if (target) {
                present.push_back(id);
                if (initial) present_at_start = true;
                else seen_after_start = true;
                device = &devices[id];
                if (!device->joystick) device->joystick = SDL_OpenJoystick(id);
                if (mapped && !device->gamepad) device->gamepad = SDL_OpenGamepad(id);
            }
            std::printf("SURFACE_CONTROLLER_DEVICE id=%u target=%d vid=%04x pid=%04x guid=%s "
                        "mapped=%d joystick_open=%d gamepad_open=%d buttons=%d axes=%d hats=%d "
                        "name=\"%s\" path=\"%s\" error=\"%s\"\n",
                        id, target, device_vendor, device_product, guid, mapped,
                        device && device->joystick, device && device->gamepad,
                        device && device->joystick ? SDL_GetNumJoystickButtons(device->joystick) : -1,
                        device && device->joystick ? SDL_GetNumJoystickAxes(device->joystick) : -1,
                        device && device->joystick ? SDL_GetNumJoystickHats(device->joystick) : -1,
                        printable(SDL_GetJoystickNameForID(id)).c_str(),
                        printable(SDL_GetJoystickPathForID(id)).c_str(),
                        printable(device && (!device->joystick || (mapped && !device->gamepad)) ? SDL_GetError() : "").c_str());
        }
        SDL_free(ids);
        for (auto iterator = devices.begin(); iterator != devices.end();) {
            if (std::find(present.begin(), present.end(), iterator->first) == present.end()) {
                if (iterator->second.gamepad) SDL_CloseGamepad(iterator->second.gamepad);
                if (iterator->second.joystick) SDL_CloseJoystick(iterator->second.joystick);
                iterator = devices.erase(iterator);
            } else ++iterator;
        }
        std::fflush(stdout);
    };
    inventory(true);
    std::printf("SURFACE_CONTROLLER_HOTPLUG_READY vendor=%04x product=%04x seconds=%u present_at_start=%d\n",
                vendor, product, seconds, present_at_start);
    std::fflush(stdout);
    auto next_inventory = started + 2000;
    while (SDL_GetTicks() - started < seconds * 1000ULL) {
        SDL_Event event{};
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_EVENT_JOYSTICK_ADDED || event.type == SDL_EVENT_JOYSTICK_REMOVED ||
                event.type == SDL_EVENT_GAMEPAD_ADDED || event.type == SDL_EVENT_GAMEPAD_REMAPPED ||
                event.type == SDL_EVENT_GAMEPAD_REMOVED) {
                std::printf("SURFACE_CONTROLLER_LIFECYCLE type=%u id=%u\n", event.type, event.jdevice.which);
                inventory(false);
            }
            if (event.type == SDL_EVENT_JOYSTICK_BUTTON_DOWN && devices.contains(event.jbutton.which)) {
                raw_button = true;
                if (++raw_events <= 64) std::printf("SURFACE_CONTROLLER_RAW button=%u id=%u\n", event.jbutton.button, event.jbutton.which);
            }
            if (event.type == SDL_EVENT_JOYSTICK_AXIS_MOTION && devices.contains(event.jaxis.which)) {
                if (std::abs(static_cast<int>(event.jaxis.value)) > 16000) raw_axis = true;
                if (++raw_events <= 64) std::printf("SURFACE_CONTROLLER_RAW axis=%u value=%d id=%u\n", event.jaxis.axis, event.jaxis.value, event.jaxis.which);
            }
            if (event.type == SDL_EVENT_JOYSTICK_HAT_MOTION && devices.contains(event.jhat.which)) {
                if (event.jhat.value != SDL_HAT_CENTERED) raw_hat = true;
                if (++raw_events <= 64) std::printf("SURFACE_CONTROLLER_RAW hat=%u value=%u id=%u\n", event.jhat.hat, event.jhat.value, event.jhat.which);
            }
            if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN && devices.contains(event.gbutton.which)) {
                if (event.gbutton.button <= SDL_GAMEPAD_BUTTON_NORTH) mapped_face = true;
                if (event.gbutton.button >= SDL_GAMEPAD_BUTTON_DPAD_UP && event.gbutton.button <= SDL_GAMEPAD_BUTTON_DPAD_RIGHT)
                    mapped_dpad = true;
                if (++mapped_events <= 64) std::printf("SURFACE_CONTROLLER_MAPPED button=%u id=%u\n", event.gbutton.button, event.gbutton.which);
            }
            if (event.type == SDL_EVENT_GAMEPAD_AXIS_MOTION && devices.contains(event.gaxis.which)) {
                if (event.gaxis.axis <= SDL_GAMEPAD_AXIS_LEFTY && std::abs(static_cast<int>(event.gaxis.value)) > 16000)
                    mapped_stick = true;
                if (++mapped_events <= 64) std::printf("SURFACE_CONTROLLER_MAPPED axis=%u value=%d id=%u\n", event.gaxis.axis, event.gaxis.value, event.gaxis.which);
            }
        }
        if (SDL_GetTicks() >= next_inventory) {
            inventory(false);
            next_inventory = SDL_GetTicks() + 2000;
        }
        std::fflush(stdout);
        SDL_Delay(10);
    }
    const bool passed = !present_at_start && seen_after_start && raw_button && raw_axis &&
                        mapped_face && mapped_dpad && mapped_stick;
    std::printf("SURFACE_CONTROLLER_HOTPLUG_%s present_at_start=%d hotplug_visible=%d "
                "raw_button=%d raw_axis=%d raw_hat=%d mapped_face=%d mapped_dpad=%d mapped_stick=%d\n",
                passed ? "PASS" : "FAIL", present_at_start, seen_after_start,
                raw_button, raw_axis, raw_hat, mapped_face, mapped_dpad, mapped_stick);
    for (auto& [id, device] : devices) {
        (void)id;
        if (device.gamepad) SDL_CloseGamepad(device.gamepad);
        if (device.joystick) SDL_CloseJoystick(device.joystick);
    }
    SDL_Quit();
    return passed ? 0 : 1;
}
