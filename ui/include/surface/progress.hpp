#pragma once
#include "surface/model.hpp"
#include <filesystem>
#include <map>
namespace surface {
class ProgressStore {
public:
    explicit ProgressStore(std::filesystem::path path);
    void apply(std::vector<App>&) const;
    void favorite(const std::string& id,bool value);
    void played(const std::string& id,std::uint64_t seconds,std::uint64_t timestamp);
    void performance(const std::string& id,unsigned preset);
private:
    struct Entry { bool favorite=false;std::uint64_t seconds=0,last=0;unsigned preset=0; };
    std::filesystem::path path;
    std::map<std::string,Entry> entries;
    void save() const;
};
}
