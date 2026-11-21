#include "surface/global_home.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace surface {
namespace global_home_detail {
void EventParser::clearState() {
    keys_.fill(false);
    contacts_.fill(false);
    slot_ = 0;
    keyboard_latched_ = false;
    touch_latched_ = false;
}

bool EventParser::neutral() const {
    return std::none_of(keys_.begin(), keys_.end(), [](bool down) { return down; }) &&
           std::none_of(contacts_.begin(), contacts_.end(), [](bool down) { return down; });
}

void EventParser::setApplicationRunning(bool running) {
    if (running_ == running) return;
    running_ = running;
    wait_for_neutral_ = !neutral();
    keyboard_latched_ = touch_latched_ = false;
}

bool EventParser::consume(std::uint16_t type, std::uint16_t code, std::int32_t value) {
    if (type == EV_SYN && code == SYN_DROPPED) {
        clearState();
        dropped_ = true;
        wait_for_neutral_ = true;
        return false;
    }
    if (dropped_) {
        if (type == EV_SYN && code == SYN_REPORT) dropped_ = false;
        return false;
    }
    if (type == EV_KEY && (value == 0 || value == 1)) {
        int key = -1;
        switch (code) {
            case KEY_LEFTCTRL: key = 0; break;
            case KEY_RIGHTCTRL: key = 1; break;
            case KEY_LEFTALT: key = 2; break;
            case KEY_RIGHTALT: key = 3; break;
            case KEY_HOME: key = 4; break;
            default: break;
        }
        if (key >= 0) keys_[static_cast<unsigned>(key)] = value == 1;
    } else if (type == EV_ABS) {
        if (code == ABS_MT_SLOT)
            slot_ = value >= 0 && value < static_cast<int>(contacts_.size()) ? value : -1;
        else if (code == ABS_MT_TRACKING_ID && slot_ >= 0 && value >= -1)
            contacts_[static_cast<unsigned>(slot_)] = value >= 0;
    }
    if (type != EV_SYN || code != SYN_REPORT) return false;
    if (!running_) return false;
    if (wait_for_neutral_) {
        if (neutral()) wait_for_neutral_ = false;
        return false;
    }
    const bool chord = (keys_[0] || keys_[1]) && (keys_[2] || keys_[3]) && keys_[4];
    const auto contacts = std::count(contacts_.begin(), contacts_.end(), true);
    bool trigger = false;
    if (!chord) keyboard_latched_ = false;
    else if (!keyboard_latched_) {
        keyboard_latched_ = true;
        trigger = true;
    }
    if (contacts == 0) touch_latched_ = false;
    else if (contacts >= 3 && !touch_latched_) {
        touch_latched_ = true;
        trigger = true;
    }
    return trigger;
}
}

namespace {
bool isImage() {
    const int fd = open("/etc/surface-image-id", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat info{};
    char marker[129]{};
    bool valid = fstat(fd, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0 &&
                 (info.st_mode & 0022) == 0 && info.st_size > 0 && info.st_size <= 128;
    if (valid) {
        const auto count = read(fd, marker, sizeof(marker) - 1);
        valid = count == info.st_size && count >= 11 && std::memcmp(marker, "surface-go-", 11) == 0;
    }
    close(fd);
    return valid;
}

template<std::size_t N> bool bit(const std::array<unsigned long, N>& bits, unsigned code) {
    constexpr auto width = sizeof(unsigned long) * 8;
    return code / width < N && ((bits[code / width] >> (code % width)) & 1UL);
}

bool supported(int fd) {
    std::array<unsigned long, (KEY_MAX / (sizeof(unsigned long) * 8)) + 1> keys{};
    std::array<unsigned long, (ABS_MAX / (sizeof(unsigned long) * 8)) + 1> axes{};
    const bool keyboard = ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys.data()) >= 0 &&
        bit(keys, KEY_HOME) && (bit(keys, KEY_LEFTCTRL) || bit(keys, KEY_RIGHTCTRL)) &&
        (bit(keys, KEY_LEFTALT) || bit(keys, KEY_RIGHTALT));
    const bool touch = ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(axes)), axes.data()) >= 0 &&
        bit(axes, ABS_MT_SLOT) && bit(axes, ABS_MT_TRACKING_ID);
    return keyboard || touch;
}

bool eventName(const char* name) {
    if (std::strncmp(name, "event", 5) != 0 || name[5] == '\0') return false;
    for (const char* digit = name + 5; *digit; ++digit)
        if (*digit < '0' || *digit > '9') return false;
    return true;
}
}

struct GlobalHomeInput::Impl {
    struct Device {
        std::string name;
        dev_t identity;
        int fd;
        bool seen = false;
        global_home_detail::EventParser parser;
        Device(std::string path, dev_t device, int descriptor)
            : name(std::move(path)), identity(device), fd(descriptor) {
            parser.setApplicationRunning(true);
        }
        ~Device() { close(fd); }
    };
    bool enabled = isImage();
    std::chrono::steady_clock::time_point next_scan{};
    std::vector<std::unique_ptr<Device>> devices;
    std::size_t next_device = 0;

    void scan() {
        if (!isImage()) { enabled = false; devices.clear(); return; }
        for (auto& device : devices) device->seen = false;
        const int dirfd = open("/dev/input", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        DIR* directory = dirfd < 0 ? nullptr : fdopendir(dirfd);
        if (!directory) {
            if (dirfd >= 0) close(dirfd);
            devices.clear();
            return;
        }
        // Device count and directory traversal are bounded even for a broken /dev.
        unsigned inspected = 0;
        while (const auto* entry = readdir(directory)) {
            if (++inspected > 256) break;
            if (!eventName(entry->d_name)) continue;
            struct stat info{};
            if (fstatat(dirfd, entry->d_name, &info, AT_SYMLINK_NOFOLLOW) != 0 || !S_ISCHR(info.st_mode)) continue;
            auto existing = std::find_if(devices.begin(), devices.end(), [&](const auto& device) {
                return device->name == entry->d_name && device->identity == info.st_rdev;
            });
            if (existing != devices.end()) { (*existing)->seen = true; continue; }
            if (devices.size() >= 32) continue;
            const int fd = openat(dirfd, entry->d_name, O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
            if (fd < 0) continue;
            struct stat opened{};
            if (fstat(fd, &opened) != 0 || !S_ISCHR(opened.st_mode) || opened.st_rdev != info.st_rdev || !supported(fd)) {
                close(fd);
                continue;
            }
            auto device = std::make_unique<Device>(entry->d_name, info.st_rdev, fd);
            device->seen = true;
            devices.push_back(std::move(device));
        }
        closedir(directory);
        std::erase_if(devices, [](const auto& device) { return !device->seen; });
    }

    bool poll(bool running) {
        if (!enabled) return false;
        if (!running) {
            // Closing readers discards their queues. A later app gets fresh fds,
            // so keys/touches queued on HOME cannot close a newly started app.
            devices.clear();
            next_scan = {};
            next_device = 0;
            return false;
        }
        const auto now = std::chrono::steady_clock::now();
        if (now >= next_scan) {
            scan();
            next_scan = now + std::chrono::seconds(2);
        }
        if (devices.empty()) return false;
        unsigned total = 0;
        bool triggered = false;
        const auto start = next_device % devices.size();
        next_device = start + 1;
        for (std::size_t offset = 0; offset < devices.size() && total < 512; ++offset) {
            auto& device = devices[(start + offset) % devices.size()];
            unsigned handled = 0;
            while (handled < 64 && total < 512) {
                std::array<input_event, 16> events{};
                const auto limit = std::min({16U, 64U - handled, 512U - total});
                const auto bytes = read(device->fd, events.data(), limit * sizeof(input_event));
                if (bytes < 0 && errno == EINTR) break;
                if (bytes < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
                if (bytes <= 0 || bytes % sizeof(input_event) != 0) { device->seen = false; break; }
                const auto count = static_cast<unsigned>(bytes / sizeof(input_event));
                total += count;
                handled += count;
                for (unsigned index = 0; index < count; ++index)
                    triggered = device->parser.consume(events[index].type, events[index].code, events[index].value) || triggered;
            }
        }
        std::erase_if(devices, [](const auto& device) { return !device->seen; });
        return triggered;
    }
};

GlobalHomeInput::GlobalHomeInput() : impl_(std::make_unique<Impl>()) {}
GlobalHomeInput::~GlobalHomeInput() = default;
bool GlobalHomeInput::poll(bool application_running) { return impl_->poll(application_running); }
}
