#include "surface/controller_mapping.hpp"
#include "surface/control_center.hpp"
#include "surface/model.hpp"
#include "surface/renderer.hpp"
#include "surface/status.hpp"
#include <SDL3/SDL.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace surface;
constexpr const char* database="/data/config/gamecontrollerdb.txt";
constexpr const char* default_database="/usr/share/surface/gamecontrollerdb.txt";
struct Step {const char* key;const char* prompt;ControllerInput input=ControllerInput::Button;};
constexpr std::array steps{
    Step{"a","A / south face button"},Step{"b","B / east face button"},
    Step{"x","X / west face button"},Step{"y","Y / north face button"},
    Step{"start","Start / menu button"},Step{"back","Back / select button"},
    Step{"dpup","D-pad UP"},Step{"dpdown","D-pad DOWN"},
    Step{"dpleft","D-pad LEFT"},Step{"dpright","D-pad RIGHT"},
    Step{"leftshoulder","Left shoulder / L1"},Step{"rightshoulder","Right shoulder / R1"},
    Step{"leftstick","Click the left stick / L3"},Step{"rightstick","Click the right stick / R3"},
    Step{"leftx","Move the LEFT stick RIGHT",ControllerInput::Stick},
    Step{"lefty","Move the LEFT stick DOWN",ControllerInput::Stick},
    Step{"rightx","Move the RIGHT stick RIGHT",ControllerInput::Stick},
    Step{"righty","Move the RIGHT stick DOWN",ControllerInput::Stick},
    Step{"lefttrigger","Press the LEFT trigger / L2",ControllerInput::Trigger},
    Step{"righttrigger","Press the RIGHT trigger / R2",ControllerInput::Trigger},
    Step{"guide","Guide / HOME button (optional)"}
};
std::string device_name(const char* name,Uint16 vendor,Uint16 product) {
    if(vendor==0x1949 && product==0x0402) return "iPega PG-9023";
    if(vendor==0x1949) return "iPega controller";
    std::string result=name?name:"Unnamed controller";
    for(char& c:result)if(static_cast<unsigned char>(c)<32 || c==',' || c==127)c=' ';
    if(result.size()>120)result.resize(120);
    return result.empty() || result=="Unidentified" || result=="Unknown" ? "Unnamed controller":result;
}
struct Device {SDL_Joystick* joystick=nullptr;SDL_Gamepad* gamepad=nullptr;std::string name;};
class Setup {
    enum class Mode { Devices, Calibrate, Capture, Review, Saved };
    Mode mode=Mode::Devices;
    std::map<SDL_JoystickID,Device> devices;
    std::vector<SDL_JoystickID> order;
    SDL_JoystickID chosen=0;
    ControllerMapping mapping;
    std::size_t step=0,selected=0;
    std::vector<int> rest,last;
    Uint64 stable=0,neutral=0;
    Uint64 next_refresh=0;
    bool armed=false,editing=false,done=false;
    std::string message;
    SDL_Window* window;
    SDL_Renderer* renderer;
public:
    Setup(SDL_Window* w,SDL_Renderer* r):window(w),renderer(r){refresh();}
    ~Setup(){close_devices();}
    bool finished()const{return done;}
    void close_devices() {
        for(auto& [id,device]:devices){(void)id;if(device.gamepad)SDL_CloseGamepad(device.gamepad);if(device.joystick)SDL_CloseJoystick(device.joystick);}
        devices.clear();order.clear();
    }
    void refresh() {
        // Retain open handles so hotplug does not invalidate an active capture.
        SDL_ClearError();
        int count=0;auto* ids=SDL_GetJoysticks(&count);
        std::vector<SDL_JoystickID> present;
        std::string open_error;
        for(int i=0;ids && i<count && i<256;++i) {
            const auto id=ids[i];present.push_back(id);
            auto found=devices.find(id);
            if(found==devices.end()) {
                SDL_ClearError();
                auto* joystick=SDL_OpenJoystick(id);
                if(joystick)devices.emplace(id,Device{joystick,SDL_IsGamepad(id)?SDL_OpenGamepad(id):nullptr,
                    device_name(SDL_GetJoystickNameForID(id),SDL_GetJoystickVendorForID(id),SDL_GetJoystickProductForID(id))});
                else if(open_error.empty())open_error=SDL_GetError();
            } else if(!found->second.gamepad && SDL_IsGamepad(id))found->second.gamepad=SDL_OpenGamepad(id);
        }
        SDL_free(ids);
        for(auto it=devices.begin();it!=devices.end();) {
            if(std::find(present.begin(),present.end(),it->first)==present.end()) {
                if(it->first==chosen && mode!=Mode::Devices){mode=Mode::Devices;message="Controller disconnected. Unsaved changes were discarded.";chosen=0;}
                if(it->second.gamepad)SDL_CloseGamepad(it->second.gamepad);
                SDL_CloseJoystick(it->second.joystick);it=devices.erase(it);
            } else ++it;
        }
        order.clear();for(auto id:present)if(devices.contains(id))order.push_back(id);
        if(mode==Mode::Devices)selected=std::min(selected,order.size()+1);
        if(order.empty()) {
            if(count>0 && !open_error.empty())message="SDL detected "+std::to_string(count)+" controller device(s), but could not open one: "+open_error;
            else message="No controller input is visible to SDL. If Bluetooth says connected, reconnect in gamepad mode (iPega: hold X + HOME), then select Refresh controller list.";
        } else if(message.starts_with("No controller input is visible to SDL") || message.starts_with("SDL detected "))message.clear();
        next_refresh=SDL_GetTicks()+1000;
    }
    SDL_Joystick* joystick()const {const auto it=devices.find(chosen);return it==devices.end()?nullptr:it->second.joystick;}
    std::string display_binding(std::string_view key)const {
        auto value=mapping.binding(key);
        if(value.empty()) {
            const auto positive=mapping.binding("+"+std::string(key)),negative=mapping.binding("-"+std::string(key));
            if(!positive.empty())value="positive "+positive;
            if(!negative.empty())value+=(value.empty()?"":", ")+std::string("negative ")+negative;
        }
        return value;
    }
    void start(SDL_JoystickID id) {
        if(!devices.contains(id))return;
        chosen=id;char guid[33]{};SDL_GUIDToString(SDL_GetJoystickGUID(joystick()),guid,sizeof(guid));
        mapping=ControllerMapping{guid,devices.at(id).name,{}};
        if(char* current=SDL_GetGamepadMappingForID(id)) {
            if(auto parsed=ControllerMapping::parse(current))mapping=std::move(*parsed);
            SDL_free(current);
        }
        mapping.guid=guid;mapping.name=devices.at(id).name;mapping.set("platform","Linux");
        if(mapping.binding("hint").empty() && SDL_GetRealGamepadTypeForID(id)==SDL_GAMEPAD_TYPE_GAMECUBE)
            mapping.set("hint","!SDL_GAMECONTROLLER_USE_GAMECUBE_LABELS:=1");
        step=0;selected=2;editing=false;mode=Mode::Calibrate;stable=0;message.clear();
        rest.resize(std::max(0,SDL_GetNumJoystickAxes(joystick())));
        for(std::size_t i=0;i<rest.size();++i)rest[i]=SDL_GetJoystickAxis(joystick(),static_cast<int>(i));
        last=rest;
    }
    bool buttons_released()const {
        auto* joy=joystick();if(!joy)return false;
        for(int i=0;i<SDL_GetNumJoystickButtons(joy);++i)if(SDL_GetJoystickButton(joy,i))return false;
        for(int i=0;i<SDL_GetNumJoystickHats(joy);++i)if(SDL_GetJoystickHat(joy,i)!=SDL_HAT_CENTERED)return false;
        return true;
    }
    void next() {
        armed=false;neutral=0;selected=0;
        if(editing){editing=false;mode=Mode::Review;return;}
        if(++step>=steps.size())mode=Mode::Review;
    }
    void capture(std::string binding) {
        mapping.set(steps[step].key,binding);
        std::printf("SURFACE_CONTROLLER_CAPTURE key=%s binding=%s\n",steps[step].key,binding.c_str());std::fflush(stdout);
        message=std::string(steps[step].prompt)+" assigned to "+binding+".";next();
    }
    void tick() {
        if(mode==Mode::Devices && SDL_GetTicks()>=next_refresh)refresh();
        if(mode!=Mode::Calibrate && mode!=Mode::Capture)return;
        auto* joy=joystick();if(!joy)return;
        const auto now=SDL_GetTicks();
        if(mode==Mode::Calibrate) {
            bool quiet=buttons_released();
            for(std::size_t i=0;i<last.size();++i) {
                const int value=SDL_GetJoystickAxis(joy,static_cast<int>(i));
                if(std::abs(value-last[i])>2500)quiet=false;
                last[i]=value;
            }
            if(!quiet){stable=0;return;}
            if(!stable)stable=now;
            if(now-stable>=900) {rest=last;mode=Mode::Capture;armed=false;neutral=0;selected=0;}
        } else if(!armed) {
            bool quiet=buttons_released();
            for(std::size_t i=0;i<rest.size();++i)if(std::abs(int(SDL_GetJoystickAxis(joy,static_cast<int>(i)))-rest[i])>8000)quiet=false;
            if(!quiet)neutral=0;
            else if(!neutral)neutral=now;
            else if(now-neutral>=220)armed=true;
        }
    }
    void cancel() {
        if(mode==Mode::Devices || mode==Mode::Saved)done=true;
        else {mode=Mode::Devices;chosen=0;selected=0;message="Mapping cancelled. Saved assignments were kept.";}
    }
    void save() {
        std::string missing;
        for(const char* key:{"a","b","dpup","dpdown","dpleft","dpright"})if(mapping.binding(key).empty())missing+=(missing.empty()?"":", ")+std::string(key);
        if(!missing.empty()){message="Assign these controls before saving: "+missing+". Select a row to change it.";return;}
        try {
            const auto line=mapping.line();
            if(SDL_AddGamepadMapping(line.c_str())<0)throw std::runtime_error(std::string("SDL rejected mapping: ")+SDL_GetError());
            char* accepted=SDL_GetGamepadMappingForID(chosen);
            const auto actual=accepted?ControllerMapping::parse(accepted):std::nullopt;
            SDL_free(accepted);
            if(!actual)throw std::runtime_error("SDL did not recognize the controller mapping");
            for(const auto& target:steps)if(actual->binding(target.key)!=mapping.binding(target.key))
                throw std::runtime_error(std::string("SDL did not accept binding for ")+target.prompt);
            if(!controller_image_allowed())throw std::runtime_error("Saving requires the Surface guest image and mounted persistent /data");
            save_controller_mapping(database,mapping);
            mode=Mode::Saved;selected=0;message="Saved privately to /data/config/gamecontrollerdb.txt. HOME and newly launched emulators use this mapping.";
            std::printf("SURFACE_CONTROLLER_MAPPING_SAVED guid=%s path=%s\n",mapping.guid.c_str(),database);std::fflush(stdout);
        } catch(const std::exception& error){message=error.what();}
    }
    ControlView view()const {
        ControlView v;v.title="Controllers / button mapping";v.selected=selected;
        if(mode==Mode::Devices) {
            for(const auto id:order)v.rows.push_back(devices.at(id).name+(SDL_IsGamepad(id)?"  [connected, mapped]":"  [connected, unmapped]"));
            v.rows.emplace_back("Refresh controller list");
            v.rows.emplace_back("Return to HOME");
            v.status=order.empty()?message:message.empty()?"Connected controllers detected. Select one to assign buttons; unmapped controllers also start mapping when you press any button.":message;
        } else if(mode==Mode::Calibrate) {
            v.rows={"Release every button and center both sticks", "Leave both triggers released", "Cancel without saving"};
            v.status="Keep the controller still for one second to measure its resting axes. Esc or touch Cancel returns without changing saved mappings.";
        } else if(mode==Mode::Capture) {
            const auto existing=display_binding(steps[step].key);
            v.title="Map "+devices.at(chosen).name+"  /  "+std::to_string(step+1)+" of "+std::to_string(steps.size());
            v.rows={std::string(armed?"Press / move: ":"Release controls, then: ")+steps[step].prompt,
                "Skip this step  ["+(existing.empty()?std::string("leave unassigned"):"keep "+existing)+"]", "Cancel without saving"};
            v.status="Every controller input is captured here. Space / touch Skip keeps an existing assignment. Esc / touch Cancel discards changes. Sticks: move only the requested direction.";
        } else if(mode==Mode::Review) {
            v.title="Review controller mapping";v.rows.emplace_back("Save mapping");
            for(const auto& target:steps){auto value=display_binding(target.key);v.rows.push_back(std::string(target.prompt)+"  /  "+(value.empty()?"unassigned":value));}
            v.rows.emplace_back("Cancel without saving");
            v.status=message.empty()?"Select any control to reassign it, or Save mapping. Skipped controls retain their existing bindings; stick and trigger axes are optional.":message;
        } else {
            v.rows={"Return to HOME","Configure another controller"};v.status=message;
        }
        return v;
    }
    void accept(std::size_t row) {
        if(mode==Mode::Devices){if(row<order.size())start(order[row]);else if(row==order.size())refresh();else done=true;}
        else if(mode==Mode::Calibrate){if(row==2)cancel();}
        else if(mode==Mode::Capture){if(row==1){message="Skipped; previous assignment kept.";next();}else if(row==2)cancel();}
        else if(mode==Mode::Review) {
            if(row==0)save();
            else if(row<=steps.size()){step=row-1;editing=true;mode=Mode::Capture;selected=0;armed=false;neutral=0;message.clear();}
            else cancel();
        } else if(row==0)done=true;else {mode=Mode::Devices;selected=0;message.clear();refresh();}
    }
    void navigate(int direction) {
        const auto count=view().rows.size();if(count)selected=(selected+count+direction)%count;
    }
    void event(const SDL_Event& event) {
        if(event.type==SDL_EVENT_QUIT){done=true;return;}
        if(event.type==SDL_EVENT_JOYSTICK_ADDED || event.type==SDL_EVENT_JOYSTICK_REMOVED || event.type==SDL_EVENT_GAMEPAD_ADDED || event.type==SDL_EVENT_GAMEPAD_REMAPPED){refresh();return;}
        if(event.type==SDL_EVENT_KEY_DOWN && !event.key.repeat) {
            switch(event.key.key) {
            case SDLK_ESCAPE:case SDLK_F10:cancel();return;
            case SDLK_UP:navigate(-1);return;case SDLK_DOWN:navigate(1);return;
            case SDLK_RETURN:accept(selected);return;
            case SDLK_SPACE:if(mode==Mode::Capture)accept(1);else accept(selected);return;
            default:break;
            }
        }
        if(event.type==SDL_EVENT_FINGER_DOWN || (event.type==SDL_EVENT_MOUSE_BUTTON_DOWN && event.button.which!=SDL_TOUCH_MOUSEID)) {
            float x=event.type==SDL_EVENT_FINGER_DOWN?event.tfinger.x:event.button.x;
            float y=event.type==SDL_EVENT_FINGER_DOWN?event.tfinger.y:event.button.y;
            if(event.type==SDL_EVENT_FINGER_DOWN){int w=0,h=0;SDL_GetWindowSize(window,&w,&h);x*=w;y*=h;}
            SDL_RenderCoordinatesFromWindow(renderer,x,y,&x,&y);
            const auto v=view();
            const auto begin=v.selected>=7?v.selected-6:0;
            for(std::size_t i=begin;i<v.rows.size() && i<begin+7;++i)if(Rect{185,250+float(i-begin)*68,1230,57}.contains(x,y)){selected=i;accept(i);return;}
        }
        // During raw capture, mapped B/Guide/Start are ordinary assignable inputs.
        // Only keyboard or explicit touch actions may cancel or skip.
        if(mode==Mode::Capture || mode==Mode::Calibrate) {
            if(mode!=Mode::Capture || !armed)return;
            if(event.type==SDL_EVENT_JOYSTICK_BUTTON_DOWN && event.jbutton.which==chosen && steps[step].input!=ControllerInput::Stick)capture("b"+std::to_string(event.jbutton.button));
            else if(event.type==SDL_EVENT_JOYSTICK_HAT_MOTION && event.jhat.which==chosen && steps[step].input==ControllerInput::Button) {
                const int value=event.jhat.value;
                if(value && (value&(value-1))==0)capture("h"+std::to_string(event.jhat.hat)+"."+std::to_string(value));
            } else if(event.type==SDL_EVENT_JOYSTICK_AXIS_MOTION && event.jaxis.which==chosen && event.jaxis.axis<rest.size()) {
                if(auto binding=controller_axis_binding(steps[step].input,event.jaxis.axis,rest[event.jaxis.axis],event.jaxis.value))capture(*binding);
            }
            return;
        }
        if(event.type==SDL_EVENT_GAMEPAD_BUTTON_DOWN) {
            switch(event.gbutton.button) {
            case SDL_GAMEPAD_BUTTON_DPAD_UP:navigate(-1);break;case SDL_GAMEPAD_BUTTON_DPAD_DOWN:navigate(1);break;
            case SDL_GAMEPAD_BUTTON_SOUTH:accept(selected);break;case SDL_GAMEPAD_BUTTON_EAST:cancel();break;
            default:break;
            }
        } else if(mode==Mode::Devices && event.type==SDL_EVENT_JOYSTICK_BUTTON_DOWN && !SDL_IsGamepad(event.jbutton.which))start(event.jbutton.which);
    }
};
}
#ifndef SURFACE_CONTROLLER_SETUP_NO_MAIN
int main(int argc,char** argv) {
    if(argc==2 && std::string(argv[1])=="--help") {
        std::puts("surface-controller-setup\nGuided raw SDL joystick mapping. Saves /data/config/gamecontrollerdb.txt only in the guest image.\nKeyboard: arrows/Enter, Space skip, Esc cancel. Touch: select the visible Skip/Cancel rows.\nDuring capture, controller B/Guide are assigned as inputs and cannot cancel.");return 0;
    }
    if(argc==2 && std::string(argv[1])=="--vm-ipega-probe") {
        std::ifstream identity("/sys/class/dmi/id/product_name");std::string product;
        std::getline(identity,product);
        const char* requested=std::getenv("SURFACE_VM_IPEGA_TEST");
        if(product!="Surface Go OS Disposable Test VM" || !requested || std::string(requested)!="1") {
            std::fprintf(stderr,"iPega VM probe is restricted to an explicitly requested disposable test VM.\n");return 2;
        }
        if(!SDL_Init(SDL_INIT_GAMEPAD|SDL_INIT_JOYSTICK)){std::fprintf(stderr,"SDL: %s\n",SDL_GetError());return 1;}
        SDL_AddGamepadMappingsFromFile(default_database);
        SDL_AddGamepadMappingsFromFile(database);
        if(const char* personal=std::getenv("SDL_GAMECONTROLLERCONFIG_FILE"))SDL_AddGamepadMappingsFromFile(personal);
        int count=0;auto* ids=SDL_GetJoysticks(&count);SDL_JoystickID target=0;
        for(int i=0;ids && i<count;++i) {
            if(SDL_GetJoystickVendorForID(ids[i])==0x1949 && SDL_GetJoystickProductForID(ids[i])==0x0402){target=ids[i];break;}
        }
        SDL_free(ids);
        if(!target){std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=not-enumerated-by-SDL\n");SDL_Quit();return 1;}
        const char* raw_name=SDL_GetJoystickNameForID(target);
        const std::string name=raw_name?raw_name:"";
        if(!SDL_IsGamepad(target)) {
            std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=default-profile-missing name=%s\n",name.c_str());SDL_Quit();return 1;
        }
        char guid[33]{};SDL_GUIDToString(SDL_GetJoystickGUIDForID(target),guid,sizeof(guid));
        SDL_Gamepad* gamepad=SDL_OpenGamepad(target);
        SDL_Joystick* joystick=SDL_OpenJoystick(target);
        if(!gamepad || !joystick) {
            std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=unprivileged-open name=%s error=%s\n",name.c_str(),SDL_GetError());
            if(gamepad)SDL_CloseGamepad(gamepad);
            if(joystick)SDL_CloseJoystick(joystick);
            SDL_Quit();return 1;
        }
        int buttons=0,axes=0;
        for(int button=SDL_GAMEPAD_BUTTON_SOUTH;button<=SDL_GAMEPAD_BUTTON_DPAD_RIGHT;++button)
            if(SDL_GamepadHasButton(gamepad,static_cast<SDL_GamepadButton>(button)))++buttons;
        for(int axis=0;axis<SDL_GAMEPAD_AXIS_COUNT;++axis)
            if(SDL_GamepadHasAxis(gamepad,static_cast<SDL_GamepadAxis>(axis)))++axes;
        if(buttons<14 || axes<SDL_GAMEPAD_AXIS_COUNT) {
            std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=incomplete-default-profile buttons=%d axes=%d\n",buttons,axes);
            SDL_CloseGamepad(gamepad);SDL_CloseJoystick(joystick);SDL_Quit();return 1;
        }
        std::printf("SURFACE_IPEGA_DEVICE name=%s guid=%s buttons=%d axes=%d default_profile=PASS\n",name.c_str(),guid,buttons,axes);std::fflush(stdout);
        auto setup=std::make_unique<Setup>(nullptr,nullptr);
        const auto rows=setup->view().rows;
        if(rows.empty() || rows.front()!="iPega PG-9023  [connected, mapped]") {
            std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=mapper-device-list\n");
            setup.reset();SDL_CloseGamepad(gamepad);SDL_CloseJoystick(joystick);SDL_Quit();return 1;
        }
        setup->accept(0);
        const auto deadline=SDL_GetTicks()+10000;bool raw_input=false,mapped_input=false;
        while(SDL_GetTicks()<deadline && !mapped_input) {
            setup->tick();
            SDL_Event event;while(SDL_PollEvent(&event)) {
                if(event.type==SDL_EVENT_JOYSTICK_BUTTON_DOWN && event.jbutton.which==target)raw_input=true;
                if(event.type==SDL_EVENT_GAMEPAD_BUTTON_DOWN && event.gbutton.which==target && event.gbutton.button==SDL_GAMEPAD_BUTTON_SOUTH)mapped_input=true;
                setup->event(event);
            }
            SDL_Delay(5);
        }
        const bool captured=setup->view().title.find("2 of ")!=std::string::npos;
        setup.reset();
        SDL_CloseGamepad(gamepad);SDL_CloseJoystick(joystick);SDL_Quit();
        if(!raw_input || !mapped_input || !captured) {
            std::fprintf(stderr,"SURFACE_IPEGA_FAIL reason=button-not-delivered raw=%d mapped=%d captured=%d\n",raw_input,mapped_input,captured);return 1;
        }
        std::puts("SURFACE_IPEGA_MAPPER_PASS listed=1 mapped=1 raw_capture=1");
        std::puts("SURFACE_IPEGA_INPUT_PASS path=udev-to-sdl-to-mapped-gamepad-button");std::fflush(stdout);return 0;
    }
    if(argc!=1){std::fprintf(stderr,"Unknown controller setup argument. Use --help.\n");return 2;}
    if(!controller_image_allowed()){std::fprintf(stderr,"Controller setup requires the Surface guest image and a mounted /data; no devices or files changed.\n");return 2;}
    if(!SDL_Init(SDL_INIT_VIDEO|SDL_INIT_GAMEPAD|SDL_INIT_JOYSTICK)){std::fprintf(stderr,"SDL: %s\n",SDL_GetError());return 1;}
    SDL_Window* window=SDL_CreateWindow("403 OS Controller setup",1200,800,SDL_WINDOW_FULLSCREEN);
    SDL_Renderer* renderer=window?SDL_CreateRenderer(window,nullptr):nullptr;
    int result=0;
    try {
        if(!renderer)throw std::runtime_error(SDL_GetError());
        SDL_SetRenderLogicalPresentation(renderer,int(ui_width),int(ui_height),SDL_LOGICAL_PRESENTATION_LETTERBOX);
        SDL_AddGamepadMappingsFromFile(default_database);
        SDL_AddGamepadMappingsFromFile(database);
        if(const char* personal=std::getenv("SDL_GAMECONTROLLERCONFIG_FILE"))SDL_AddGamepadMappingsFromFile(personal);
        HomeRenderer home(renderer);Setup setup(window,renderer);
        Model model({App{"controllers","Controller setup","Map your controller for HOME and emulators",""}});
        Status status;status.time="Controller setup";
        while(!setup.finished()) {
            SDL_Event event;while(SDL_PollEvent(&event))setup.event(event);
            setup.tick();auto controls=setup.view();home.draw(model,status,&controls);SDL_RenderPresent(renderer);SDL_Delay(16);
        }
    } catch(const std::exception& error){std::fprintf(stderr,"Controller setup: %s\n",error.what());result=1;}
    if(renderer)SDL_DestroyRenderer(renderer);
    if(window)SDL_DestroyWindow(window);
    SDL_Quit();return result;
}
#endif
