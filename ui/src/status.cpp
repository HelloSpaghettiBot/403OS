#include "surface/status.hpp"
#include <ctime>
#include <algorithm>
#include <cerrno>
#include <cctype>
#include <csignal>
#include <fcntl.h>
#include <map>
#include <poll.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
#include <fstream>

namespace surface {
namespace {
constexpr std::size_t status_limit=256*1024;
struct Json {
    enum class Type { Null, Bool, Number, String, Object, Array } type=Type::Null;
    bool boolean=false;
    std::string string;
    std::map<std::string,Json> members;
};
class JsonReader {
    std::string_view input;
    std::size_t at=0;
    [[noreturn]] static void invalid() { throw std::runtime_error("Invalid status JSON"); }
    void whitespace() { while(at<input.size() && (input[at]==' ' || input[at]=='\n' || input[at]=='\r' || input[at]=='\t')) ++at; }
    bool take(char c) { whitespace(); if(at<input.size() && input[at]==c) { ++at; return true; } return false; }
    void require(char c) { if(!take(c)) invalid(); }
    unsigned hex() {
        unsigned value=0;
        for(unsigned i=0;i<4;++i) { if(at>=input.size()) invalid(); const char c=input[at++]; value<<=4;
            if(c>='0' && c<='9') value+=c-'0'; else if(c>='a' && c<='f') value+=c-'a'+10; else if(c>='A' && c<='F') value+=c-'A'+10; else invalid(); }
        return value;
    }
    static void utf8(std::string& output,unsigned cp) {
        if(cp<0x80) output+=static_cast<char>(cp);
        else if(cp<0x800) { output+=static_cast<char>(0xc0|(cp>>6)); output+=static_cast<char>(0x80|(cp&63)); }
        else if(cp<0x10000) { output+=static_cast<char>(0xe0|(cp>>12)); output+=static_cast<char>(0x80|((cp>>6)&63)); output+=static_cast<char>(0x80|(cp&63)); }
        else { output+=static_cast<char>(0xf0|(cp>>18)); output+=static_cast<char>(0x80|((cp>>12)&63)); output+=static_cast<char>(0x80|((cp>>6)&63)); output+=static_cast<char>(0x80|(cp&63)); }
    }
    std::string string() {
        require('"'); std::string result;
        while(at<input.size()) {
            const auto c=static_cast<unsigned char>(input[at++]); if(c=='"') return result;
            if(c<32) invalid();
            if(c!='\\') { result+=static_cast<char>(c); continue; }
            if(at>=input.size()) invalid();
            switch(input[at++]) {
            case '"': result+='"'; break; case '\\': result+='\\'; break; case '/': result+='/'; break;
            case 'b': result+='\b'; break; case 'f': result+='\f'; break; case 'n': result+='\n'; break; case 'r': result+='\r'; break; case 't': result+='\t'; break;
            case 'u': {
                unsigned cp=hex();
                if(cp>=0xd800 && cp<=0xdbff) { if(at+2>input.size() || input[at++]!='\\' || input[at++]!='u') invalid(); const auto low=hex(); if(low<0xdc00 || low>0xdfff) invalid(); cp=0x10000+((cp-0xd800)<<10)+(low-0xdc00); }
                else if(cp>=0xdc00 && cp<=0xdfff) invalid();
                utf8(result,cp); break;
            }
            default: invalid();
            }
        }
        invalid();
    }
    Json value(unsigned depth) {
        if(depth>48) invalid();
        whitespace(); if(at>=input.size()) invalid(); Json result;
        if(input[at]=='{') {
            ++at; result.type=Json::Type::Object; if(take('}')) return result;
            do { const auto key=string(); require(':'); auto child=value(depth+1); if(!result.members.emplace(key,std::move(child)).second) invalid(); } while(take(','));
            require('}'); return result;
        }
        if(input[at]=='[') { ++at; result.type=Json::Type::Array; if(take(']')) return result; do { value(depth+1); } while(take(',')); require(']'); return result; }
        if(input[at]=='"') { result.type=Json::Type::String; result.string=string(); return result; }
        for(const auto literal:{std::string_view("true"),std::string_view("false"),std::string_view("null")}) {
            if(input.substr(at,literal.size())==literal) { at+=literal.size(); result.type=literal=="null" ? Json::Type::Null : Json::Type::Bool; result.boolean=literal=="true"; return result; }
        }
        result.type=Json::Type::Number;
        if(input[at]=='-') ++at;
        if(at>=input.size()) invalid();
        if(input[at]=='0') ++at;
        else { if(input[at]<'1' || input[at]>'9') invalid(); while(at<input.size() && input[at]>='0' && input[at]<='9') ++at; }
        if(at<input.size() && input[at]=='.') { ++at; const auto start=at; while(at<input.size() && input[at]>='0' && input[at]<='9') ++at; if(at==start) invalid(); }
        if(at<input.size() && (input[at]=='e' || input[at]=='E')) { ++at; if(at<input.size() && (input[at]=='+' || input[at]=='-')) ++at; const auto start=at; while(at<input.size() && input[at]>='0' && input[at]<='9') ++at; if(at==start) invalid(); }
        return result;
    }
public:
    explicit JsonReader(std::string_view text):input(text) {}
    Json read() { if(input.size()>status_limit) invalid(); auto result=value(0); whitespace(); if(at!=input.size()) invalid(); return result; }
};
struct CommandOutput { std::string text; bool complete=false,timed_out=false; };
CommandOutput status_command(const std::filesystem::path& executable,std::chrono::milliseconds timeout) {
    CommandOutput result;
    if(!executable.is_absolute() || access(executable.c_str(),X_OK)!=0) return result;
    int pipefd[2]; if(pipe2(pipefd,O_CLOEXEC)!=0) return result;
    fcntl(pipefd[0],F_SETFL,O_NONBLOCK);
    posix_spawn_file_actions_t actions; posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions,STDIN_FILENO,"/dev/null",O_RDONLY,0);
    posix_spawn_file_actions_addopen(&actions,STDERR_FILENO,"/dev/null",O_WRONLY,0);
    posix_spawn_file_actions_adddup2(&actions,pipefd[1],STDOUT_FILENO);
    posix_spawn_file_actions_addclose(&actions,pipefd[0]); posix_spawn_file_actions_addclose(&actions,pipefd[1]);
    posix_spawnattr_t attributes; posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes,POSIX_SPAWN_SETPGROUP); posix_spawnattr_setpgroup(&attributes,0);
    const std::string path=executable.string();
    char* const argv[]{const_cast<char*>(path.c_str()),const_cast<char*>("status"),const_cast<char*>("--json"),nullptr};
    pid_t child=-1; const int spawned=posix_spawn(&child,path.c_str(),&actions,&attributes,argv,environ);
    posix_spawnattr_destroy(&attributes); posix_spawn_file_actions_destroy(&actions); close(pipefd[1]);
    if(spawned!=0) { close(pipefd[0]); return result; }
    const auto deadline=std::chrono::steady_clock::now()+std::clamp(timeout,std::chrono::milliseconds(10),std::chrono::milliseconds(2000));
    bool reaped=false,eof=false,oversize=false; int status=0;
    while(std::chrono::steady_clock::now()<deadline) {
        char buffer[4096];
        for(;;) {
            const auto count=read(pipefd[0],buffer,sizeof(buffer));
            if(count>0) { result.text.append(buffer,static_cast<std::size_t>(count)); if(result.text.size()>status_limit) { oversize=true; break; } }
            else if(count==0) { eof=true; break; }
            else if(errno==EINTR) continue;
            else break;
        }
        if(oversize) break;
        if(!reaped) { const auto waited=waitpid(child,&status,WNOHANG); reaped=waited==child || (waited<0 && errno!=EINTR); }
        if(reaped && eof) { result.complete=WIFEXITED(status); break; }
        pollfd descriptor{pipefd[0],POLLIN,0}; poll(&descriptor,1,10);
    }
    if(!reaped) { kill(-child,SIGKILL); while(waitpid(child,&status,0)<0 && errno==EINTR) {} }
    close(pipefd[0]); result.timed_out=!result.complete && !oversize; return result;
}
std::string line(const std::filesystem::path& path) {
    std::ifstream input(path);
    std::string value;
    std::getline(input, value);
    return value;
}
}
VpnStatus parse_tailscale_status(std::string_view json) {
    try {
        const auto root=JsonReader(json).read(); if(root.type!=Json::Type::Object) return {"Tailscale state unverified",false};
        const auto backend=root.members.find("BackendState");
        if(backend==root.members.end() || backend->second.type!=Json::Type::String) return {"Tailscale state unverified",false};
        const auto& state=backend->second.string;
        if(state=="NeedsLogin") return {"Tailscale sign-in required",false};
        if(state=="NeedsMachineAuth") return {"Tailscale approval required",false};
        if(state=="Stopped") return {"Tailscale stopped",false};
        if(state=="Starting" || state=="NoState") return {"Tailscale starting",false};
        if(state=="Running") {
            const auto self=root.members.find("Self");
            if(self!=root.members.end() && self->second.type==Json::Type::Object) {
                const auto online=self->second.members.find("Online");
                if(online!=self->second.members.end() && online->second.type==Json::Type::Bool)
                    return online->second.boolean ? VpnStatus{"Tailscale connected",true} : VpnStatus{"Tailscale offline",false};
            }
        }
        return {"Tailscale state unverified",false};
    } catch(const std::exception&) { return {"Tailscale state unverified",false}; }
}
VpnStatus read_tailscale_status(const std::filesystem::path& executable,std::chrono::milliseconds timeout) {
    const auto output=status_command(executable,timeout);
    if(!output.complete) return {output.timed_out ? "Tailscale status timed out" : "Tailscale unavailable",false};
    if(output.text.empty()) return {};
    return parse_tailscale_status(output.text);
}
Status read_status(const std::filesystem::path& sys) {
    Status result;
    std::error_code error;
    for (const auto& entry : std::filesystem::directory_iterator(sys / "class/net", error)) {
        if (entry.path().filename() == "lo") continue;
        if (line(entry.path() / "operstate") == "up") {
            result.network = std::filesystem::exists(entry.path() / "wireless") ? "WiFi link up" : "Ethernet link up";
            break;
        }
        result.network = "Network link down";
    }
    error.clear();
    for (const auto& entry : std::filesystem::directory_iterator(sys / "class/bluetooth", error)) {
        if (entry.path().filename().string().starts_with("hci")) result.bluetooth = "BT adapter present";
    }
    error.clear();
    for (const auto& entry : std::filesystem::directory_iterator(sys / "class/power_supply", error)) {
        if (line(entry.path() / "type") != "Battery") continue;
        const auto capacity = line(entry.path() / "capacity");
        if (!capacity.empty()) result.battery = capacity + "% " + line(entry.path() / "status");
    }
    // Fixture sysfs roots remain independent of the host's installed services.
    if(sys==std::filesystem::path("/sys")) {
        const auto vpn=read_tailscale_status(); result.vpn=vpn.text; result.vpn_connected=vpn.connected;
    }
    std::time_t now = std::time(nullptr);
    std::tm local{};
    localtime_r(&now, &local);
    char clock[24]{};
    std::strftime(clock, sizeof(clock), "%I:%M %p", &local);
    result.time = clock;
    if(result.time.starts_with('0')) result.time.erase(0,1);
    return result;
}
}
