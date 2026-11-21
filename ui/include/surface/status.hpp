#pragma once
#include <filesystem>
#include <chrono>
#include <string_view>
#include <string>
namespace surface {
struct Status {
    std::string network = "Network unavailable";
    std::string bluetooth = "BT unavailable";
    std::string battery = "Battery unavailable";
    std::string vpn = "VPN unavailable";
    std::string time;
    bool vpn_connected = false;
};
struct VpnStatus {
    std::string text = "Tailscale unavailable";
    bool connected = false;
};
VpnStatus parse_tailscale_status(std::string_view json);
VpnStatus read_tailscale_status(const std::filesystem::path& executable = "/usr/bin/tailscale",
                              std::chrono::milliseconds timeout = std::chrono::milliseconds(800));
Status read_status(const std::filesystem::path& sys = "/sys");
}
