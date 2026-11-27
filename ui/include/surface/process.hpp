#pragma once
#include <optional>
#include <chrono>
#include <string>
#include <vector>
#include <sys/types.h>
namespace surface {
class Process {
public:
    bool start(const std::vector<std::string>& arguments);
    void stop();
    std::optional<int> poll();
    bool running() const { return child_ > 0; }
    void useProfiles(bool enabled) { profiles_=enabled; }
    void failureLog(std::string path) { failure_log_=std::move(path); }
    std::string error;
private:
    bool failed(const char* stage, const std::string& executable, int number);
    std::string failure_log_;
    pid_t child_ = -1;
    bool stopping_ = false;
    bool profiles_ = false;
    std::chrono::steady_clock::time_point stop_deadline_{};
};
}
