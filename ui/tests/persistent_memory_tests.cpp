#include "surface/persistent_memory.hpp"
#include <array>
#include <cassert>
#include <fstream>
#include <iostream>

int main() {
    char name[]="/tmp/surface-memory-tests-XXXXXX";
    const auto* root=mkdtemp(name);assert(root);
    const std::filesystem::path directory(root),sram=directory/"save.srm",rtc=directory/"save.rtc";
    const std::array<unsigned char,8> original{1,2,3,4,5,6,7,8};
    {std::ofstream file(sram,std::ios::binary);file.write(reinterpret_cast<const char*>(original.data()),original.size());}
    surface::PersistentMemory memory;memory.path(sram);
    std::array<unsigned char,4> wrong{};
    bool refused=false;try {memory.load(wrong.data(),wrong.size());} catch(const std::runtime_error&) {refused=true;}
    assert(refused && !memory.save(wrong.data(),wrong.size()));
    std::array<unsigned char,8> loaded{};
    assert(memory.load(loaded.data(),loaded.size()) && loaded==original);
    refused=false;try {memory.save(wrong.data(),wrong.size());} catch(const std::runtime_error&) {refused=true;}
    assert(refused && !memory.save(loaded.data(),loaded.size()));
    surface::PersistentMemory clock;clock.path(rtc);
    assert(!clock.load(loaded.data(),loaded.size()));
    loaded[0]=77;assert(clock.save(loaded.data(),loaded.size()));
    std::array<unsigned char,8> restored{};
    surface::PersistentMemory restarted;restarted.path(rtc);
    assert(restarted.load(restored.data(),restored.size()) && restored==loaded);
    assert(memory.load(restored.data(),restored.size()) && restored==original);
    const auto link=directory/"link.srm";std::filesystem::create_symlink(sram,link);
    surface::PersistentMemory symlink;symlink.path(link);refused=false;
    try {symlink.load(restored.data(),restored.size());} catch(const std::runtime_error&) {refused=true;}
    assert(refused && !symlink.save(loaded.data(),loaded.size()));
    assert(memory.load(restored.data(),restored.size()) && restored==original);
    for(const auto& entry:std::filesystem::directory_iterator(directory)) assert(entry.path().extension()!=".tmp");
    std::filesystem::remove_all(directory);
    std::cout<<"Battery SRAM mismatch protection and independent RTC persistence pass\n";
}
