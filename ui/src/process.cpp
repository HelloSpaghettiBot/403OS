#include "surface/process.hpp"
#include <cerrno>
#include <cstring>
#include <csignal>
#include <cstdio>
#include <fcntl.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
namespace surface {
bool Process::failed(const char* stage, const std::string& executable, int number) {
    error = "Cannot start " + executable + ": " + std::strerror(number) +
        " (" + stage + ", error " + std::to_string(number) + ")";
    const auto record = "SURFACE_APPLICATION_ERROR " + error + "\n";
    std::fputs(record.c_str(), stderr);
    if (!failure_log_.empty()) {
        // Keep only the latest failure; never log game arguments, credentials
        // or follow a substituted log file. Failure to log must not hide errno.
        const auto slash=failure_log_.find_last_of('/');
        if (slash!=std::string::npos) {
            const auto parent=failure_log_.substr(0,slash);
            const int directory=open(parent.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
            if(directory>=0) {
                struct stat info{};
                if(fstat(directory,&info)==0 && info.st_uid==geteuid() && !(info.st_mode&0022)) {
                    const int fd=openat(directory,failure_log_.substr(slash+1).c_str(),
                        O_WRONLY|O_CREAT|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK,0600);
                    if(fd>=0) {
                        if(fstat(fd,&info)==0 && S_ISREG(info.st_mode) && info.st_uid==geteuid() &&
                           info.st_nlink==1 && !(info.st_mode&0077) && ftruncate(fd,0)==0) {
                            const auto bounded=record.substr(0,4096);
                            std::size_t written=0;
                            while(written<bounded.size()) {
                                const auto count=write(fd,bounded.data()+written,bounded.size()-written);
                                if(count<0 && errno==EINTR) continue;
                                if(count<=0) break;
                                written+=static_cast<std::size_t>(count);
                            }
                        }
                        close(fd);
                    }
                }
                close(directory);
            }
        }
    }
    return false;
}
bool Process::start(const std::vector<std::string>& arguments) {
    error.clear();
    if (running()) { error = "An application is already running"; return false; }
    if (arguments.empty() || arguments.front().empty()) { error = "Application is not packaged in this image"; return false; }
    if (arguments.front().front() != '/') return failed("absolute path required",arguments.front(),EINVAL);
    struct stat info{};
    if (stat(arguments.front().c_str(),&info)!=0) return failed("executable lookup",arguments.front(),errno);
    if (!S_ISREG(info.st_mode)) return failed("not a regular executable",arguments.front(),EACCES);
    if (access(arguments.front().c_str(), X_OK) != 0) return failed("execute permission",arguments.front(),errno);
    std::vector<std::string> command;
    if(profiles_) command={"/usr/sbin/surface-profiles","--run","--"};
    command.insert(command.end(),arguments.begin(),arguments.end());
    std::vector<char*> argv;
    for (auto& argument : command) argv.push_back(argument.data());
    argv.push_back(nullptr);
    posix_spawnattr_t attributes;
    int result=posix_spawnattr_init(&attributes);
    if(result!=0) return failed("process attributes",command.front(),result);
    result=posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP);
    if(result==0) result=posix_spawnattr_setpgroup(&attributes, 0);
    if(result==0) result=posix_spawn(&child_, argv.front(), nullptr, &attributes, argv.data(), environ);
    posix_spawnattr_destroy(&attributes);
    if (result != 0) { child_ = -1; return failed("process launch",command.front(),result); }
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
