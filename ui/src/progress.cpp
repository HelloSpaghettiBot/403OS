#include "surface/progress.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <fcntl.h>
#include <unistd.h>

namespace surface {
namespace {
bool valid(const std::string& id) { return !id.empty() && id.size()<256 && id.find_first_of("\t\r\n")==std::string::npos; }
}
ProgressStore::ProgressStore(std::filesystem::path file):path(std::move(file)) {
    std::ifstream stream(path);std::string line;
    while(entries.size()<100000 && std::getline(stream,line)) {
        if(line.size()>1024)continue;
        std::istringstream row(line);std::string id;Entry item;int flag=0;
        if(std::getline(row,id,'\t') && valid(id) && row>>flag>>item.seconds>>item.last && (flag==0 || flag==1)) {
            if(!(row>>item.preset) || item.preset>2) item.preset=0;
            item.favorite=flag==1;entries[id]=item;
        }
    }
}
void ProgressStore::apply(std::vector<App>& apps) const {
    for(auto& app:apps) if(auto it=entries.find(app.id);it!=entries.end()) {
        app.favorite=it->second.favorite;app.play_seconds=it->second.seconds;app.last_played=it->second.last;app.performance=it->second.preset;
    }
}
void ProgressStore::favorite(const std::string& id,bool value) {
    if(!valid(id))throw std::runtime_error("Invalid library ID");
    entries[id].favorite=value;save();
}
void ProgressStore::played(const std::string& id,std::uint64_t seconds,std::uint64_t timestamp) {
    if(!valid(id))throw std::runtime_error("Invalid library ID");
    auto& entry=entries[id];entry.seconds+=seconds;entry.last=timestamp;save();
}
void ProgressStore::performance(const std::string& id,unsigned preset) {
    if(!valid(id) || preset>2) throw std::runtime_error("Invalid performance preset");
    entries[id].preset=preset;save();
}
void ProgressStore::save() const {
    std::filesystem::create_directories(path.parent_path());
    std::ostringstream out;
    for(const auto& [id,e]:entries)out<<id<<'\t'<<int(e.favorite)<<'\t'<<e.seconds<<'\t'<<e.last<<'\t'<<e.preset<<'\n';
    const auto bytes=out.str(),temporary=path.string()+".new-"+std::to_string(getpid());
    const int fd=open(temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0)throw std::runtime_error("Cannot create library state transaction");
    std::size_t at=0;
    while(at<bytes.size()) {
        const auto n=write(fd,bytes.data()+at,bytes.size()-at);
        if(n<=0) {close(fd);unlink(temporary.c_str());throw std::runtime_error("Cannot write library state");}
        at+=std::size_t(n);
    }
    const bool synced=fsync(fd)==0,closed=close(fd)==0;
    if(!synced || !closed || rename(temporary.c_str(),path.c_str())!=0) {
        unlink(temporary.c_str());throw std::runtime_error("Cannot commit library state");
    }
    const int directory=open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(directory>=0) {fsync(directory);close(directory);}
}
}
