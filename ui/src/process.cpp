#include "surface/process.hpp"
#include <cerrno>
#include <cstring>
#include <csignal>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
namespace surface {
bool Process::start(const std::vector<std::string>& arguments) {
    error.clear();
    if (running()) { error = "An application is already running"; return false; }
    if (arguments.empty() || arguments.front().empty()) { error = "Application is not packaged in this image"; return false; }
    if (arguments.front().front() != '/' || access(arguments.front().c_str(), X_OK) != 0) {
        error = "Application is not installed: " + arguments.front(); return false;
    }
    std::vector<char*> argv;
    for (const auto& argument : arguments) argv.push_back(const_cast<char*>(argument.c_str()));
    argv.push_back(nullptr);
    posix_spawnattr_t attributes;
    posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attributes, 0);
    const int result = posix_spawn(&child_, argv.front(), nullptr, &attributes, argv.data(), environ);
    posix_spawnattr_destroy(&attributes);
    if (result != 0) { child_ = -1; error = std::strerror(result); return false; }
    stopping_ = false;
    return true;
}
void Process::stop() {
    if(running() && !stopping_) {
        stopping_ = true;stop_deadline_=std::chrono::steady_clock::now()+std::chrono::seconds(5);
        kill(-child_, SIGTERM);
    }
}
std::optional<int> Process::poll() {
    if (!running()) return std::nullopt;
    if(stopping_ && std::chrono::steady_clock::now()>=stop_deadline_) kill(-child_, SIGKILL);
    int status = 0;
    const auto result = waitpid(child_, &status, WNOHANG);
    if (result == 0 || (result < 0 && errno == EINTR)) return std::nullopt;
    child_ = -1;
    if (result < 0) return 127;
    if(stopping_ && WIFSIGNALED(status) && WTERMSIG(status)==SIGTERM) return 0;
    // Supervisors may translate our TERM into the conventional exit 143.
    if(stopping_ && WIFEXITED(status) && WEXITSTATUS(status)==128+SIGTERM) return 0;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
}
