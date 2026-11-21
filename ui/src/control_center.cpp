#include "surface/control_center.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstring>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <ifaddrs.h>
#include <netdb.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sstream>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;
namespace surface {
namespace {
using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;
std::string readLine(const fs::path& path) { std::ifstream stream(path); std::string value; std::getline(stream,value); return value; }
std::string clean(std::string text) {
    std::string result; bool escape=false;
    for(unsigned char character:text) {
        if(character==27) { escape=true; continue; }
        if(escape) { if(character>='@' && character<='~' && character!='[') escape=false; continue; }
        if(character=='\n' || character=='\t' || character>=32) result+=static_cast<char>(character);
    }
    return result;
}
std::vector<std::string> lines(const std::string& text) {
    std::vector<std::string> result; std::istringstream stream(clean(text)); std::string line;
    while(std::getline(stream,line)) if(!line.empty()) result.push_back(line);
    return result;
}
std::string field(const std::string& text,const std::string& name) {
    for(const auto& line:lines(text)) if(line.starts_with(name+'=')) return line.substr(name.size()+1);
    return {};
}
std::string executable(const std::string& name) {
    if(name.starts_with('/')) return access(name.c_str(),X_OK)==0?name:std::string{};
    for(const auto* directory:{"/usr/bin/","/usr/sbin/","/bin/","/sbin/"}) {
        const std::string path=std::string(directory)+name;
        if(access(path.c_str(),X_OK)==0) return path;
    }
    return {};
}
std::string brief(const std::string& text) {
    auto list=lines(text); if(list.empty()) return "Command returned no diagnostic output";
    auto value=list.back(); if(value.size()>240) value.resize(240); return value;
}
bool isImage() {
    struct stat info{};
    return lstat("/etc/surface-image-id",&info)==0 && S_ISREG(info.st_mode) && info.st_uid==0 &&
        !(info.st_mode&0022) && readLine("/etc/surface-image-id").starts_with("surface-go-");
}
std::string wifiInterface() {
    std::error_code error;
    for(const auto& item:fs::directory_iterator("/sys/class/net",error))
        if(fs::exists(item.path()/"wireless",error)) return item.path().filename().string();
    return {};
}
std::string address(const std::string& interface) {
    ifaddrs* list=nullptr; if(getifaddrs(&list)!=0) return {};
    std::string result;
    for(auto* entry=list;entry;entry=entry->ifa_next) {
        if(!entry->ifa_addr || interface!=entry->ifa_name || entry->ifa_addr->sa_family!=AF_INET) continue;
        std::array<char,NI_MAXHOST> name{};
        if(getnameinfo(entry->ifa_addr,sizeof(sockaddr_in),name.data(),name.size(),nullptr,0,NI_NUMERICHOST)==0) result=name.data();
    }
    freeifaddrs(list); return result;
}
bool ensurePrivate(const fs::path& directory) {
    std::error_code error; fs::create_directories(directory,error);
    return !error && chmod(directory.c_str(),0700)==0;
}
bool writePrivate(const fs::path& path,const std::string& data) {
    if(!ensurePrivate(path.parent_path())) return false;
    const auto temporary=path.string()+".new";
    const int descriptor=::open(temporary.c_str(),O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
    if(descriptor<0) return false;
    const auto count=::write(descriptor,data.data(),data.size());
    const bool ok=count==static_cast<ssize_t>(data.size()) && fsync(descriptor)==0;
    ::close(descriptor);
    if(!ok) { unlink(temporary.c_str()); return false; }
    return rename(temporary.c_str(),path.c_str())==0;
}
std::string hex(const std::string& text) {
    static constexpr char digits[]="0123456789abcdef"; std::string result;
    for(unsigned char character:text) { result+=digits[character>>4]; result+=digits[character&15]; } return result;
}
std::string wpaQuote(const std::string& text) {
    std::string result="\"";
    for(char character:text) { if(character=='\\' || character=='\"') result+='\\'; result+=character; }
    return result+'\"';
}
std::string decodeSSID(const std::string& text) {
    std::string result;
    for(std::size_t i=0;i<text.size();++i) {
        if(text[i]=='\\' && i+3<text.size() && text[i+1]=='x' && std::isxdigit(static_cast<unsigned char>(text[i+2])) && std::isxdigit(static_cast<unsigned char>(text[i+3]))) {
            result+=static_cast<char>(std::stoi(text.substr(i+2,2),nullptr,16)); i+=3;
        } else if(text[i]=='\\' && i+1<text.size() && text[i+1]=='\\') { result+='\\'; ++i; }
        else result+=text[i];
    }
    return result;
}
std::string jsonString(const std::string& text,const std::string& key) {
    auto position=text.find('"'+key+'"'); if(position==std::string::npos) return {};
    position=text.find(':',position+key.size()+2); if(position==std::string::npos) return {};
    position=text.find_first_not_of(" \t\r\n",position+1);
    if(position==std::string::npos || text[position]!='"') return {};
    std::string result;
    const auto hexValue=[](char c)->int {
        if(c>='0' && c<='9') return c-'0';
        if(c>='a' && c<='f') return c-'a'+10;
        if(c>='A' && c<='F') return c-'A'+10;
        return -1;
    };
    const auto unicode=[&](std::size_t first)->std::optional<std::uint32_t> {
        if(first>text.size() || text.size()-first<4) return std::nullopt;
        std::uint32_t value=0;
        for(std::size_t index=first;index<first+4;++index) {
            const auto digit=hexValue(text[index]); if(digit<0) return std::nullopt;
            value=(value<<4)|static_cast<std::uint32_t>(digit);
        }
        return value;
    };
    const auto utf8=[&](std::uint32_t value) {
        if(value<=0x7f) result+=static_cast<char>(value);
        else if(value<=0x7ff) {
            result+=static_cast<char>(0xc0|(value>>6)); result+=static_cast<char>(0x80|(value&0x3f));
        } else if(value<=0xffff) {
            result+=static_cast<char>(0xe0|(value>>12)); result+=static_cast<char>(0x80|((value>>6)&0x3f)); result+=static_cast<char>(0x80|(value&0x3f));
        } else {
            result+=static_cast<char>(0xf0|(value>>18)); result+=static_cast<char>(0x80|((value>>12)&0x3f));
            result+=static_cast<char>(0x80|((value>>6)&0x3f)); result+=static_cast<char>(0x80|(value&0x3f));
        }
    };
    for(++position;position<text.size();++position) {
        const char c=text[position];
        if(c=='"') return result;
        if(static_cast<unsigned char>(c)<32) return {};
        if(c!='\\') { result+=c; continue; }
        if(++position>=text.size()) return {};
        const char escaped=text[position];
        if(escaped=='"' || escaped=='\\' || escaped=='/') result+=escaped;
        else if(escaped=='b') result+='\b';
        else if(escaped=='f') result+='\f';
        else if(escaped=='n') result+='\n';
        else if(escaped=='r') result+='\r';
        else if(escaped=='t') result+='\t';
        else if(escaped=='u') {
            const auto first=unicode(position+1); if(!first) return {};
            std::uint32_t value=*first; position+=4;
            if(value>=0xd800 && value<=0xdbff) {
                if(text.size()-position<7 || text[position+1]!='\\' || text[position+2]!='u') return {};
                const auto second=unicode(position+3);
                if(!second || *second<0xdc00 || *second>0xdfff) return {};
                value=0x10000+((value-0xd800)<<10)+(*second-0xdc00); position+=6;
            } else if(value>=0xdc00 && value<=0xdfff) return {};
            utf8(value);
        } else return {};
    }
    return {}; // Reject incomplete JSON instead of returning a changed path.
}
std::size_t jsonValue(const std::string& text,const std::string& key) {
    auto position=text.find('"'+key+'"'); if(position==std::string::npos) return position;
    position=text.find(':',position+key.size()+2); if(position==std::string::npos) return position;
    return text.find_first_not_of(" \t\r\n",position+1);
}
bool jsonBool(const std::string& text,const std::string& key) {
    const auto position=jsonValue(text,key); return position!=std::string::npos && text.compare(position,4,"true")==0;
}
std::uint64_t jsonNumber(const std::string& text,const std::string& key) {
    const auto position=jsonValue(text,key); if(position==std::string::npos) return 0;
    try { return std::stoull(text.substr(position)); } catch(const std::exception&) { return 0; }
}
std::vector<std::string> jsonObjects(const std::string& text,const std::string& key) {
    std::vector<std::string> result; const auto array=jsonValue(text,key);
    if(array==std::string::npos || text[array]!='[') return result;
    bool quoted=false,escape=false; unsigned depth=0; std::size_t begin=0;
    for(std::size_t index=array+1;index<text.size();++index) {
        const char c=text[index];
        if(quoted) { if(escape) escape=false; else if(c=='\\') escape=true; else if(c=='"') quoted=false; continue; }
        if(c=='"') quoted=true;
        else if(c=='{') { if(depth++==0) begin=index; }
        else if(c=='}' && depth) { if(--depth==0) result.push_back(text.substr(begin,index-begin+1)); }
        else if(c==']' && !depth) break;
    }
    return result;
}
std::vector<std::string> jsonStrings(const std::string& text,const std::string& key) {
    std::vector<std::string> result; const auto array=jsonValue(text,key);
    if(array==std::string::npos || text[array]!='[') return result;
    bool quoted=false,escape=false; std::size_t begin=0;
    for(std::size_t index=array+1;index<text.size();++index) {
        const char c=text[index];
        if(quoted) {
            if(escape) escape=false;
            else if(c=='\\') escape=true;
            else if(c=='"') {
                result.push_back(jsonString("{\"value\":"+text.substr(begin,index-begin+1)+"}","value")); quoted=false;
                if(result.size()>=64) break;
            }
        } else if(c=='"') { quoted=true; begin=index; }
        else if(c==']') break;
    }
    return result;
}
std::string readTail(const fs::path& path) {
    std::ifstream stream(path,std::ios::binary|std::ios::ate); if(!stream) return {};
    const auto length=stream.tellg(); const auto size=std::min<std::streamoff>(length,65536);
    if(size<=0) return {};
    std::string result(static_cast<std::size_t>(size),'\0'); stream.seekg(length-size); stream.read(result.data(),size); return result;
}
bool validHost(const std::string& host) {
    return !host.empty() && host.size()<=253 && host.front()!='-' &&
        std::all_of(host.begin(),host.end(),[](unsigned char c){ return std::isalnum(c) || c=='.' || c=='-' || c==':' || c=='_' || c=='[' || c==']'; });
}
bool validMac(const std::string& value) {
    if(value.size()!=17) return false;
    for(std::size_t i=0;i<value.size();++i) if(i%3==2?value[i]!=':':!std::isxdigit(static_cast<unsigned char>(value[i]))) return false;
    return true;
}
bool resolution(const std::string& value,unsigned& width,unsigned& height) {
    const auto separator=value.find_first_of("xX");
    if(separator==std::string::npos || separator==0 || separator+1==value.size() || value.size()>11) return false;
    if(!std::all_of(value.begin(),value.begin()+static_cast<std::ptrdiff_t>(separator),[](unsigned char c){return std::isdigit(c);}) ||
       !std::all_of(value.begin()+static_cast<std::ptrdiff_t>(separator+1),value.end(),[](unsigned char c){return std::isdigit(c);})) return false;
    try {
        const auto w=std::stoul(value.substr(0,separator)),h=std::stoul(value.substr(separator+1));
        if(w<320 || w>3840 || h<240 || h>2160 || w%2 || h%2) return false;
        width=static_cast<unsigned>(w); height=static_cast<unsigned>(h); return true;
    } catch(const std::exception&) { return false; }
}
std::string storage(const std::string& path) {
    struct statvfs info{}; if(statvfs(path.c_str(),&info)!=0) return path+": unavailable";
    const auto total=static_cast<std::uint64_t>(info.f_blocks)*info.f_frsize;
    const auto free=static_cast<std::uint64_t>(info.f_bavail)*info.f_frsize;
    return path+": "+std::to_string(free/(1024*1024))+" MiB available / "+std::to_string(total/(1024*1024))+" MiB total";
}
} // namespace

namespace control_detail {
// One bounded process group at a time. Stdout/stderr remain in memory; command
// arguments and Wi-Fi credentials are never printed or written into UI logs.
struct Command {
    pid_t pid=-1; int output=-1,input=-1; std::string text,pending;
    struct Retired { pid_t pid; Clock::time_point killAfter; };
    std::vector<Retired> retired;
    Clock::time_point deadline{}; bool timedOut=false;
    bool running() const { return pid>0; }
    void append(const char* data,std::size_t count) {
        text.append(data,count);
        if(text.size()>65536) text.erase(0,text.size()-65536);
    }
    void reap() {
        retired.erase(std::remove_if(retired.begin(),retired.end(),[](const Retired& child){
            const auto result=waitpid(child.pid,nullptr,WNOHANG);
            if(result>0 || (result<0 && errno==ECHILD)) return true;
            if(Clock::now()>=child.killAfter) kill(-child.pid,SIGKILL);
            return false;
        }),retired.end());
    }
    void cancel() {
        // Supervisors need a short TERM window to clean up descendants which
        // moved to separate sessions. Reaping/escalation never blocks a frame.
        if(pid>0) { kill(-pid,SIGTERM); retired.push_back({pid,Clock::now()+std::chrono::seconds(4)}); }
        reap();
        pid=-1; if(output>=0) ::close(output); if(input>=0) ::close(input);
        output=input=-1; std::fill(pending.begin(),pending.end(),'\0'); pending.clear();
        std::fill(text.begin(),text.end(),'\0'); text.clear();
    }
    ~Command() { cancel(); }
    bool start(std::vector<std::string> args,int timeout,std::string data,std::string& error) {
        if(running()) { error="A device operation is already running"; return false; }
        text.clear(); timedOut=false;
        if(args.empty()) return false;
        const auto path=executable(args[0]); if(path.empty()) { error=args[0]+" is not installed in this image"; return false; }
        args[0]=path;
        int out[2],in[2];
        if(pipe2(out,O_CLOEXEC)<0) { error=std::strerror(errno); return false; }
        if(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,in)<0) { ::close(out[0]); ::close(out[1]); error=std::strerror(errno); return false; }
        posix_spawn_file_actions_t files; posix_spawn_file_actions_init(&files);
        posix_spawn_file_actions_adddup2(&files,in[0],STDIN_FILENO);
        posix_spawn_file_actions_adddup2(&files,out[1],STDOUT_FILENO);
        posix_spawn_file_actions_adddup2(&files,out[1],STDERR_FILENO);
        posix_spawn_file_actions_addclose(&files,out[0]); posix_spawn_file_actions_addclose(&files,in[1]);
        posix_spawnattr_t attributes; posix_spawnattr_init(&attributes);
        posix_spawnattr_setflags(&attributes,POSIX_SPAWN_SETPGROUP); posix_spawnattr_setpgroup(&attributes,0);
        std::vector<char*> argv; for(auto& argument:args) argv.push_back(argument.data()); argv.push_back(nullptr);
        const int result=posix_spawn(&pid,path.c_str(),&files,&attributes,argv.data(),environ);
        posix_spawnattr_destroy(&attributes); posix_spawn_file_actions_destroy(&files);
        ::close(out[1]); ::close(in[0]);
        if(result) { ::close(out[0]); ::close(in[1]); pid=-1; error=std::strerror(result); return false; }
        output=out[0]; input=in[1]; fcntl(output,F_SETFL,O_NONBLOCK); fcntl(input,F_SETFL,O_NONBLOCK);
        pending=std::move(data); deadline=Clock::now()+std::chrono::seconds(timeout);
        if(pending.empty()) { ::close(input); input=-1; }
        return true;
    }
    std::optional<int> update() {
        reap();
        if(!running()) return std::nullopt;
        if(input>=0) {
            // All command input is shorter than PIPE_BUF. Poll before writing,
            // and never expose sensitive input in diagnostic command strings.
            const auto count=::send(input,pending.data(),pending.size(),MSG_NOSIGNAL);
            if(count>0) { std::fill(pending.begin(),pending.begin()+count,'\0'); pending.erase(0,static_cast<std::size_t>(count)); }
            if(pending.empty() || (count<0 && errno!=EAGAIN && errno!=EINTR)) { ::close(input); input=-1; }
        }
        std::array<char,2048> buffer{}; ssize_t count=0;
        for(unsigned reads=0;reads<16 && (count=::read(output,buffer.data(),buffer.size()))>0;++reads) {
            append(buffer.data(),static_cast<std::size_t>(count));
        }
        if(Clock::now()>deadline) { timedOut=true; kill(-pid,SIGKILL); }
        int state=0; const auto result=waitpid(pid,&state,WNOHANG);
        if(result==0 || (result<0 && errno==EINTR)) return std::nullopt;
        for(unsigned reads=0;reads<32 && (count=::read(output,buffer.data(),buffer.size()))>0;++reads) append(buffer.data(),static_cast<std::size_t>(count));
        pid=-1; ::close(output); output=-1; if(input>=0) ::close(input); input=-1;
        std::fill(pending.begin(),pending.end(),'\0'); pending.clear();
        if(timedOut) return 124;
        return result<0?127:(WIFEXITED(state)?WEXITSTATUS(state):128+WTERMSIG(state));
    }
};
} // namespace control_detail
using control_detail::Command;

struct ControlCenter::Impl {
    enum class Edit { None,WifiPassword,HiddenSSID,MoonHost,MoonResolution,BrowserURL,InstallConfirm,LibrarySource,LibrarySearch,DW3Source,PokeSource,WindowsPath,WindowsTitle };
    enum class Job { None,Mixer,Volume,AudioStore,WpaStatus,WpaStart,WpaScan,WpaResults,WpaAdd,WpaConfigure,WpaWait,Dhcp,WpaDisconnect,WpaForget,BtShow,BtPower,BtScan,BtDevices,BtInfo,BtPair,BtTrust,BtConnect,BtDisconnect,BtForget,TailStatus,TailLogin,TailLogout,MoonPair,MoonApps,MoonDiscover,InstallList,InstallPlan,InstallExecute,InstallPoweroff,PokeStatus,PokeInstall,LibraryScan,DW3Status,DW3Import,Reboot,Suspend,PokeScan,PokeImport,PokeRomStatus,WindowsStatus,WindowsSetup,LibraryVolumes,WindowsList,WindowsRegister };
    enum class Power { None,Reboot,Suspend };
    struct Network { std::string ssid,flags; int signal=0; };
    struct Device { std::string mac,name; };
    struct InstallSource { std::string path,hash; std::uint64_t bytes=0; };
    struct InstallTarget { std::string path,id,model; std::uint64_t bytes=0; };
    struct PokeRom { std::string path,title,code; std::uint64_t bytes=0; };
    struct WindowsApp { std::string path,title,runner; };
    enum class InstallStage { Disks,Review,Running,Done };
    ControlView view;
    PanelKind panel=PanelKind::Settings;
    bool opened=false,returnSettings=false,shift=false,logoutConfirm=false;
    bool systemInfo=false; Power powerConfirm=Power::None;
    Edit edit=Edit::None; Job job=Job::None,delayed=Job::None;
    Command command; Clock::time_point wake{};
    std::string interface,ssid,networkId,password,selectedSSID,selectedFlags;
    std::string mixer="Master",host,authURL,btMac,btName;
    unsigned moonWidth=1280,moonHeight=720,moonFps=60,moonBitrate=0;
    std::vector<Network> networks; std::vector<Device> devices;
    std::vector<std::string> apps;
    bool adapter=false,btDetail=false; unsigned attempts=0;
    std::optional<std::vector<std::string>> launch;
    std::optional<std::string> search;
    std::vector<InstallSource> installSources;
    std::vector<InstallTarget> installTargets;
    std::size_t installSourceIndex=0;
    InstallSource plannedSource; InstallTarget plannedTarget;
    std::uint64_t plannedDataBytes=0;
    InstallStage installStage=InstallStage::Disks;
    bool installPowerConfirm=false;
    bool pokeInstalled=false,pokeConfirm=false;
    bool pokeRomConfirm=false;
    std::string pokeSource="/library/roms";
    std::vector<PokeRom> pokeRoms; PokeRom selectedPokeRom;
    std::string librarySource="/data/roms";
    bool dw3Imported=false,dw3Confirm=false;
    bool protonReady=false,protonConfirm=false;
    std::string dw3Runner="wine";
    std::string windowsPath,windowsTitle,windowsRunner="wine";
    std::vector<WindowsApp> windowsApps;
    std::string dw3Source="/library/roms/DW3 Online";

    Impl() {
        keyboard(); host=readLine("/data/home/moonlight-host"); if(!validHost(host)) host.clear();
        const auto saved=readLine("/data/home/library-source"); if(!saved.empty() && saved.front()=='/') librarySource=saved;
        const auto dw3=readLine("/data/home/dw3-source"); if(!dw3.empty() && dw3.front()=='/') dw3Source=dw3;
        if(readLine("/data/home/dw3-runner")=="proton") dw3Runner="proton";
        const auto poke=readLine("/data/home/pokemmo-rom-source"); if(!poke.empty() && poke.front()=='/') pokeSource=poke;
        std::ifstream profile("/data/home/moonlight-profile"); std::string size; unsigned fps=0,rate=0,w=0,h=0;
        if(profile>>size>>fps>>rate && resolution(size,w,h) && (fps==30 || fps==60) && (rate==0 || rate==5000 || rate==10000 || rate==20000 || rate==40000)) {
            moonWidth=w; moonHeight=h; moonFps=fps; moonBitrate=rate;
        }
    }
    void keyboard() {
        view.keyboard.clear();
        for(char c='a';c<='z';++c) view.keyboard.emplace_back(1,shift?static_cast<char>(std::toupper(c)):c);
        for(char c='0';c<='9';++c) view.keyboard.emplace_back(1,c);
        for(const auto* key:{".","-","_","@","/",":","Space","Shift","!","?","#","$","%","&","(",")","+","=","[","]","{","}",";","'","\"",",","<",">","*","\\","|","~","`","^"}) view.keyboard.emplace_back(key);
        // Fill the character area to a complete 13-column row.
        while(view.keyboard.size()%13) view.keyboard.emplace_back(" ");
        view.keyboard.insert(view.keyboard.end(),{"Done","Delete","Cancel"});
    }
    bool allowed() {
        if(isImage()) return true;
        view.status="Device changes are available only inside the Surface OS image"; return false;
    }
    bool start(Job kind,std::vector<std::string> args,const std::string& message,int seconds=15,std::string input={}) {
        if(command.running()) { view.status="An operation is still running; Back cancels it"; return false; }
        std::string error;
        if(!command.start(std::move(args),seconds,std::move(input),error)) { view.status=error; view.busy=false; return false; }
        job=kind; view.busy=true; view.status=message; return true;
    }
    bool mutate(Job kind,std::vector<std::string> args,const std::string& message,int seconds=15,std::string input={}) {
        return allowed() && start(kind,std::move(args),message,seconds,std::move(input));
    }
    std::vector<std::string> wpa(std::initializer_list<std::string> arguments) const {
        std::vector<std::string> result{"wpa_cli","-i",interface}; result.insert(result.end(),arguments); return result;
    }
    std::vector<std::string> tail(std::initializer_list<std::string> arguments) const {
        std::vector<std::string> result{"tailscale","--socket=/run/tailscale/tailscaled.sock"}; result.insert(result.end(),arguments); return result;
    }
    std::vector<std::string> moon(const std::string& action) const {
        return {"moonlight",action,"-keydir","/data/moonlight",host};
    }
    std::string moonSize() const { return std::to_string(moonWidth)+"x"+std::to_string(moonHeight); }
    bool saveMoon(unsigned width,unsigned height,unsigned fps,unsigned bitrate) {
        if(!allowed()) return false;
        if(!writePrivate("/data/home/moonlight-profile",std::to_string(width)+"x"+std::to_string(height)+'\n'+std::to_string(fps)+'\n'+std::to_string(bitrate)+'\n')) { view.status="Cannot persist the streaming profile"; return false; }
        moonWidth=width; moonHeight=height; moonFps=fps; moonBitrate=bitrate; rows();
        view.status="Streaming profile saved. The host must support this resolution; decoding uses software"; return true;
    }
    void rows() {
        const auto selected=view.selected; view.rows.clear();
        switch(panel) {
        case PanelKind::Settings:
            view.title="Settings";
            if(powerConfirm!=Power::None) view.rows={powerConfirm==Power::Reboot?"Confirm restart":"Confirm suspend","Cancel"};
            else if(systemInfo) {
                view.rows={"Model: "+readLine("/sys/class/dmi/id/product_name"),"Kernel: "+readLine("/proc/sys/kernel/osrelease"),readLine("/proc/meminfo"),storage("/data"),storage("/"),fs::is_regular_file("/data/config/gamecontrollerdb.txt")?"Saved controller mappings: /data/config/gamecontrollerdb.txt":"Controller mappings: SDL defaults; no custom mappings saved","Return to Settings"};
            } else view.rows={"Volume down","Volume up","Toggle mute","Brightness down","Brightness up","Wi-Fi and network","Bluetooth controllers","Tailscale","Moonlight streaming","Open web browser","Install to internal storage","System and storage information","Restart device","Suspend device","Configure controller buttons","Windows applications"}; break;
        case PanelKind::Network:
            view.title="Wi-Fi and network";
            view.rows={"Scan for Wi-Fi networks","Connect to hidden WPA2 network","Disconnect Wi-Fi","Forget current network"};
            if(interface.empty()) view.rows.emplace_back("No Wi-Fi adapter detected");
            else view.rows.push_back(interface+" | "+(ssid.empty()?"not associated":ssid)+" | "+(address(interface).empty()?"no IPv4 address":address(interface)));
            for(const auto& network:networks) view.rows.push_back(network.ssid+"  "+std::to_string(network.signal)+" dBm  "+(network.flags.find("WPA")!=std::string::npos?"secured":"open/other"));
            break;
        case PanelKind::Bluetooth:
            view.title=btDetail?btName:"Bluetooth controllers";
            if(btDetail) view.rows={"Pair, trust and connect","Connect","Disconnect","Forget device","Back to controllers"};
            else { view.rows={"Scan for controllers","Refresh known devices"}; for(const auto& device:devices) view.rows.push_back(device.name+"  "+device.mac); if(!adapter) view.rows.emplace_back("No Bluetooth adapter detected"); }
            break;
        case PanelKind::Tailscale:
            view.title="Tailscale";
            if(logoutConfirm) view.rows={"Confirm sign out","Cancel"};
            else { view.rows={"Refresh connection status","Sign in to Tailscale","Sign out"}; if(!authURL.empty()) view.rows.emplace_back("Open sign-in page in browser"); }
            break;
        case PanelKind::Moonlight:
            view.title="Moonlight streaming";
            view.rows={host.empty()?"Set Sunshine host address":"Host: "+host,"Pair with this host","Refresh host applications","Discover a Sunshine host on this LAN","Resolution: "+moonSize()+" (change preset)","Custom resolution","Frame rate: "+std::to_string(moonFps)+" FPS (change)","Bitrate: "+(moonBitrate?std::to_string(moonBitrate/1000)+" Mbps":"automatic")+" (change)","Decoder: software H.264 via SDL / Wayland"};
            for(const auto& app:apps) view.rows.push_back("Stream: "+app);
            break;
        case PanelKind::Install:
            view.title="Install Surface OS";
            if(installStage==InstallStage::Review) {
                view.rows={"Target: "+plannedTarget.model+" ("+plannedTarget.path+")","Disk: "+std::to_string(plannedTarget.bytes/(1024*1024))+" MiB | DATA grows from 4096 to "+std::to_string(plannedDataBytes/(1024*1024))+" MiB","Stable ID: "+plannedTarget.id,"Image SHA256: "+plannedSource.hash,"ALL FILES AND PARTITIONS ON THIS DISK WILL BE ERASED","Type exact confirmation to install","Back to disk selection"};
            } else if(installStage==InstallStage::Running) view.rows={"Verifying and installing the reviewed image","Keep power connected and the source USB inserted","This operation must finish before leaving the installer"};
            else if(installStage==InstallStage::Done) {
                if(installPowerConfirm) view.rows={"Confirm power off","Cancel"};
                else view.rows={"Power off, then remove USB before powering on","Return to HOME"};
            }
            else {
                view.rows={"Refresh disks and installer media",installSources.empty()?"No verified staged installer image found":"Source: "+installSources[installSourceIndex].path+" (select to change)"};
                for(const auto& target:installTargets) view.rows.push_back(target.model+" | "+target.path+" | "+std::to_string(target.bytes)+" bytes | "+target.id);
                if(installTargets.empty()) view.rows.emplace_back("No eligible unused internal disk detected");
            }
            break;
        case PanelKind::PokeMMO:
            view.title="PokeMMO";
            if(pokeRomConfirm) view.rows={"Copy "+selectedPokeRom.title+" ("+selectedPokeRom.code+") into the official client","Cancel"};
            else if(pokeConfirm) view.rows={"Download official client and pinned Java runtime","Cancel"};
            else {
                view.rows={"Refresh installation and imported ROM state",pokeInstalled?"Launch the official client":"Install PokeMMO (Internet download)","ROM source folder: "+pokeSource,"Scan for supported owner ROM headers","Current official ROM requirements","Open official download and terms"};
                for(const auto& rom:pokeRoms) view.rows.push_back("Import "+rom.title+" ["+rom.code+"] | "+fs::path(rom.path).filename().string());
            }
            break;
        case PanelKind::Library:
            view.title="Game library";
            view.rows={"Search games","Source folder: "+librarySource,"Scan source folder (local artwork only)","Scan and download matching cover artwork","Refresh external drives (read only)"};
            break;
        case PanelKind::DW3:
            view.title="Digimon World 3 Online";
            if(protonConfirm) view.rows={"Download Proton and Steam runtime, then test Windows command","Cancel"};
            else if(dw3Confirm) view.rows={"Import owner-provided client files","Cancel"};
            else view.rows={"Refresh imported-client and Windows runtime status","Source folder: "+dw3Source,"Import owner-provided DW3 installation",dw3Imported?"Launch imported client (compatibility untested)":"Launch requires imported client files","Required source files","Runner: "+std::string(dw3Runner=="wine"?"Wine64 / OpenGL":"UMU-Proton / OpenGL fallback")+" (change)",protonReady?"Explicitly refresh Proton/Steam runtime":"Set up Proton/Steam runtime (download required)","Refresh Windows runtime status"};
            break;
        case PanelKind::Windows:
            view.title="Windows applications";
            if(protonConfirm) view.rows={"Download Proton and Steam runtime, then test Windows command","Cancel"};
            else {
                view.rows={windowsPath.empty()?"Choose executable path":"Executable: "+windowsPath,"Title: "+(windowsTitle.empty()?"enter application title":windowsTitle),"Runner: "+std::string(windowsRunner=="wine"?"Wine64 / OpenGL":"UMU-Proton / OpenGL fallback")+" (change)","Save application reference",protonReady?"Explicitly refresh Proton/Steam runtime":"Set up Proton/Steam runtime (download required)","Refresh saved applications and runtime status"};
                for(const auto& app:windowsApps) view.rows.push_back("Launch "+app.title+" ["+app.runner+"] (compatibility unverified)");
            }
            break;
        }
        view.selected=view.rows.empty()?0:std::min(selected,view.rows.size()-1);
    }
    void beginEdit(Edit purpose,const std::string& prompt,bool secret=false,std::string initial={}) {
        edit=purpose; view.editing=true; view.secret=secret; view.input=std::move(initial); view.keyboard_index=0; view.status=prompt;
    }
    void cancelEdit() { std::fill(view.input.begin(),view.input.end(),'\0'); view.input.clear(); view.editing=false; view.secret=false; edit=Edit::None; }
    void open(PanelKind kind) {
        if(command.running() && job==Job::InstallExecute) { view.status="Installation is active; keep power connected until verification finishes"; return; }
        command.cancel(); job=delayed=Job::None; cancelEdit(); panel=kind; opened=true; returnSettings=false;
        view={}; keyboard(); btDetail=false; logoutConfirm=false; systemInfo=false; protonConfirm=false; powerConfirm=Power::None; interface=wifiInterface(); rows();
        if(kind==PanelKind::Settings) start(Job::Mixer,{"amixer","scontrols"},"Reading audio controls");
        else if(kind==PanelKind::Network) {
            if(interface.empty()) view.status="No Wi-Fi radio is visible to the kernel; wired networking may still be available";
            else start(Job::WpaStatus,wpa({"status"}),"Reading Wi-Fi association");
        } else if(kind==PanelKind::Bluetooth) start(Job::BtShow,{"bluetoothctl","show"},"Detecting Bluetooth adapter");
        else if(kind==PanelKind::Tailscale) start(Job::TailStatus,tail({"status","--json"}),"Reading Tailscale daemon state");
        else if(kind==PanelKind::Moonlight) view.status=host.empty()?"Enter the IP or DNS name of your authorized Sunshine host":"Pair this device, then list the host's available applications";
        else if(kind==PanelKind::Install) {
            installStage=InstallStage::Disks; installPowerConfirm=false; rows();
            mutate(Job::InstallList,{"surface-install-device","--list"},"Checking unused internal disks and read-only installer media",90);
        } else if(kind==PanelKind::PokeMMO) {
            pokeConfirm=pokeRomConfirm=false; pokeInstalled=fs::is_regular_file("/data/apps/pokemmo/client/PokeMMO.sh") && access("/data/apps/pokemmo/jre/bin/java",X_OK)==0; rows();
            start(Job::PokeStatus,{"pokemmo-setup","--status"},"Checking the local PokeMMO installation");
        } else if(kind==PanelKind::DW3) {
            dw3Confirm=protonConfirm=false; rows(); start(Job::DW3Status,{"dw3-setup","--status"},"Checking locally imported DW3 files; Wine/Proton compatibility is unverified");
        } else if(kind==PanelKind::Windows) start(Job::WindowsList,{"surface-windows-runner","--list-exes"},"Reading explicitly registered Windows applications; no executable scan or launch occurs");
        else view.status="Game sources are read only. Cover downloads occur only after choosing the download action";
    }
    void browser(const std::string& url) {
        if(!allowed()) return;
        if(!executable("/usr/bin/surface-browser").empty()) launch=std::vector<std::string>{"/usr/bin/surface-browser",url};
        else if(!executable("cog").empty()) launch=std::vector<std::string>{executable("dbus-run-session"),"--",executable("cog"),"--platform=wl",url};
        else { view.status="The WebKit browser is not installed in this image"; return; }
        if(launch->front().empty()) { launch.reset(); view.status="Browser requires dbus-run-session in this image"; }
    }
    void brightness(int direction) {
        if(!allowed()) return;
        std::error_code error;
        for(const auto& device:fs::directory_iterator("/sys/class/backlight",error)) {
            try {
                const int maximum=std::stoi(readLine(device.path()/"max_brightness"));
                const int current=std::stoi(readLine(device.path()/"brightness"));
                if(maximum<=0) continue;
                const int target=std::clamp(current+direction*std::max(1,maximum/10),1,maximum);
                std::ofstream stream(device.path()/"brightness"); stream<<target; stream.flush();
                if(!stream) { view.status="Backlight driver rejected the change"; return; }
                const bool saved=writePrivate("/data/home/brightness",device.path().filename().string()+'\n'+std::to_string(target)+'\n');
                view.status="Brightness: "+std::to_string(target*100/maximum)+"%"+(saved?" | saved":" | could not save this setting"); return;
            } catch(const std::exception&) { continue; }
        }
        view.status="No writable backlight device is available on this hardware";
    }
    void scanWifi() {
        if(interface.empty()) { view.status="No Wi-Fi adapter detected"; return; }
        if(!allowed()) return;
        if(fs::exists(fs::path("/run/wpa_supplicant")/interface)) mutate(Job::WpaScan,wpa({"scan"}),"Scanning nearby Wi-Fi networks");
        else {
            if(!ensurePrivate("/data/home") || !ensurePrivate("/run/wpa_supplicant")) { view.status="Cannot prepare private Wi-Fi configuration"; return; }
            if(!fs::exists("/data/home/wpa_supplicant.conf") && !writePrivate("/data/home/wpa_supplicant.conf","ctrl_interface=/run/wpa_supplicant\nupdate_config=1\n")) { view.status="Cannot persist Wi-Fi settings"; return; }
            mutate(Job::WpaStart,{"wpa_supplicant","-B","-D","nl80211","-i",interface,"-c","/data/home/wpa_supplicant.conf"},"Starting Wi-Fi supplicant");
        }
    }
    void join() {
        if(!allowed()) return;
        if(selectedSSID.empty() || selectedSSID.size()>32) { view.status="Wi-Fi SSID must contain 1 to 32 bytes"; return; }
        // Retire only the DHCP process previously started by this UI. The
        // replacement client remains running to renew the new network's lease.
        const auto old=readLine("/run/udhcpc-surface-wifi.pid");
        if(!old.empty() && old.size()<10 && std::all_of(old.begin(),old.end(),[](unsigned char c){return std::isdigit(c);})) {
            try {
                const auto pid=static_cast<pid_t>(std::stol(old));
                std::ifstream stream("/proc/"+old+"/cmdline",std::ios::binary);
                std::string arguments((std::istreambuf_iterator<char>(stream)),{});
                if(pid>1 && arguments.find("udhcpc")!=std::string::npos && arguments.find(interface)!=std::string::npos) kill(pid,SIGTERM);
            } catch(const std::exception&) {}
        }
        mutate(Job::WpaAdd,wpa({"add_network"}),"Preparing Wi-Fi connection");
    }
    void activate() {
        const auto choice=view.selected;
        if(command.running()) {
            if(panel==PanelKind::Tailscale && choice==3 && !authURL.empty()) browser(authURL);
            else view.status=job==Job::InstallExecute?"Installation is active; keep power connected until verification finishes":"Operation in progress; Back cancels it";
            return;
        }
        switch(panel) {
        case PanelKind::Settings:
            if(powerConfirm!=Power::None) {
                const auto requested=powerConfirm; powerConfirm=Power::None; view.selected=0; rows();
                if(choice==0) {
                    if(requested==Power::Reboot) mutate(Job::Reboot,{"reboot"},"Restart requested");
                    else mutate(Job::Suspend,{"python3","-c","from pathlib import Path; p=Path('/sys/power/state'); modes=p.read_text().split(); assert 'mem' in modes, 'Kernel does not expose suspend-to-RAM'; f=p.open('w'); f.write('mem'); f.close(); print('Device resumed from suspend', flush=True)"},"Suspending; use the hardware power button to resume",30);
                } else view.status="Power action cancelled";
            } else if(systemInfo) { systemInfo=false; view.selected=11; rows(); }
            else if(choice<3) mutate(Job::Volume,{"amixer","sset",mixer,choice==0?"5%-":choice==1?"5%+":"toggle"},"Changing audio volume");
            else if(choice<5) brightness(choice==3?-1:1);
            else if(choice<9) { const auto next=choice==5?PanelKind::Network:choice==6?PanelKind::Bluetooth:choice==7?PanelKind::Tailscale:PanelKind::Moonlight; open(next); returnSettings=true; }
            else if(choice==9) {
                auto url=readLine("/data/home/browser-url");
                if((!url.starts_with("https://") && !url.starts_with("http://")) || url.size()>512) url="https://www.google.com/";
                beginEdit(Edit::BrowserURL,"Enter an https:// or http:// web address",false,std::move(url));
            }
            else if(choice==10) { open(PanelKind::Install); returnSettings=true; }
            else if(choice==11) { systemInfo=true; view.selected=6; rows(); view.status="Storage reports mounted filesystem capacity. Unallocated internal disk space is not expanded by the installer"; }
            else if(choice<14) { powerConfirm=choice==12?Power::Reboot:Power::Suspend; view.selected=1; rows(); view.status=powerConfirm==Power::Reboot?"Restart this device now? Save and close any running game first":"Suspend this device now? Hardware wake support depends on the running kernel and device"; }
            else if(choice==14 && allowed()) {
                const auto program=executable("surface-controller-setup");
                if(program.empty()) view.status="The SDL controller mapping wizard is not installed in this image";
                else launch=std::vector<std::string>{program};
            } else if(choice==15) { open(PanelKind::Windows); returnSettings=true; }
            break;
        case PanelKind::Network:
            if(choice==0) scanWifi();
            else if(choice==1) beginEdit(Edit::HiddenSSID,"Enter hidden Wi-Fi network name");
            else if(choice==2) mutate(Job::WpaDisconnect,wpa({"disconnect"}),"Disconnecting Wi-Fi");
            else if(choice==3) {
                if(networkId.empty()) view.status="No current saved network is identified";
                else mutate(Job::WpaForget,wpa({}),"Removing saved Wi-Fi network",15,"remove_network "+networkId+"\nsave_config\nquit\n");
            } else if(choice>=5 && choice-5<networks.size()) {
                selectedSSID=networks[choice-5].ssid; selectedFlags=networks[choice-5].flags;
                if(selectedFlags.find("EAP")!=std::string::npos || selectedFlags.find("WEP")!=std::string::npos) view.status="Enterprise and legacy WEP networks require administrator provisioning";
                else if(selectedFlags.find("PSK")!=std::string::npos || selectedFlags.find("SAE")!=std::string::npos) beginEdit(Edit::WifiPassword,"Password for "+selectedSSID,true);
                else { password.clear(); join(); }
            }
            break;
        case PanelKind::Bluetooth:
            if(!btDetail) {
                if(choice==0) { if(!adapter) view.status="No Bluetooth adapter detected"; else mutate(Job::BtPower,{"bluetoothctl","power","on"},"Enabling Bluetooth radio"); }
                else if(choice==1) start(Job::BtShow,{"bluetoothctl","show"},"Detecting Bluetooth adapter");
                else if(choice-2<devices.size()) { btMac=devices[choice-2].mac; btName=devices[choice-2].name; btDetail=true; view.selected=0; rows(); start(Job::BtInfo,{"bluetoothctl","info",btMac},"Reading controller state"); }
            } else if(validMac(btMac)) {
                if(choice==0) mutate(Job::BtPair,{"bluetoothctl","--agent","NoInputNoOutput","--timeout","35","pair",btMac},"Pairing controller; keep it in pairing mode",40);
                else if(choice==1) mutate(Job::BtConnect,{"bluetoothctl","connect",btMac},"Connecting controller",25);
                else if(choice==2) mutate(Job::BtDisconnect,{"bluetoothctl","disconnect",btMac},"Disconnecting controller");
                else if(choice==3) mutate(Job::BtForget,{"bluetoothctl","remove",btMac},"Forgetting controller");
                else { btDetail=false; view.selected=0; rows(); }
            }
            break;
        case PanelKind::Tailscale:
            if(logoutConfirm) { logoutConfirm=false; rows(); if(choice==0) mutate(Job::TailLogout,tail({"logout"}),"Signing this device out of Tailscale"); }
            else if(choice==0) start(Job::TailStatus,tail({"status","--json"}),"Reading Tailscale daemon state");
            else if(choice==1) mutate(Job::TailLogin,tail({"up","--accept-dns=true","--timeout=90s"}),"Requesting a Tailscale sign-in link",95);
            else if(choice==2) { logoutConfirm=true; view.selected=1; rows(); view.status="Signing out disconnects this device from its tailnet"; }
            else if(choice==3 && !authURL.empty()) browser(authURL);
            break;
        case PanelKind::Moonlight:
            if(choice==0) beginEdit(Edit::MoonHost,"Enter the IP or DNS name of your Sunshine host",false,host);
            else if(choice==3) {
                if(!allowed()) break;
                if(!ensurePrivate("/data/moonlight")) { view.status="Cannot prepare private Moonlight state"; break; }
                mutate(Job::MoonDiscover,{"moonlight","list","-keydir","/data/moonlight"},"Searching the local network using Moonlight's Avahi discovery",20);
            } else if(choice==4) {
                const std::array<std::pair<unsigned,unsigned>,4> presets{{{1280,720},{1280,800},{1800,1200},{1920,1080}}};
                auto next=presets.begin();
                const auto found=std::find(presets.begin(),presets.end(),std::pair{moonWidth,moonHeight});
                if(found!=presets.end()) next=found+1==presets.end()?presets.begin():found+1;
                saveMoon(next->first,next->second,moonFps,moonBitrate);
            } else if(choice==5) beginEdit(Edit::MoonResolution,"Enter even width x height, for example 1280x800. Sunshine must support it",false,moonSize());
            else if(choice==6) saveMoon(moonWidth,moonHeight,moonFps==60?30:60,moonBitrate);
            else if(choice==7) {
                const std::array<unsigned,5> rates{{0,5000,10000,20000,40000}};
                const auto found=std::find(rates.begin(),rates.end(),moonBitrate);
                const auto next=found==rates.end() || found+1==rates.end()?rates.begin():found+1;
                saveMoon(moonWidth,moonHeight,moonFps,*next);
            } else if(choice==8) view.status="This build decodes H.264 in software through FFmpeg and SDL/Wayland. Hardware video decoding has not been implemented or verified";
            else if(!validHost(host)) view.status="Set or discover a valid Sunshine host address first";
            else if(choice==1 || choice==2) {
                if(!allowed()) break;
                if(!ensurePrivate("/data/moonlight")) { view.status="Cannot create private Moonlight pairing state"; break; }
                mutate(choice==1?Job::MoonPair:Job::MoonApps,moon(choice==1?"pair":"list"),choice==1?"Pairing: waiting for the PIN to enter on your Sunshine PC":"Requesting actual applications from the host",choice==1?65:20);
            } else if(choice>=9 && choice-9<apps.size()) {
                if(!allowed()) break;
                const auto program=executable("moonlight"),env=executable("env");
                if(program.empty() || env.empty()) { view.status="Moonlight is not installed in this image"; break; }
                launch=std::vector<std::string>{env,"SDL_VIDEODRIVER=wayland","SDL_RENDER_DRIVER=opengles2",program,"stream","-platform","sdl","-codec","h264","-width",std::to_string(moonWidth),"-height",std::to_string(moonHeight),"-fps",std::to_string(moonFps),"-app",apps[choice-9],"-keydir","/data/moonlight"};
                if(moonBitrate) launch->insert(launch->end(),{"-bitrate",std::to_string(moonBitrate)});
                if((moonWidth!=1280 || moonHeight!=720) && (moonWidth!=1920 || moonHeight!=1080)) launch->push_back("-nosops");
                launch->push_back(host);
                view.status="Starting stream; Ctrl+Alt+Shift+Q or Play+Back+L1+R1 returns HOME";
            }
            break;
        case PanelKind::Install:
            if(installStage==InstallStage::Disks) {
                if(choice==0) mutate(Job::InstallList,{"surface-install-device","--list"},"Checking unused internal disks and read-only installer media",90);
                else if(choice==1 && !installSources.empty()) { installSourceIndex=(installSourceIndex+1)%installSources.size(); rows(); }
                else if(choice>=2 && choice-2<installTargets.size()) {
                    if(installSources.empty()) { view.status="No staged surface-go.img plus trusted SHA256 sidecar is available on installer media"; break; }
                    plannedTarget=installTargets[choice-2]; plannedSource=installSources[installSourceIndex];
                    mutate(Job::InstallPlan,{"surface-install-device","--plan","--target",plannedTarget.path,"--source",plannedSource.path,"--expected-target-id",plannedTarget.id,"--expected-source-sha256",plannedSource.hash},"Preparing a read-only installation review",30);
                }
            } else if(installStage==InstallStage::Review) {
                if(choice==5) beginEdit(Edit::InstallConfirm,"Type exactly: ERASE "+plannedTarget.id+" | This permanently erases the reviewed internal disk");
                else if(choice==6) { installStage=InstallStage::Disks; view.selected=0; rows(); }
            } else if(installStage==InstallStage::Done) {
                if(installPowerConfirm) {
                    installPowerConfirm=false; view.selected=1; rows();
                    if(choice==0) mutate(Job::InstallPoweroff,{"poweroff"},"Powering off. Remove USB only after shutdown, then power the Surface on");
                    else view.status="Power off cancelled. Keep the running USB boot media connected";
                } else if(choice==0) { installPowerConfirm=true; view.selected=1; rows(); view.status="Power off now? After shutdown, remove USB boot media before powering the Surface on to boot internal storage"; }
                else opened=false;
            }
            break;
        case PanelKind::PokeMMO:
            if(pokeRomConfirm) {
                pokeRomConfirm=false; rows();
                if(choice==0) mutate(Job::PokeImport,{"pokemmo-setup","--import-rom",selectedPokeRom.path},"Verifying and copying the selected owner-provided ROM; source stays read only",300);
            } else if(pokeConfirm) {
                pokeConfirm=false; rows();
                if(choice==0) mutate(Job::PokeInstall,{"pokemmo-setup","--install"},"Downloading and verifying the official client and Java runtime",600);
            } else if(choice==0) start(Job::PokeStatus,{"pokemmo-setup","--status"},"Checking the local PokeMMO installation");
            else if(choice==1) {
                if(pokeInstalled) {
                    if(allowed()) { const auto program=executable("pokemmo-launch"); if(program.empty()) view.status="The PokeMMO launcher is not installed"; else launch=std::vector<std::string>{program}; }
                } else { pokeConfirm=true; view.selected=1; rows(); view.status="Download the unmodified official PokeMMO client and pinned Java runtime? Requires Internet and at least 2 GiB free on DATA. Official account and game files are supplied by you"; }
            } else if(choice==2) beginEdit(Edit::PokeSource,"Choose a mounted ROM folder below /library/roms, /media, /run/media or /data/imports",false,pokeSource);
            else if(choice==3) mutate(Job::PokeScan,{"pokemmo-setup","--scan-roms",pokeSource},"Reading supported cartridge headers; no ROM is copied by scanning",40);
            else if(choice==4) view.status="Black or White 1 is required. FireRed enables Kanto; FireRed plus Emerald enables Hoenn; Platinum enables Sinnoh; HeartGold or SoulSilver enables Johto. Scan, select one file, and confirm its copy. Header recognition does not prove an unmodified compatible dump; the official client validates it in Client Management";
            else if(choice==5) browser("https://pokemmo.com/en/downloads/");
            else if(choice>=6 && choice-6<pokeRoms.size()) {
                if(!pokeInstalled) { view.status="Install the official client before importing ROMs"; break; }
                selectedPokeRom=pokeRoms[choice-6]; pokeRomConfirm=true; view.selected=1; rows();
                view.status="Copy "+std::to_string(selectedPokeRom.bytes)+" bytes from "+selectedPokeRom.path+"? Existing files and the source are preserved. The official client must validate compatibility; no account credentials are handled here";
            }
            break;
        case PanelKind::Library:
            if(choice==0) beginEdit(Edit::LibrarySearch,"Search installed catalog titles (an empty query clears the filter)");
            else if(choice==1) beginEdit(Edit::LibrarySource,"Enter the absolute path to your mounted game folder",false,librarySource);
            else if(choice==2 || choice==3) {
                std::vector<std::string> arguments{"surface-library-scan","--root",librarySource,"--output","/data/library","--cache","/data/artwork"};
                if(choice==3) arguments.emplace_back("--fetch-covers");
                mutate(Job::LibraryScan,std::move(arguments),choice==3?"Scanning game references and downloading exact matching cover artwork":"Scanning game references and local artwork; source games remain read only",600);
            } else if(choice==4) mutate(Job::LibraryVolumes,{"surface-library-volumes","--mount-readonly"},"Discovering external game volumes and verifying read-only mounts",40);
            break;
        case PanelKind::DW3:
            if(protonConfirm) {
                protonConfirm=false; rows();
                if(choice==0) mutate(Job::WindowsSetup,{"surface-windows-runner","--setup-proton"},"Checking free DATA space before explicit Proton/Steam runtime setup",1250);
            } else if(dw3Confirm) {
                dw3Confirm=false; rows();
                if(choice==0) mutate(Job::DW3Import,{"dw3-setup","--import",dw3Source},"Importing unmodified owner-provided DW3 files",600);
            } else if(choice==0) start(Job::DW3Status,{"dw3-setup","--status"},"Checking locally imported DW3 files");
            else if(choice==1) beginEdit(Edit::DW3Source,"Enter the mounted folder containing your DW3 Online installation",false,dw3Source);
            else if(choice==2) { dw3Confirm=true; view.selected=1; rows(); view.status="Copy your existing DW3 installation into private DATA storage? Source files remain unchanged. Wine/runtime compatibility has not been verified"; }
            else if(choice==3) {
                if(!dw3Imported) { view.status="Import your existing client files before launching"; break; }
                if(dw3Runner=="proton" && !protonReady) { view.status="Choose Set up Proton/Steam runtime first. No first-run runtime download is performed by the launch action"; break; }
                if(allowed()) { const auto program=executable("surface-windows-runner"); if(program.empty()) view.status="The guarded Windows runner is not installed"; else launch=std::vector<std::string>{program,"--launch-dw3","--runner",dw3Runner}; }
            } else if(choice==4) view.status="Required: DW3 Online.exe, app/client.jar, app/dwcofre.dll, runtime/bin/java.dll and data/data.pak. Import your own complete installation. Both Wine and Proton gameplay remain unverified";
            else if(choice==5) {
                if(!allowed()) break;
                const auto next=dw3Runner=="wine"?"proton":"wine";
                if(!writePrivate("/data/home/dw3-runner",std::string(next)+'\n')) { view.status="Could not save Windows runner selection"; break; }
                dw3Runner=next; rows(); view.status="Runner saved. Wine and Proton use separate private prefixes and run as gaming; DW3 gameplay compatibility is unverified";
            } else if(choice==6) { protonConfirm=true; view.selected=1; rows(); view.status="Download the current official UMU-Proton and required Steam runtime? Needs at least 6 GiB free on DATA plus Internet. The launcher is pinned; these user-selected runtime downloads are mutable and logged. A Windows command test does not verify DW3 gameplay"; }
            else if(choice==7) start(Job::WindowsStatus,{"surface-windows-runner","--status"},"Reading installed Windows runner artifacts and free storage");
            break;
        case PanelKind::Windows:
            if(protonConfirm) {
                protonConfirm=false; rows();
                if(choice==0) mutate(Job::WindowsSetup,{"surface-windows-runner","--setup-proton"},"Checking free DATA space before explicit Proton/Steam runtime setup",1250);
            } else if(choice==0) beginEdit(Edit::WindowsPath,"Enter the exact .exe path on read-only mounted media or below /data/windows/apps",false,windowsPath);
            else if(choice==1) beginEdit(Edit::WindowsTitle,"Enter a title for this application reference",false,windowsTitle);
            else if(choice==2) { windowsRunner=windowsRunner=="wine"?"proton":"wine"; rows(); view.status="Runner selection applies when saving a reference. Compatibility must be tested separately for each application"; }
            else if(choice==3) {
                if(windowsPath.empty() || windowsTitle.empty()) { view.status="Choose an executable path and title first"; break; }
                mutate(Job::WindowsRegister,{"surface-windows-runner","--register-exe",windowsPath,"--title",windowsTitle,"--runner",windowsRunner},"Validating the selected PE executable and saving its literal application reference");
            } else if(choice==4) { protonConfirm=true; view.selected=1; rows(); view.status="Download current official UMU-Proton and Steam runtime? Requires at least 6 GiB free plus Internet. These mutable user downloads are logged. Setup tests a Windows command; application compatibility remains unverified"; }
            else if(choice==5) start(Job::WindowsList,{"surface-windows-runner","--list-exes"},"Reading saved Windows application references");
            else if(choice>=6 && choice-6<windowsApps.size()) {
                const auto& app=windowsApps[choice-6];
                if(app.runner=="proton" && !protonReady) { view.status="Set up Proton/Steam runtime explicitly before launching this application"; break; }
                if(!allowed()) break;
                const auto program=executable("surface-windows-runner");
                if(program.empty()) view.status="The Windows runner is not installed";
                else launch=std::vector<std::string>{program,"--launch-exe",app.path,"--runner",app.runner};
            }
            break;
        }
    }
    void completed(Job completedJob,int code,std::string output) {
        struct Wipe { std::string& value; ~Wipe() { std::fill(value.begin(),value.end(),'\0'); } } wipe{output};
        const bool ok=code==0;
        const bool failed=output.find("FAIL")!=std::string::npos || output.find("Failed")!=std::string::npos;
        const auto diagnostic=completedJob==Job::WpaConfigure?"Private Wi-Fi operation failed (command output suppressed)":brief(output);
        if(!ok && (completedJob==Job::InstallList || completedJob==Job::InstallPlan || completedJob==Job::InstallExecute)) {
            const auto list=lines(output); const auto message=list.empty()?std::string{}:jsonString(list.back(),"message");
            view.status="Installer stopped: "+(message.empty()?diagnostic:message);
            if(completedJob==Job::InstallExecute) { installStage=InstallStage::Disks; rows(); view.status+=" | Do not boot the target until installation has completed successfully"; }
            return;
        }
        if(!ok && completedJob==Job::WindowsSetup) { const auto list=lines(output); const auto message=list.empty()?std::string{}:jsonString(list.back(),"message"); view.status="Proton setup stopped: "+(message.empty()?diagnostic:message); return; }
        if(!ok && completedJob!=Job::TailStatus && completedJob!=Job::PokeStatus && completedJob!=Job::DW3Status && completedJob!=Job::MoonDiscover && completedJob!=Job::LibraryVolumes) { view.status=(code==124?"Operation timed out: ":"Operation failed: ")+diagnostic; return; }
        switch(completedJob) {
        case Job::Mixer:
            if(output.find("'Master'")!=std::string::npos) mixer="Master";
            else if(output.find("'PCM'")!=std::string::npos) mixer="PCM";
            else if(output.find("'Speaker'")!=std::string::npos) mixer="Speaker";
            else { view.status="No supported ALSA volume control is exposed by this device"; break; }
            view.status="Audio control: "+mixer; break;
        case Job::Volume:
            if(failed) { view.status=diagnostic; break; }
            if(!ensurePrivate("/data/home")) { view.status="Audio changed; cannot save the device's audio state"; break; }
            mutate(Job::AudioStore,{"alsactl","-f","/data/home/asound.state","store"},"Audio changed; saving persistent mixer state"); break;
        case Job::AudioStore:
            if(fs::is_regular_file("/data/home/asound.state") && chmod("/data/home/asound.state",0600)==0) view.status="Audio mixer state saved";
            else view.status="Audio changed, but its state could not be persisted";
            break;
        case Job::WpaStatus:
            ssid=field(output,"ssid"); networkId=field(output,"id"); rows();
            view.status=field(output,"wpa_state").empty()?"Wi-Fi supplicant is not running; choose Scan to initialize it":"Wi-Fi: "+field(output,"wpa_state")+(ssid.empty()?"":" | "+ssid); break;
        case Job::WpaStart:
            if(failed) { view.status=diagnostic; break; }
            mutate(Job::WpaScan,wpa({"scan"}),"Scanning nearby Wi-Fi networks"); break;
        case Job::WpaScan:
            if(output.find("OK")==std::string::npos && output.find("FAIL-BUSY")==std::string::npos) { view.status="Wi-Fi scan rejected: "+diagnostic; break; }
            delayed=Job::WpaResults; wake=Clock::now()+std::chrono::seconds(2); view.busy=true; view.status="Waiting for radio scan results"; break;
        case Job::WpaResults:
            networks.clear();
            for(const auto& line:lines(output)) {
                std::istringstream stream(line); std::string bssid,frequency,signal,flags,name;
                if(!std::getline(stream,bssid,'\t') || !validMac(bssid) || !std::getline(stream,frequency,'\t') || !std::getline(stream,signal,'\t') || !std::getline(stream,flags,'\t') || !std::getline(stream,name)) continue;
                name=decodeSSID(name);
                if(name.empty() || name.size()>32 || std::any_of(networks.begin(),networks.end(),[&](const Network& network){return network.ssid==name;})) continue;
                try { networks.push_back({name,flags,std::stoi(signal)}); } catch(const std::exception&) { continue; }
                if(networks.size()>=64) break;
            }
            std::sort(networks.begin(),networks.end(),[](const Network& a,const Network& b){return a.signal>b.signal;}); rows(); view.status="Found "+std::to_string(networks.size())+" Wi-Fi networks"; break;
        case Job::WpaAdd: {
            std::string id;
            for(const auto& line:lines(output)) if(!line.empty() && std::all_of(line.begin(),line.end(),[](unsigned char c){return std::isdigit(c);})) id=line;
            if(id.empty()) { view.status="Wi-Fi refused to create a network: "+diagnostic; break; }
            networkId=id;
            std::string payload="set_network "+id+" ssid "+hex(selectedSSID)+"\n";
            payload+="set_network "+id+" scan_ssid 1\n";
            if(selectedFlags.find("SAE")!=std::string::npos) payload+="set_network "+id+" key_mgmt SAE\nset_network "+id+" ieee80211w 2\nset_network "+id+" sae_password "+wpaQuote(password)+"\n";
            else if(!password.empty()) payload+="set_network "+id+" key_mgmt WPA-PSK\nset_network "+id+" psk "+(password.size()==64?password:wpaQuote(password))+"\n";
            else payload+="set_network "+id+" key_mgmt "+(selectedFlags.find("OWE")!=std::string::npos?"OWE":"NONE")+"\n";
            payload+="enable_network "+id+"\nselect_network "+id+"\nsave_config\nquit\n";
            std::fill(password.begin(),password.end(),'\0'); password.clear();
            mutate(Job::WpaConfigure,wpa({}),"Applying Wi-Fi credentials privately",15,std::move(payload)); break;
        }
        case Job::WpaConfigure:
            // Interactive wpa_cli can echo commands. Never show its output.
            if(failed) { view.status="Wi-Fi configuration was rejected; credentials were not logged. Check the password and supplicant configuration"; break; }
            attempts=0; delayed=Job::WpaWait; wake=Clock::now()+std::chrono::seconds(1); view.busy=true; view.status="Waiting for Wi-Fi authentication"; break;
        case Job::WpaWait:
            if(field(output,"wpa_state")=="COMPLETED") mutate(Job::Dhcp,{"udhcpc","-i",interface,"-b","-p","/run/udhcpc-surface-wifi.pid","-t","5","-T","2"},"Wi-Fi associated; requesting an IP address",18);
            else if(++attempts<15) { delayed=Job::WpaWait; wake=Clock::now()+std::chrono::seconds(1); view.busy=true; view.status="Authenticating with "+selectedSSID; }
            else view.status="Wi-Fi association did not complete; verify the password and radio signal";
            break;
        case Job::Dhcp:
            ssid=selectedSSID; rows(); view.status=address(interface).empty()?"Wi-Fi associated, but DHCP did not provide an IPv4 address":"Connected to "+ssid+" | "+address(interface); break;
        case Job::WpaDisconnect: if(!failed) ssid.clear(); rows(); view.status=failed?diagnostic:"Wi-Fi disconnected"; break;
        case Job::WpaForget: if(!failed) { ssid.clear(); networkId.clear(); } rows(); view.status=failed?"Removing/saving this network was rejected":"Saved network removed"; break;
        case Job::BtShow:
            adapter=output.find("Controller ")!=std::string::npos && output.find("No default controller")==std::string::npos; rows();
            if(adapter) start(Job::BtDevices,{"bluetoothctl","devices"},"Reading known controllers");
            else view.status="No Bluetooth adapter detected; check the hardware/driver or USB adapter";
            break;
        case Job::BtPower:
            if(failed) { view.status=diagnostic; break; }
            mutate(Job::BtScan,{"bluetoothctl","--timeout","8","scan","on"},"Scanning: put the controller into pairing mode",12); break;
        case Job::BtScan: start(Job::BtDevices,{"bluetoothctl","devices"},"Reading discovered controllers"); break;
        case Job::BtDevices:
            devices.clear();
            for(const auto& line:lines(output)) {
                const auto position=line.find("Device "); if(position==std::string::npos || line.size()<position+24) continue;
                const auto mac=line.substr(position+7,17); if(!validMac(mac)) continue;
                const auto name=line.size()>position+25?line.substr(position+25):mac;
                if(std::none_of(devices.begin(),devices.end(),[&](const Device& device){return device.mac==mac;})) devices.push_back({mac,name});
                if(devices.size()>=64) break;
            }
            rows(); view.status=devices.empty()?"No controllers found; activate the controller's pairing mode and scan again":"Select a controller to inspect, pair or connect"; break;
        case Job::BtInfo: {
            std::string information;
            for(const auto& line:lines(output)) if(line.find("Paired:")!=std::string::npos || line.find("Trusted:")!=std::string::npos || line.find("Connected:")!=std::string::npos) information+=line+" ";
            view.status=information.empty()?diagnostic:information; break;
        }
        case Job::BtPair:
            if(output.find("Pairing successful")==std::string::npos && output.find("AlreadyExists")==std::string::npos) { view.status="Pairing did not complete: "+diagnostic; break; }
            mutate(Job::BtTrust,{"bluetoothctl","trust",btMac},"Trusting paired controller"); break;
        case Job::BtTrust:
            if(failed) { view.status=diagnostic; break; }
            mutate(Job::BtConnect,{"bluetoothctl","connect",btMac},"Connecting paired controller",25); break;
        case Job::BtConnect: view.status=output.find("Connection successful")!=std::string::npos?"Controller connected: "+btName:"Connection not confirmed: "+diagnostic; break;
        case Job::BtDisconnect: view.status=failed?diagnostic:"Controller disconnected"; break;
        case Job::BtForget: view.status=failed?diagnostic:"Controller removed from saved devices"; btDetail=false; rows(); break;
        case Job::TailStatus: {
            const auto state=jsonString(output,"BackendState");
            if(state.empty()) view.status="Tailscale daemon is unavailable: "+diagnostic;
            else view.status="Tailscale: "+state+" | "+jsonString(output,"DNSName");
            const auto url=jsonString(output,"AuthURL"); if(url.starts_with("https://")) authURL=url;
            if(state=="Running") authURL.clear();
            rows(); break;
        }
        case Job::TailLogin:
            if(ok) { authURL.clear(); rows(); start(Job::TailStatus,tail({"status","--json"}),"Verifying signed-in Tailscale state"); }
            else view.status="Tailscale sign-in did not finish: "+diagnostic;
            break;
        case Job::TailLogout: authURL.clear(); rows(); view.status=ok?"This device is signed out of Tailscale":diagnostic; break;
        case Job::MoonPair:
            view.status=output.find("Succesfully paired")!=std::string::npos?"Moonlight pairing confirmed; refresh host applications":"Pairing was not confirmed: "+diagnostic; break;
        case Job::MoonDiscover: {
            std::string discovered;
            for(const auto& line:lines(output)) {
                if(!line.starts_with("Connecting to ") || !line.ends_with("...")) continue;
                const auto candidate=line.substr(14,line.size()-17);
                if(validHost(candidate)) discovered=candidate;
            }
            if(discovered.empty()) { view.status=code==124?"LAN discovery timed out. Enable Sunshine discovery on the PC, check the same LAN, or enter its address manually":"No host was discovered: "+diagnostic; break; }
            if(!allowed() || !writePrivate("/data/home/moonlight-host",discovered+'\n')) { view.status="A host was discovered, but its address could not be saved"; break; }
            host=std::move(discovered); apps.clear();
            if(!ok) { rows(); view.status="Discovered host "+host+"; pairing/application access is not confirmed. "+diagnostic; break; }
            [[fallthrough]];
        }
        case Job::MoonApps:
            apps.clear();
            for(const auto& line:lines(output)) {
                const auto dot=line.find(". ");
                if(dot==std::string::npos || dot==0 || !std::all_of(line.begin(),line.begin()+static_cast<std::ptrdiff_t>(dot),[](unsigned char c){return std::isdigit(c);})) continue;
                auto app=line.substr(dot+2); if(!app.empty() && apps.size()<64) apps.push_back(app);
            }
            rows(); view.status=apps.empty()?"No applications returned; verify host reachability and pairing: "+diagnostic:"Select an application to start a real H.264 stream"; break;
        case Job::InstallList: {
            installSources.clear(); installTargets.clear(); installSourceIndex=0; installStage=InstallStage::Disks;
            for(const auto& item:jsonObjects(output,"sources")) {
                const auto path=jsonString(item,"path"),hash=jsonString(item,"sha256");
                if(path.starts_with('/') && hash.size()==64) installSources.push_back({path,hash,jsonNumber(item,"bytes")});
            }
            std::string excluded;
            for(const auto& item:jsonObjects(output,"targets")) {
                if(jsonBool(item,"eligible")) installTargets.push_back({jsonString(item,"device"),jsonString(item,"stable_id"),jsonString(item,"model"),jsonNumber(item,"bytes")});
                else if(excluded.empty()) excluded=jsonString(item,"reason");
            }
            rows();
            view.status=installSources.empty()?"Staged installer image not found. Installer media needs SurfaceGoGamingOS/surface-go.img and surface-go.img.sha256":installTargets.empty()?"No safe internal target is available. "+excluded:"Select an internal disk to review. USB/boot/mounted disks are excluded. No disk has been erased";
            break;
        }
        case Job::InstallPlan:
            plannedDataBytes=jsonNumber(output,"data_proposed_bytes");
            if(jsonString(output,"event")!="plan" || jsonString(output,"confirmation")!="ERASE "+plannedTarget.id || jsonString(output,"target")!=plannedTarget.path || jsonString(output,"source_sha256")!=plannedSource.hash || jsonNumber(output,"data_original_bytes")!=4ULL*1024*1024*1024 || plannedDataBytes<=4ULL*1024*1024*1024 || plannedDataBytes>=plannedTarget.bytes) { view.status="Installer returned an invalid or changed review; refresh storage devices"; break; }
            installStage=InstallStage::Review; view.selected=6; rows();
            view.status="Review the disk, checksum and DATA capacity. Every existing file on this internal disk will be erased. DATA expands before its first mount. USB installer media is preserved"; break;
        case Job::InstallExecute: {
            const auto list=lines(output);
            if(list.empty() || jsonString(list.back(),"event")!="complete" || !jsonBool(list.back(),"data_expansion_verified") || jsonNumber(list.back(),"data_actual_bytes")!=plannedDataBytes) { view.status="Installation did not report verified copy and DATA expansion. Do not boot the target"; installStage=InstallStage::Disks; rows(); break; }
            installStage=InstallStage::Done; installPowerConfirm=false; view.selected=1; rows(); view.status="Image copy, readback, GPT and DATA expansion verified. Power off, remove USB boot media after shutdown, then power on to boot internal storage"; break;
        }
        case Job::InstallPoweroff: view.status="Power off requested. After shutdown, remove USB boot media, then power on"; break;
        case Job::Reboot: view.status="Restart requested"; break;
        case Job::Suspend: view.status="Device resumed from suspend"; break;
        case Job::PokeStatus:
            if(output.find("INSTALLED")==std::string::npos) { view.status="PokeMMO status could not be read: "+diagnostic; break; }
            pokeInstalled=output.find("NOT_INSTALLED")==std::string::npos && output.find("INSTALLED")!=std::string::npos; rows();
            if(pokeInstalled) start(Job::PokeRomStatus,{"pokemmo-setup","--rom-status"},"Checking imported ROM headers without network access");
            else view.status="PokeMMO is not installed. Installation requires an explicit download action";
            break;
        case Job::PokeInstall:
            pokeInstalled=fs::is_regular_file("/data/apps/pokemmo/client/PokeMMO.sh") && access("/data/apps/pokemmo/jre/bin/java",X_OK)==0; rows();
            view.status=pokeInstalled?"Official client installed. Launch it, sign in and select your own game files":"Setup exited without a usable client/runtime. "+diagnostic; break;
        case Job::PokeRomStatus: {
            if(jsonString(output,"event")!="rom-status") { view.status="Imported ROM status returned an invalid inventory"; break; }
            const auto imported=jsonObjects(output,"roms");
            view.status="Imported ROM headers: "+std::to_string(imported.size())+". "+(jsonBool(output,"black_white_present")?"Required Black/White header is present":"Required Black/White 1 is missing")+". Compatibility is validated by the official client's Client Management";
            if(jsonNumber(output,"warning_count")) view.status+=" | Some installed files failed header validation";
            break;
        }
        case Job::PokeScan:
            if(jsonString(output,"event")!="rom-scan") { view.status="ROM scanner returned an invalid inventory"; break; }
            pokeRoms.clear();
            for(const auto& item:jsonObjects(output,"roms")) {
                const auto path=jsonString(item,"path"),title=jsonString(item,"title"),code=jsonString(item,"code");
                if(path.starts_with('/') && !title.empty() && code.size()==4 && jsonNumber(item,"bytes")) pokeRoms.push_back({path,title,code,jsonNumber(item,"bytes")});
                if(pokeRoms.size()>=256) break;
            }
            rows(); view.status="Recognized "+std::to_string(pokeRoms.size())+" supported cartridge headers; "+std::to_string(jsonNumber(output,"warning_count"))+" rejected files/warnings. Select a file for a separate copy confirmation. Header checks do not prove client compatibility";
            break;
        case Job::PokeImport:
            start(Job::PokeRomStatus,{"pokemmo-setup","--rom-status"},"Import copy verified; checking installed ROM headers"); break;
        case Job::LibraryScan: {
            view.status="Catalog: "+std::to_string(jsonNumber(output,"games"))+" games, "+std::to_string(jsonNumber(output,"covers"))+" covers, "+std::to_string(jsonNumber(output,"warnings"))+" warnings. HOME will reload the saved catalog";
            if(jsonNumber(output,"warnings")) {
                const auto report=readTail("/data/library/catalog.json"); const auto position=report.rfind("\"errors\"");
                if(position!=std::string::npos) {
                    const auto first=report.find('"',report.find('[',position));
                    if(first!=std::string::npos) view.status+=" | "+jsonString("{\"item\":"+report.substr(first)+"}","item");
                }
            }
            break;
        }
        case Job::DW3Status:
            if(output.find("IMPORTED")==std::string::npos && output.find("IMPORT_REQUIRED")==std::string::npos) { view.status="DW3 status could not be read: "+diagnostic; break; }
            dw3Imported=output.find("IMPORTED")!=std::string::npos; rows();
            start(Job::WindowsStatus,{"surface-windows-runner","--status"},dw3Imported?"Imported client present; reading Windows runner setup state":"Client import required; reading Windows runner setup state"); break;
        case Job::DW3Import:
            start(Job::DW3Status,{"dw3-setup","--status"},"Checking the imported installation; gameplay compatibility remains unverified"); break;
        case Job::WindowsStatus:
            if(jsonString(output,"event")!="windows-status") { view.status="Windows runner returned an invalid status"; break; }
            protonReady=jsonBool(output,"proton_ready"); rows();
            view.status=(panel==PanelKind::DW3?std::string(dw3Imported?"DW3 imported. ":"DW3 import required. "):std::string{})+"Wine: "+(jsonBool(output,"wine_installed")?"installed":"missing")+"; UMU: "+(jsonBool(output,"umu_installed")?"installed":"missing")+"; Proton setup: "+(protonReady?"Windows command probe passed":"required")+". DATA free: "+std::to_string(jsonNumber(output,"free_bytes")/(1024*1024))+" MiB. Application/gameplay compatibility remains unverified";
            break;
        case Job::WindowsSetup: {
            const auto list=lines(output);
            if(list.empty() || jsonString(list.back(),"event")!="complete") { view.status="Proton setup did not report verified completion"; break; }
            start(Job::WindowsStatus,{"surface-windows-runner","--status"},"Verifying the recorded Proton/runtime artifacts and Windows command probe"); break;
        }
        case Job::LibraryVolumes: {
            const auto paths=jsonStrings(output,"mount_paths"),errors=jsonStrings(output,"errors");
            std::vector<std::string> valid;
            for(const auto& path:paths) if(path.starts_with('/')) valid.push_back(path);
            if(valid.empty()) { view.status="No usable read-only external game volume was confirmed"+(errors.empty()?std::string{}:" | "+errors.front()); break; }
            const auto underLibrary=std::any_of(valid.begin(),valid.end(),[](const std::string& path){return path.starts_with("/media/library/");});
            const auto source=underLibrary?std::string("/media/library"):valid.front();
            if(!allowed() || !writePrivate("/data/home/library-source",source+'\n')) { view.status="External volumes mounted read only; the scan source could not be saved"; break; }
            librarySource=source; rows(); view.status="Confirmed "+std::to_string(valid.size())+" read-only volume(s). Source saved: "+source+". Choose Scan to index games";
            if(!errors.empty()) view.status+=" | "+errors.front();
            break;
        }
        case Job::WindowsList:
            if(jsonString(output,"event")!="windows-apps") { view.status="The Windows registry returned an invalid inventory"; break; }
            windowsApps.clear();
            for(const auto& item:jsonObjects(output,"apps")) {
                const auto path=jsonString(item,"path"),title=jsonString(item,"title"),runner=jsonString(item,"runner");
                if(path.starts_with('/') && !title.empty() && (runner=="wine" || runner=="proton")) windowsApps.push_back({path,title,runner});
                if(windowsApps.size()>=128) break;
            }
            rows(); start(Job::WindowsStatus,{"surface-windows-runner","--status"},"Reading cached Windows runtime setup state"); break;
        case Job::WindowsRegister:
            if(jsonString(output,"event")!="registered") { view.status="The Windows runner did not confirm registry publication"; break; }
            start(Job::WindowsList,{"surface-windows-runner","--list-exes"},"Application reference saved; refreshing actual registry"); break;
        case Job::None: break;
        }
    }
    void update() {
        command.reap();
        if(delayed!=Job::None && Clock::now()>=wake && !command.running()) {
            const auto next=delayed; delayed=Job::None;
            start(next,wpa({next==Job::WpaResults?"scan_results":"status"}),next==Job::WpaResults?"Reading Wi-Fi scan results":"Checking Wi-Fi association");
        }
        if(command.running()) {
            const auto result=command.update();
            if(job==Job::InstallExecute) {
                const auto list=lines(command.text);
                if(!list.empty()) {
                    const auto phase=jsonString(list.back(),"phase");
                    if(!phase.empty()) view.status="Install: "+phase+" "+std::to_string(jsonNumber(list.back(),"percent"))+"% | Keep power connected";
                }
            } else if(job==Job::WindowsSetup) {
                const auto list=lines(command.text); if(!list.empty()) { const auto message=jsonString(list.back(),"message"); if(!message.empty()) view.status=message; }
            } else if(job==Job::PokeInstall || job==Job::LibraryScan || job==Job::DW3Import || job==Job::PokeImport) {
                if(!command.text.empty()) view.status=brief(command.text);
            } else if(job==Job::TailLogin) {
                const auto position=command.text.find("https://login.tailscale.com/");
                if(position!=std::string::npos) {
                    const auto end=command.text.find_first_of("\r\n\t ",position);
                    const auto url=command.text.substr(position,std::min<std::size_t>(end==std::string::npos?command.text.size()-position:end-position,1024));
                    if(url!=authURL) { authURL=url; rows(); }
                    view.status="Open this link and authorize this device: "+authURL;
                }
            } else if(job==Job::MoonPair) {
                const auto position=command.text.find("Please enter the following PIN on the target PC:");
                if(position!=std::string::npos) {
                    const auto end=command.text.find('\n',position);
                    view.status=clean(command.text.substr(position,end==std::string::npos?std::string::npos:end-position));
                }
            }
            if(result) {
                const auto previous=job; job=Job::None; view.busy=false;
                if(previous==Job::InstallExecute) view.critical=false;
                auto output=std::move(command.text); command.text.clear(); completed(previous,*result,std::move(output));
            }
        }
        view.busy=command.running() || delayed!=Job::None;
    }
    void submit() {
        if(!view.editing) return;
        const auto purpose=edit; std::string value=view.input; cancelEdit();
        switch(purpose) {
        case Edit::WifiPassword:
            if(value.empty() || (selectedFlags.find("SAE")==std::string::npos && value.size()<8) || value.size()>64 || (selectedFlags.find("SAE")!=std::string::npos && value.size()>63) ||
                (value.size()==64 && !std::all_of(value.begin(),value.end(),[](unsigned char c){return std::isxdigit(c);}))) {
                view.status="Use an 8–63 character WPA2 passphrase (or 64 hex digits); WPA3 allows 1–63 characters"; break;
            }
            password=std::move(value); join(); break;
        case Edit::HiddenSSID:
            if(value.empty() || value.size()>32) { view.status="SSID must contain 1 to 32 bytes"; break; }
            selectedSSID=value; selectedFlags="WPA-PSK"; beginEdit(Edit::WifiPassword,"Password for hidden network "+selectedSSID,true); break;
        case Edit::MoonHost:
            if(!validHost(value)) { view.status="Enter an IP address or DNS hostname, without spaces or a URL scheme"; break; }
            if(!allowed()) break;
            if(!writePrivate("/data/home/moonlight-host",value+'\n')) { view.status="Cannot persist Sunshine host address"; break; }
            host=std::move(value); apps.clear(); rows(); view.status="Host saved; pair this device before listing applications"; break;
        case Edit::MoonResolution: {
            unsigned width=0,height=0;
            if(!resolution(value,width,height)) view.status="Use even dimensions from 320x240 through 3840x2160, written as width x height without spaces";
            else saveMoon(width,height,moonFps,moonBitrate);
            break;
        }
        case Edit::BrowserURL:
            if(!value.starts_with("https://") && !value.starts_with("http://")) view.status="Use an https:// or http:// URL";
            else { if(allowed()) writePrivate("/data/home/browser-url",value+'\n'); browser(value); }
            break;
        case Edit::InstallConfirm:
            if(value!="ERASE "+plannedTarget.id || plannedTarget.id.empty()) { view.status="Confirmation did not match. No installation was started"; break; }
            if(mutate(Job::InstallExecute,{"surface-install-device","--execute","--target",plannedTarget.path,"--source",plannedSource.path,"--expected-target-id",plannedTarget.id,"--expected-source-sha256",plannedSource.hash,"--confirm",value},"Verifying the full source checksum before opening the target writable",3600)) {
                installStage=InstallStage::Running; view.critical=true; rows();
            }
            break;
        case Edit::LibrarySource:
            if(value.empty() || value.front()!='/') { view.status="Use the absolute path to a mounted game folder"; break; }
            if(!allowed()) break;
            if(!writePrivate("/data/home/library-source",value+'\n')) { view.status="Could not save the library source folder"; break; }
            librarySource=std::move(value); rows(); view.status="Source saved. Choose a scan action to update the catalog"; break;
        case Edit::LibrarySearch:
            search=value; opened=false; break;
        case Edit::DW3Source:
            if(value.empty() || value.front()!='/') { view.status="Use an absolute path to your mounted DW3 installation folder"; break; }
            if(!allowed()) break;
            if(!writePrivate("/data/home/dw3-source",value+'\n')) { view.status="Could not save the DW3 import source"; break; }
            dw3Source=std::move(value); rows(); view.status="Source saved; choose Import to copy owner-provided files"; break;
        case Edit::PokeSource:
            if(value.empty() || value.front()!='/') { view.status="Use an absolute path below an approved mounted ROM source"; break; }
            if(!allowed()) break;
            if(!writePrivate("/data/home/pokemmo-rom-source",value+'\n')) { view.status="Could not persist the ROM source folder"; break; }
            pokeSource=std::move(value); pokeRoms.clear(); rows(); view.status="Source saved; Scan reads headers only. Importing a selected file requires confirmation"; break;
        case Edit::WindowsPath:
            if(value.empty() || value.front()!='/') { view.status="Use the absolute path to your owner-provided Windows executable"; break; }
            windowsPath=std::move(value); if(windowsTitle.empty()) windowsTitle=fs::path(windowsPath).stem().string(); rows();
            view.status="Path selected in this panel only. Review the title/runner and choose Save to validate and register it"; break;
        case Edit::WindowsTitle:
            if(value.empty() || value.size()>100) { view.status="Use a nonempty title up to 100 UTF-8 bytes"; break; }
            windowsTitle=std::move(value); rows(); view.status="Title selected; Save validates and records this application reference"; break;
        case Edit::None: break;
        }
        std::fill(value.begin(),value.end(),'\0');
    }
    void navigate(Action action) {
        if(!opened) return;
        if(view.editing) {
            const auto count=view.keyboard.size(); auto& index=view.keyboard_index;
            if(action==Action::Left) index=(index+count-1)%count;
            else if(action==Action::Right) index=(index+1)%count;
            else if(action==Action::Up) index=index>=13?index-13:std::min(count-1,index+((count-1)/13)*13);
            else if(action==Action::Down) index=index+13<count?index+13:index%13;
            else if(action==Action::Back) { cancelEdit(); view.status="Input cancelled"; }
            else if(action==Action::Accept) {
                const auto key=view.keyboard[index];
                if(key=="Done") submit();
                else if(key=="Cancel") { cancelEdit(); view.status="Input cancelled"; }
                else if(key=="Delete") backspace();
                else if(key=="Shift") { shift=!shift; keyboard(); }
                else input(key=="Space"?" ":key);
            }
            return;
        }
        if(action==Action::Back) {
            if(command.running() && job==Job::InstallExecute) { view.status="Installation is active; keep power connected until verification finishes"; }
            else if(command.running() || delayed!=Job::None) { command.cancel(); job=delayed=Job::None; view.busy=false; view.status="Operation cancelled"; }
            else if(panel==PanelKind::Settings && (systemInfo || powerConfirm!=Power::None)) { systemInfo=false; powerConfirm=Power::None; view.selected=0; rows(); view.status="Returned to Settings"; }
            else if(panel==PanelKind::Tailscale && logoutConfirm) { logoutConfirm=false; view.selected=2; rows(); view.status="Sign out cancelled"; }
            else if(panel==PanelKind::PokeMMO && pokeConfirm) { pokeConfirm=false; view.selected=1; rows(); view.status="Installation cancelled"; }
            else if(panel==PanelKind::PokeMMO && pokeRomConfirm) { pokeRomConfirm=false; view.selected=3; rows(); view.status="ROM import cancelled"; }
            else if(panel==PanelKind::DW3 && dw3Confirm) { dw3Confirm=false; view.selected=2; rows(); view.status="Import cancelled"; }
            else if((panel==PanelKind::DW3 || panel==PanelKind::Windows) && protonConfirm) { protonConfirm=false; view.selected=panel==PanelKind::DW3?6:4; rows(); view.status="Proton setup cancelled"; }
            else if(panel==PanelKind::Install && installStage==InstallStage::Review) { installStage=InstallStage::Disks; view.selected=0; rows(); }
            else if(panel==PanelKind::Install && installPowerConfirm) { installPowerConfirm=false; view.selected=1; rows(); view.status="Power off cancelled. Keep the running USB boot media connected"; }
            else if(btDetail && panel==PanelKind::Bluetooth) { btDetail=false; view.selected=0; rows(); }
            else if(returnSettings) open(PanelKind::Settings);
            else opened=false;
        } else if(action==Action::Up || action==Action::Left) { if(!view.rows.empty()) view.selected=(view.selected+view.rows.size()-1)%view.rows.size(); }
        else if(action==Action::Down || action==Action::Right) { if(!view.rows.empty()) view.selected=(view.selected+1)%view.rows.size(); }
        else if(action==Action::Accept) activate();
    }
    void input(const std::string& text) {
        if(!view.editing) return;
        for(unsigned char c:text) if(c>=32 && c!=127 && view.input.size()<512) view.input+=static_cast<char>(c);
    }
    void backspace() {
        if(view.input.empty()) return;
        auto position=view.input.size()-1;
        while(position>0 && (static_cast<unsigned char>(view.input[position])&0xc0)==0x80) --position;
        view.input.erase(position);
    }
};

ControlCenter::ControlCenter():impl_(std::make_unique<Impl>()) {}
ControlCenter::~ControlCenter()=default;
void ControlCenter::open(PanelKind panel) { impl_->open(panel); }
bool ControlCenter::active() const { return impl_->opened; }
void ControlCenter::navigate(Action action) { impl_->navigate(action); }
void ControlCenter::inputText(std::string text) { impl_->input(text); }
void ControlCenter::backspace() { if(impl_->view.editing) impl_->backspace(); }
void ControlCenter::submitText() { impl_->submit(); }
void ControlCenter::update() { impl_->update(); }
const ControlView& ControlCenter::view() const { return impl_->view; }
std::optional<std::vector<std::string>> ControlCenter::takeLaunch() { auto result=std::move(impl_->launch); impl_->launch.reset(); return result; }
std::optional<std::string> ControlCenter::takeSearch() { auto result=std::move(impl_->search); impl_->search.reset(); return result; }
}
