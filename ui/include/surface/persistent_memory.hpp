#pragma once
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace surface {
// A failed load must never turn an existing battery save into a fresh save.
// Each libretro memory region has its own file and successful-load latch.
class PersistentMemory {
    std::filesystem::path path_;
    std::size_t size_=0;
    bool writable_=false;
public:
    void path(std::filesystem::path value) { path_=std::move(value);writable_=false;size_=0; }
    bool load(void* memory,std::size_t size) {
        writable_=false;size_=0;
        if(!memory || !size) return false;
        if(size>64*1024*1024 || path_.empty()) throw std::runtime_error("Invalid battery save size or path");
        const int fd=open(path_.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
        if(fd<0) {
            if(errno!=ENOENT) throw std::runtime_error("Cannot read battery save; existing file preserved: "+path_.string());
            size_=size;writable_=true;return false;
        }
        try {
            struct stat info{};
            if(fstat(fd,&info) || !S_ISREG(info.st_mode) || info.st_size!=static_cast<off_t>(size))
                throw std::runtime_error("Battery save size or format mismatch; existing file preserved: "+path_.string());
            std::vector<char> bytes(size);std::size_t done=0;
            while(done<size) {
                const auto n=read(fd,bytes.data()+done,size-done);
                if(n<0 && errno==EINTR) continue;
                if(n<=0) throw std::runtime_error("Incomplete battery save; existing file preserved: "+path_.string());
                done+=static_cast<std::size_t>(n);
            }
            char extra;
            if(read(fd,&extra,1)!=0) throw std::runtime_error("Battery save changed during loading");
            std::memcpy(memory,bytes.data(),size);
        } catch(...) { close(fd);throw; }
        close(fd);size_=size;writable_=true;return true;
    }
    bool save(const void* memory,std::size_t size) {
        if(!writable_ || !memory || !size) return false;
        if(size!=size_) { writable_=false;throw std::runtime_error("Core battery memory size changed; existing save preserved"); }
        std::string temporary=path_.string()+".tmp-XXXXXX";
        const int fd=mkstemp(temporary.data());
        if(fd<0) throw std::runtime_error("Cannot create temporary battery save");
        bool closed=false;
        try {
            fcntl(fd,F_SETFD,FD_CLOEXEC);
            const auto* bytes=static_cast<const char*>(memory);std::size_t done=0;
            while(done<size) {
                const auto n=write(fd,bytes+done,size-done);
                if(n<0 && errno==EINTR) continue;
                if(n<=0) throw std::runtime_error("Incomplete battery save write");
                done+=static_cast<std::size_t>(n);
            }
            const bool synced=fsync(fd)==0;const bool ok=close(fd)==0;closed=true;
            if(!synced || !ok || rename(temporary.c_str(),path_.c_str())) throw std::runtime_error("Cannot commit battery save");
            const int directory=open(path_.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
            if(directory<0) throw std::runtime_error("Cannot sync battery save directory");
            const bool durable=fsync(directory)==0;close(directory);
            if(!durable) throw std::runtime_error("Cannot sync battery save directory");
        } catch(...) { if(!closed) close(fd);unlink(temporary.c_str());throw; }
        return true;
    }
};
}
