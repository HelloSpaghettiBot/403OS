#include "surface/controller_mapping.hpp"
#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace surface {
namespace {
bool guid_valid(std::string_view guid) {
    return guid.size()==32 && std::all_of(guid.begin(),guid.end(),[](unsigned char c){return std::isxdigit(c);});
}
bool clean(std::string_view value) {
    return !value.empty() && value.size()<=512 && std::all_of(value.begin(),value.end(),[](unsigned char c){return c>=32 && c!=127 && c!=',';});
}
bool key_valid(std::string_view key) {
    return !key.empty() && key.size()<64 && std::all_of(key.begin(),key.end(),[](unsigned char c){return std::isalnum(c) || c=='_' || c=='+' || c=='-';});
}
struct Descriptor {
    int value=-1;
    explicit Descriptor(int fd):value(fd) {}
    ~Descriptor(){if(value>=0)close(value);}
    Descriptor(const Descriptor&)=delete;
    Descriptor& operator=(const Descriptor&)=delete;
};
[[noreturn]] void failure(const std::string& operation) { throw std::runtime_error(operation+": "+std::strerror(errno)); }
}
std::optional<ControllerMapping> ControllerMapping::parse(std::string_view text) {
    if(text.size()>8192 || text.find('\n')!=std::string_view::npos || text.find('\r')!=std::string_view::npos) return {};
    const auto first=text.find(','),second=first==std::string_view::npos?first:text.find(',',first+1);
    if(first==std::string_view::npos || second==std::string_view::npos || !guid_valid(text.substr(0,first)) || !clean(text.substr(first+1,second-first-1)))return {};
    ControllerMapping result{std::string(text.substr(0,first)),std::string(text.substr(first+1,second-first-1)),{}};
    std::transform(result.guid.begin(),result.guid.end(),result.guid.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    for(auto at=second+1;at<text.size();) {
        auto end=text.find(',',at);if(end==std::string_view::npos)end=text.size();
        const auto field=text.substr(at,end-at);
        const auto colon=field.find(':');
        if(colon==std::string_view::npos || !key_valid(field.substr(0,colon)) || !clean(field.substr(colon+1)))return {};
        const auto key=field.substr(0,colon);
        if(std::any_of(result.fields.begin(),result.fields.end(),[&](const auto& entry){return entry.first==key;}))return {};
        result.fields.emplace_back(key,field.substr(colon+1));at=end+1;
    }
    return result;
}
std::string ControllerMapping::binding(std::string_view key) const {
    for(const auto& [name,value]:fields)if(name==key)return value;
    return {};
}
void ControllerMapping::set(std::string key,std::string value) {
    if(!key_valid(key) || !clean(value))throw std::runtime_error("Invalid controller binding");
    // A full-axis replacement supersedes pre-existing split-axis outputs.
    std::erase_if(fields,[&](const auto& entry){return entry.first==key || entry.first=="+"+key || entry.first=="-"+key;});
    fields.emplace_back(std::move(key),std::move(value));
}
std::string ControllerMapping::line() const {
    std::string result=guid+","+name+",";
    for(const auto& [key,value]:fields)result+=key+":"+value+",";
    if(!parse(result))throw std::runtime_error("Invalid controller mapping");
    return result;
}
std::optional<std::string> controller_axis_binding(ControllerInput target,int axis,int rest,int value) {
    if(axis<0 || axis>255 || rest<-32768 || rest>32767 || value<-32768 || value>32767 || std::abs(value-rest)<16000)return {};
    const auto raw="a"+std::to_string(axis);
    if(target==ControllerInput::Stick)return value>rest?raw:raw+"~";
    if(target==ControllerInput::Trigger && rest<-16000)return raw;
    if(target==ControllerInput::Trigger && rest>16000)return raw+"~";
    return (value>rest?"+":"-")+raw;
}
bool controller_image_allowed() {
    struct stat info{};
    if(lstat("/etc/surface-image-id",&info)!=0 || !S_ISREG(info.st_mode) || info.st_uid!=0 || (info.st_mode&0022))return false;
    std::string marker;std::getline(std::ifstream("/etc/surface-image-id"),marker);
    if(!marker.starts_with("surface-go-"))return false;
    std::ifstream mounts("/proc/mounts");std::string source,target,rest;
    while(mounts>>source>>target) {std::getline(mounts,rest);if(target=="/data")return true;}
    return false;
}
void save_controller_mapping(const std::filesystem::path& path,const ControllerMapping& mapping) {
    const auto replacement=mapping.line();
    if(mapping.binding("platform")!="Linux")throw std::runtime_error("Controller mapping must target Linux");
    const auto parent=path.parent_path();
    if(mkdir(parent.c_str(),0700)!=0 && errno!=EEXIST)failure("Create controller config directory");
    Descriptor directory(open(parent.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    struct stat directory_info{};
    if(directory.value<0 || fstat(directory.value,&directory_info)!=0)failure("Open controller config directory");
    if(directory_info.st_uid!=geteuid() || (directory_info.st_mode&0022))throw std::runtime_error("Controller config directory must have a private owner and no shared write access");
    const auto filename=path.filename().string();if(filename.empty() || filename=="." || filename=="..")throw std::runtime_error("Invalid mapping filename");
    std::string contents;
    Descriptor existing(openat(directory.value,filename.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC));
    if(existing.value<0 && errno!=ENOENT)failure("Open existing controller mappings");
    if(existing.value>=0) {
        struct stat info{};if(fstat(existing.value,&info)!=0)failure("Inspect controller mappings");
        if(!S_ISREG(info.st_mode) || info.st_uid!=geteuid() || (info.st_mode&0022) || info.st_size<0 || info.st_size>1024*1024)throw std::runtime_error("Existing controller mappings are unsafe or too large");
        contents.resize(static_cast<std::size_t>(info.st_size));std::size_t at=0;
        while(at<contents.size()) {const auto n=read(existing.value,contents.data()+at,contents.size()-at);if(n<0 && errno==EINTR)continue;if(n<=0)throw std::runtime_error("Cannot read complete controller mapping file");at+=static_cast<std::size_t>(n);}
    }
    std::string output;bool replaced=false;
    for(std::size_t at=0;at<contents.size();) {
        auto end=contents.find('\n',at);if(end==std::string::npos)end=contents.size();
        auto line=contents.substr(at,end-at);if(!line.empty() && line.back()=='\r')line.pop_back();
        const auto old=ControllerMapping::parse(line);
        if(old && old->guid==mapping.guid && (old->binding("platform").empty() || old->binding("platform")=="Linux")) {
            if(!replaced)output+=replacement+"\n";
            replaced=true;
        } else output+=line+"\n";
        at=end+1;
    }
    if(!replaced)output+=replacement+"\n";
    if(output.size()>1024*1024)throw std::runtime_error("Controller mapping database exceeds 1 MiB");
    static unsigned sequence=0;
    const auto temporary=".gamecontrollerdb."+std::to_string(getpid())+"."+std::to_string(++sequence);
    Descriptor destination(openat(directory.value,temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
    if(destination.value<0)failure("Create controller mapping temporary file");
    try {
        if(fchmod(destination.value,0600)!=0)failure("Protect controller mappings");
        std::size_t at=0;while(at<output.size()) {const auto n=write(destination.value,output.data()+at,output.size()-at);if(n<0 && errno==EINTR)continue;if(n<=0)failure("Write controller mappings");at+=static_cast<std::size_t>(n);}
        if(fsync(destination.value)!=0)failure("Flush controller mappings");
        if(renameat(directory.value,temporary.c_str(),directory.value,filename.c_str())!=0)failure("Replace controller mappings");
        if(fsync(directory.value)!=0)failure("Flush controller mapping directory");
    } catch(...) {unlinkat(directory.value,temporary.c_str(),0);throw;}
}
}
