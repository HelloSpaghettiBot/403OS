#include "surface/controller_mapping.hpp"
#include <SDL3/SDL.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

using namespace surface;
namespace fs=std::filesystem;
#define CHECK(value) do {if(!(value))throw std::runtime_error(#value);}while(false)
std::string read(const fs::path& path){std::ifstream file(path);return {std::istreambuf_iterator<char>(file),{}};}
int main() {
    try {
        const std::string guid="03000000091200000304000000000000";
        auto parsed=ControllerMapping::parse(guid+",Original Pad,a:b0,b:b1,leftx:a0,lefty:a1~,lefttrigger:+a4,platform:Linux,hint:SDL_GAMECONTROLLER_USE_BUTTON_LABELS:=1,");
        CHECK(parsed);const auto skipped_axis=parsed->binding("leftx");
        // Reassigning a face button must leave skipped analog bindings and SDL metadata intact.
        parsed->set("a","b3");CHECK(parsed->binding("leftx")==skipped_axis);CHECK(parsed->binding("lefty")=="a1~");
        CHECK(parsed->binding("hint")=="SDL_GAMECONTROLLER_USE_BUTTON_LABELS:=1");
        auto roundtrip=ControllerMapping::parse(parsed->line());CHECK(roundtrip && roundtrip->binding("a")=="b3");
        CHECK(!ControllerMapping::parse("not-a-guid,Pad,a:b0,"));
        CHECK(!ControllerMapping::parse(guid+",Bad\nName,a:b0,"));
        CHECK(!ControllerMapping::parse(guid+",Pad,a:b0,a:b1,"));
        CHECK(!ControllerMapping::parse(guid+",Pad,broken,"));
        CHECK(!ControllerMapping::parse(std::string(9000,'a')));
        auto split=ControllerMapping::parse(guid+",Split,+leftx:+a0,-leftx:-a1,a:b0,platform:Linux,");CHECK(split);
        split->set("leftx","a2~");CHECK(split->binding("+leftx").empty() && split->binding("-leftx").empty());
        CHECK(controller_axis_binding(ControllerInput::Stick,0,0,28000)=="a0");
        CHECK(controller_axis_binding(ControllerInput::Stick,1,0,-28000)=="a1~");
        CHECK(!controller_axis_binding(ControllerInput::Stick,0,500,2200));
        CHECK(controller_axis_binding(ControllerInput::Trigger,4,-32768,28000)=="a4");
        CHECK(controller_axis_binding(ControllerInput::Trigger,5,32767,-28000)=="a5~");
        CHECK(controller_axis_binding(ControllerInput::Trigger,4,0,28000)=="+a4");
        CHECK(controller_axis_binding(ControllerInput::Button,0,0,-28000)=="-a0");

        const auto directory=fs::temp_directory_path()/("surface-controller-test-"+std::to_string(getpid()));
        fs::create_directory(directory);chmod(directory.c_str(),0700);
        const auto database=directory/"gamecontrollerdb.txt";
        const std::string other="03000000091200000404000000000000,Other,a:b1,platform:Linux,";
        {std::ofstream file(database);file<<"# private user mappings\n"<<other<<'\n'<<guid<<",Old,a:b1,platform:Linux,\n";}
        save_controller_mapping(database,*parsed);
        const auto saved=read(database);CHECK(saved.find(other)!=std::string::npos);CHECK(saved.find("# private user mappings")!=std::string::npos);
        CHECK(saved.find(",Old,")==std::string::npos && saved.find(parsed->line())!=std::string::npos);
        struct stat info{};CHECK(stat(database.c_str(),&info)==0 && (info.st_mode&0777)==0600);
        save_controller_mapping(database,*parsed);CHECK(read(database)==saved);
        const auto symlink=directory/"unsafe.txt";fs::create_symlink(database,symlink);
        bool rejected=false;try{save_controller_mapping(symlink,*parsed);}catch(const std::exception&){rejected=true;}CHECK(rejected && read(database)==saved);

        // This uses SDL's actual mapping machinery without SDL_INIT_VIDEO or a window.
        SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI,"0");
        CHECK(SDL_Init(SDL_INIT_JOYSTICK|SDL_INIT_GAMEPAD));
        SDL_VirtualJoystickDesc description{};SDL_INIT_INTERFACE(&description);
        description.type=SDL_JOYSTICK_TYPE_UNKNOWN;description.vendor_id=0x1209;description.product_id=0x0403;
        description.naxes=6;description.nbuttons=16;description.nhats=1;description.name="Surface Mapping Test";
        const auto id=SDL_AttachVirtualJoystick(&description);CHECK(id!=0);
        auto* joystick=SDL_OpenJoystick(id);CHECK(joystick);
        char actual_guid[33]{};SDL_GUIDToString(SDL_GetJoystickGUID(joystick),actual_guid,sizeof(actual_guid));
        ControllerMapping actual{actual_guid,"Surface Mapping Test",{}};
        for(const auto& [key,value]:std::vector<std::pair<std::string,std::string>>{{"a","b3"},{"b","b1"},{"dpup","h0.1"},{"dpdown","h0.4"},{"dpleft","h0.8"},{"dpright","h0.2"},{"leftx","a0~"},{"lefty","a1"},{"lefttrigger","+a4"},{"platform","Linux"}})actual.set(key,value);
        save_controller_mapping(database,actual);
        CHECK(SDL_AddGamepadMappingsFromFile(database.c_str())>=1);
        CHECK(SDL_IsGamepad(id));auto* gamepad=SDL_OpenGamepad(id);CHECK(gamepad);
        CHECK(SDL_SetJoystickVirtualButton(joystick,3,true));
        CHECK(SDL_SetJoystickVirtualHat(joystick,0,SDL_HAT_LEFT));
        CHECK(SDL_SetJoystickVirtualAxis(joystick,0,-27000));
        CHECK(SDL_SetJoystickVirtualAxis(joystick,4,28000));
        SDL_UpdateJoysticks();
        CHECK(SDL_GetGamepadButton(gamepad,SDL_GAMEPAD_BUTTON_SOUTH));
        CHECK(SDL_GetGamepadButton(gamepad,SDL_GAMEPAD_BUTTON_DPAD_LEFT));
        CHECK(SDL_GetGamepadAxis(gamepad,SDL_GAMEPAD_AXIS_LEFTX)>26000);
        CHECK(SDL_GetGamepadAxis(gamepad,SDL_GAMEPAD_AXIS_LEFT_TRIGGER)>27000);
        CHECK(SDL_SetJoystickVirtualButton(joystick,3,false));SDL_UpdateJoysticks();
        CHECK(!SDL_GetGamepadButton(gamepad,SDL_GAMEPAD_BUTTON_SOUTH));
        SDL_CloseGamepad(gamepad);SDL_CloseJoystick(joystick);CHECK(SDL_DetachVirtualJoystick(id));SDL_Quit();
        fs::remove_all(directory);
        std::cout<<"PASS controller parsing, skipped-axis preservation, atomic private save, symlink rejection and actual SDL virtual button/hat/axis translation\n";
    } catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<" SDL="<<SDL_GetError()<<'\n';SDL_Quit();return 1;}
}
