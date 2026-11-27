#include "surface/library.hpp"
#include <algorithm>
#include <fstream>
#include <map>
#include <sstream>

namespace surface {
namespace {
struct System { const char *name,*core; };
const std::map<std::string,System> systems{
    {"nes",{"Nintendo Entertainment System","fceumm"}},
    {"snes",{"Super Nintendo","snes9x"}},
    {"gb",{"Game Boy","gambatte"}}, {"gbc",{"Game Boy Color","gambatte"}},
    {"gba",{"Game Boy Advance","mgba"}},
    {"genesis",{"Mega Drive / Genesis","genesis_plus_gx"}},
    {"ps1",{"PlayStation","pcsx_rearmed"}},
    {"n64",{"Nintendo 64","mupen64plus_next"}},
    {"nds",{"Nintendo DS","melondsds"}},
    {"dreamcast",{"Dreamcast","flycast"}}, {"psp",{"PlayStation Portable","ppsspp"}},
    {"gamecube",{"GameCube","dolphin"}}
};
}
Library read_library(const std::filesystem::path& catalog) {
    Library result;
    std::ifstream file(catalog);
    std::string line;
    while(std::getline(file,line)) {
        std::istringstream row(line);std::vector<std::string> parts;std::string value;
        while(std::getline(row,value,'\t')) parts.push_back(value);
        if(parts.size()<4 || parts[0].empty() || parts[1].empty()) continue;
        std::error_code error;
        if(!std::filesystem::is_regular_file(parts[3],error)) continue;
        const auto info=systems.find(parts[2]);
        const auto system_name=info==systems.end()?parts[2]:info->second.name;
        App app{parts[0],parts[1],system_name,"/usr/bin/surface-emulator"};
        app.game=true;app.system=system_name;app.rom_path=parts[3];if(parts.size()>4)app.artwork=parts[4];
        if(info==systems.end()) {
            app.executable.clear();app.subtitle+=" - no compatible engine";
        } else if(parts[2]=="gamecube") {
            app.executable="/usr/bin/dolphin-emu-nogui";
            app.arguments={"--platform=x11","-e",parts[3]};
        } else {
            app.arguments={"--core","/usr/lib/libretro/"+std::string(info->second.core)+"_libretro.so",
                           "--rom",parts[3],"--system-dir","/data/bios","--save-dir","/data/saves"};
        }
        result.games.push_back(std::move(app));
    }
    for(const auto& [id,info]:systems) {
        const auto count=std::count_if(result.games.begin(),result.games.end(),[&](const auto& app){return app.system==info.name;});
        App app{"system:"+id,info.name,std::to_string(count)+" games in your library",""};
        app.system=info.name;
        app.artwork="/usr/share/surface-home/system-"+id+".sgo";
        result.systems.push_back(std::move(app));
    }
    return result;
}
}
