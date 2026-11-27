#include "surface/model.hpp"
#include "surface/process.hpp"
#include "surface/status.hpp"
#include "surface/renderer.hpp"
#include "surface/library.hpp"
#include "surface/progress.hpp"
#include "surface/control_center.hpp"
#include "surface/global_home.hpp"
#include "surface/home_input.hpp"
#include <cstdlib>
#include <csignal>
#include <memory>
#include <SDL3/SDL.h>
#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <ctime>
#include <future>
#include <fstream>
#include <optional>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

using namespace surface;
namespace {
volatile std::sig_atomic_t session_stop=0;
void stop_session(int) { session_stop=1; }
void controller_report() {
    // Inventory only: never log key events or controller Bluetooth addresses.
    if(!std::filesystem::is_regular_file("/etc/surface-image-id") || geteuid()!=0) return;
    const int directory=open("/data/logs",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(directory<0) return;
    const int fd=openat(directory,"controller-input.log",O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
    close(directory);if(fd<0) return;
    FILE* file=fdopen(fd,"w");if(!file){close(fd);return;}
    int count=0;auto* ids=SDL_GetJoysticks(&count);
    std::fprintf(file,"SDL input inventory: %d joystick collections\n",count);
    for(int i=0;ids && i<count && i<32;++i) {
        const auto id=ids[i];char guid[33]{};
        SDL_GUIDToString(SDL_GetJoystickGUIDForID(id),guid,sizeof(guid));
        auto* joystick=SDL_OpenJoystick(id);
        const auto* name=SDL_GetJoystickNameForID(id);
        std::fprintf(file,"name=%.120s vid=%04x pid=%04x guid=%s gamepad=%d opened=%d buttons=%d axes=%d hats=%d\n",
            name?name:"unknown",SDL_GetJoystickVendorForID(id),SDL_GetJoystickProductForID(id),guid,
            SDL_IsGamepad(id),joystick!=nullptr,joystick?SDL_GetNumJoystickButtons(joystick):0,
            joystick?SDL_GetNumJoystickAxes(joystick):0,joystick?SDL_GetNumJoystickHats(joystick):0);
        if(joystick) SDL_CloseJoystick(joystick);
    }
    SDL_free(ids);std::fclose(file);
}

struct Session {
    Library library;
    std::vector<App> home;
    std::vector<App> pinned;
    std::filesystem::path catalog;
    std::filesystem::file_time_type catalog_time{};
    std::string filter_system, query;
    ProgressStore progress{std::getenv("SURFACE_PROGRESS")?std::getenv("SURFACE_PROGRESS"):"/data/home/progress.tsv"};
    std::string playing;
    Uint64 started=0;
    bool mapping_child=false;
    ControlCenter controls;
    void refresh(Model& model) { progress.apply(library.games);progress.apply(home);progress.apply(model.apps); }
    void profileChanged(Model& model,const ProfileIdentity& profile) {
        progress=ProgressStore(std::filesystem::path(profile.state)/"progress.tsv");
        library=read_library(catalog);home=library.games;
        home.insert(home.end(),pinned.begin(),pinned.end());
        model.apps=home;model.tile=0;model.zone=Zone::Carousel;model.page=Page::Home;
        model.overlay=false;model.details=false;model.shutdown_confirmation=false;model.message.clear();
        refresh(model);
    }
    void show(Model& model,Page page,const std::string& system={}) {
        filter_system=system;
        std::vector<App> items;
        if(page==Page::Systems) items=library.systems;
        else if(page==Page::Home) items=home;
        else {
            for(const auto& app:library.games) {
                if(!system.empty() && app.system!=system) continue;
                if(page==Page::Favorites && !app.favorite) continue;
                if(page==Page::Recent && !app.last_played) continue;
                if(page==Page::Search) {
                    auto title=app.title,term=query;
                    auto lower=[](unsigned char c){return static_cast<char>(std::tolower(c));};
                    std::transform(title.begin(),title.end(),title.begin(),lower);
                    std::transform(term.begin(),term.end(),term.begin(),lower);
                    if(title.find(term)==std::string::npos) continue;
                }
                items.push_back(app);
            }
            if(page==Page::Recent) std::stable_sort(items.begin(),items.end(),[](const auto& a,const auto& b){return a.last_played>b.last_played;});
            if(page==Page::Games && system.empty()) items.insert(items.begin(),{
                {"favorites","Favorites","Your favorite games",""},
                {"recent","Recently played","Your game history",""},
                {"library-setup","Search games","Search by name; game drives are discovered automatically",""}});
        }
        if(items.empty()) {
            model.message=page==Page::Favorites?"No favorites yet. Select a game and press Y / F to add it.":page==Page::Recent?"Your played games will appear here.":page==Page::Search?"No games match your search.":"No games found for this system. Use Search and library to scan your game files.";
            return;
        }
        model.apps=std::move(items);model.tile=0;model.zone=Zone::Carousel;model.page=page;
    }
    void reload(Model& model) {
        std::error_code error;
        const auto changed=std::filesystem::last_write_time(catalog,error);
        if(error || changed==catalog_time) return;
        const auto id=model.selected().id;
        const auto page=model.page;
        catalog_time=changed;library=read_library(catalog);progress.apply(library.games);
        std::printf("SURFACE_LIBRARY_UPDATED games=%zu\n",library.games.size());std::fflush(stdout);
        home=library.games;home.insert(home.end(),pinned.begin(),pinned.end());progress.apply(home);
        show(model,page,filter_system);
        for(std::size_t i=0;i<model.apps.size();++i) if(model.apps[i].id==id) {model.tile=i;break;}
    }
};
void activate(Model& model, Process& child, Session& session) {
    if(model.shutdown_confirmation) {
        model.shutdown_confirmation=false;
        if(!session.controls.administrator()) { model.message="Sign in as root to power off the device";return; }
        if(!std::filesystem::exists("/etc/surface-image-id")) {
            model.message="Power controls are available only inside the booted OS image."; return;
        }
        if(!child.start({"/sbin/poweroff"})) model.message=child.error;
        return;
    }
    if(model.overlay || !model.message.empty()) return;
    model.details=false;
    if(model.zone == Zone::Dock) {
        if(model.dock == 0) { session.show(model,Page::Games); return; }
        if(model.dock == 1) { session.show(model,Page::Systems); return; }
        if(model.dock == 2) { session.controls.open(PanelKind::Moonlight);return; }
        if(model.dock == 3) { if(!child.start({"/usr/bin/surface-browser"})) model.message=child.error; return; }
        if(model.dock == 4) { session.controls.open(PanelKind::Tailscale);return; }
        if(model.dock == 5) { session.controls.open(PanelKind::Bluetooth);return; }
        if(model.dock == 6) { session.controls.open(PanelKind::Settings);return; }
        if(model.dock == 7) {
            model.shutdown_confirmation=true;
            model.message="Power off this device? A / Enter: Confirm. B / Esc: Cancel."; return;
        }
        model.message="This control is not implemented in the foundation image. See docs/STATUS.md."; return;
    }
    if(model.selected().id.starts_with("system:")) { session.show(model,Page::Games,model.selected().system); return; }
    if(model.selected().id=="install") { session.controls.open(PanelKind::Install); return; }
    if(model.selected().id=="library") {session.show(model,Page::Games);return;}
    if(model.selected().id=="favorites") {session.show(model,Page::Favorites);return;}
    if(model.selected().id=="recent") {session.show(model,Page::Recent);return;}
    if(model.selected().id=="library-setup") {session.controls.open(PanelKind::Library);session.controls.navigate(Action::Accept);return;}
    if(model.selected().id=="pokemmo") {session.controls.open(PanelKind::PokeMMO);return;}
    if(model.selected().id=="dw3") {session.controls.open(PanelKind::DW3);return;}
    if(model.selected().id=="windows") {session.controls.open(PanelKind::Windows);return;}
    if(model.selected().id=="moonlight") { session.controls.open(PanelKind::Moonlight);return; }
    if(model.selected().id == "diagnostics") { model.overlay=true; return; }
    if(model.selected().executable.empty()) { model.message="No compatible emulator engine is installed for this system."; return; }
    std::vector<std::string> command{model.selected().executable};
    command.insert(command.end(),model.selected().arguments.begin(),model.selected().arguments.end());
    const auto profile=session.controls.currentProfile();
    if(profile && profile->uid!=0) {
        for(std::size_t i=0;i+1<command.size();++i) if(command[i]=="--save-dir") command[i+1]=profile->home+"/saves";
    }
    if(model.selected().system=="GameCube") {
        const auto preset=model.selected().performance;
        const std::vector<std::string> options{"--user",profile && profile->uid!=0?profile->home+"/config/dolphin":"/data/config/dolphin","--video_backend","OGL",
            "-C","GFX.Settings.InternalResolution=1","-C","GFX.Settings.MSAA=1",
            "-C",std::string("Dolphin.Core.CPUThread=")+(preset==1?"False":"True"),
            "-C",std::string("GFX.Hacks.EFBAccessEnable=")+(preset==2?"False":"True"),
            "-C",std::string("GFX.Hacks.EFBToTextureEnable=")+(preset==1?"False":"True")};
        command.insert(command.end(),options.begin(),options.end());
    }
    if(!child.start(command)) model.message=child.error;
    else if(model.selected().game) {
        session.playing=model.selected().id;session.started=SDL_GetTicks();
        try { session.progress.played(session.playing,0,std::time(nullptr));session.refresh(model); }
        catch(const std::exception& error) { std::fprintf(stderr,"Library state: %s\n",error.what()); }
    }
}
void memory_report(const char* prefix) {
    std::string line;
    std::ifstream memory("/proc/meminfo");
    while(std::getline(memory,line)) if(line.starts_with("MemTotal:") || line.starts_with("MemAvailable:"))
        std::printf("%s %s\n",prefix,line.c_str());
    std::ifstream process("/proc/self/status");
    while(std::getline(process,line)) if(line.starts_with("VmRSS:")) std::printf("%s %s\n",prefix,line.c_str());
    std::fflush(stdout);
}
}

int main(int argc, char** argv) {
    std::signal(SIGTERM,stop_session);
    std::signal(SIGINT,stop_session);
    const bool self_test = argc > 1 && std::string(argv[1]) == "--self-test";
    const bool capture = argc > 2 && std::string(argv[1]) == "--screenshot";
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS,"1");
    if(!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) { std::fprintf(stderr,"SDL: %s\n",SDL_GetError()); return 1; }
    SDL_AddGamepadMappingsFromFile("/usr/share/surface/gamecontrollerdb.txt");
    SDL_AddGamepadMappingsFromFile("/data/config/gamecontrollerdb.txt");
    if(const char* personal=std::getenv("SDL_GAMECONTROLLERCONFIG_FILE"))SDL_AddGamepadMappingsFromFile(personal);
    SDL_Window* window = SDL_CreateWindow("Surface HOME",int(ui_width),int(ui_height),(self_test || capture) ? SDL_WINDOW_HIDDEN : SDL_WINDOW_FULLSCREEN);
    SDL_Renderer* renderer = window ? SDL_CreateRenderer(window,self_test ? "software" : nullptr) : nullptr;
    if(!renderer) { std::fprintf(stderr,"Graphics: %s\n",SDL_GetError()); SDL_Quit(); return 1; }
    SDL_SetRenderLogicalPresentation(renderer,int(ui_width),int(ui_height),SDL_LOGICAL_PRESENTATION_LETTERBOX);
    Model model({{"diagnostics","System status","Live device information",""},
                 {"moonlight","Moonlight","Remote PC streaming","/usr/bin/moonlight"},
                 {"browser","Browser","Web application","/usr/bin/surface-browser"},
                 {"library","Game library","Browse games and systems",""},
                 {"recent","Recently played","Your game history",""},
                 {"pokemmo","PokeMMO","Official Linux client setup",""},
                 {"dw3","Digimon World 3 Online","User-supplied Windows client",""},
                 {"windows","Windows applications","Wine and Proton compatibility",""}});
    for(auto& app:model.apps) if(!app.executable.empty() && !std::filesystem::exists(app.executable))
        app.subtitle="Application is not installed";
    if(std::filesystem::is_regular_file("/data/SurfaceGoGamingOS/surface-go.img"))
        model.apps.insert(model.apps.begin(),{"install","Update or install 403 OS","Keep games and settings when updating",""});
    Session session;
    const char* catalog=std::getenv("SURFACE_LIBRARY");
    session.catalog=catalog?catalog:"/data/library/catalog.tsv";
    session.library=read_library(session.catalog);
    std::error_code catalog_error;
    session.catalog_time=std::filesystem::last_write_time(session.catalog,catalog_error);
    session.pinned=model.apps;
    session.home=session.library.games;
    session.home.insert(session.home.end(),model.apps.begin(),model.apps.end());
    std::printf("SURFACE_LIBRARY games=%zu systems=%zu\n",session.library.games.size(),session.library.systems.size());
    if(!self_test) model.apps=session.home;
    session.refresh(model);
    std::unique_ptr<HomeRenderer> home;
    try { home=std::make_unique<HomeRenderer>(renderer); }
    catch(const std::exception& error) { std::fprintf(stderr,"HOME: %s\n",error.what()); return 1; }
    Process child;
    const bool profiles_enabled=!self_test && !capture && std::filesystem::is_regular_file("/etc/surface-image-id");
    if(profiles_enabled) {
        std::puts("SURFACE_PROFILE_PROTOCOL 1");std::fflush(stdout);
        child.useProfiles(true);session.controls.enableProfiles();
        model.apps=session.pinned;model.tile=0;
    }
    GlobalHomeInput global_home;
    HomeInput home_input;
    Status status = read_status();
    std::future<Status> pending;
    Uint64 next_status=SDL_GetTicks()+2000;
    std::vector<SDL_Gamepad*> pads;
    int count=0;
    SDL_JoystickID* ids=SDL_GetGamepads(&count);
    for(int i=0;i<count;++i) if(auto* p=SDL_OpenGamepad(ids[i])) pads.push_back(p);
    SDL_free(ids);
    controller_report();
    auto draw=[&]{home->draw(model,status,session.controls.active()?&session.controls.view():nullptr);};
    auto resume=[&] { model.overlay=false;model.message.clear();SDL_HideWindow(window); };
    draw();
    if(!SDL_RenderPresent(renderer)) return 1;
    std::printf("SURFACE_READY graphics=%s renderer=%s input=keyboard,gamepad,touch size=1600x1066\n",
                SDL_GetCurrentVideoDriver(),SDL_GetRendererName(renderer));
    std::fflush(stdout);
    if(!self_test) memory_report("SURFACE_MEMORY");
    bool idle_reported=false;
    bool home_focus_pending=false;
    int test_step=0;
    bool quit=false;
    bool text_input=false;
    std::string control_snapshot;
    std::string password_prompt;
    bool profile_lock_pending=false;
    while(!quit && !session_stop) {
        if(capture && SDL_GetTicks()>1800) {
            draw();
            SDL_Surface* frame=SDL_RenderReadPixels(renderer,nullptr);
            if(!frame || !SDL_SaveBMP(frame,argv[2])) { std::fprintf(stderr,"Screenshot: %s\n",SDL_GetError());return 1; }
            SDL_DestroySurface(frame);quit=true;
        }
        if(!self_test && !idle_reported && SDL_GetTicks()>=10000) {
            memory_report("SURFACE_IDLE_MEMORY"); idle_reported=true;
        }
        if(self_test) {
            SDL_Event inject{};
            if(test_step==0) { inject.type=SDL_EVENT_KEY_DOWN; inject.key.key=SDLK_RIGHT; }
            if(test_step==1) { inject.type=SDL_EVENT_GAMEPAD_BUTTON_DOWN; inject.gbutton.button=SDL_GAMEPAD_BUTTON_DPAD_RIGHT; }
            if(test_step==2) { inject.type=SDL_EVENT_KEY_DOWN; inject.key.key=SDLK_TAB; }
            if(test_step==3) { inject.type=SDL_EVENT_KEY_DOWN; inject.key.key=SDLK_ESCAPE; }
            if(test_step==4) {
                inject.type=SDL_EVENT_FINGER_DOWN;
                auto power=dock_rect(7);
                inject.tfinger.x=(power.x+power.w/2)/ui_width;
                inject.tfinger.y=(power.y+power.h/2)/ui_height;
            }
            if(test_step==5) { inject.type=SDL_EVENT_KEY_DOWN; inject.key.key=SDLK_ESCAPE; }
            if(test_step < 6 && !SDL_PushEvent(&inject)) return 2;
        }
        SDL_Event event{};
        bool redraw=false;
        if(SDL_WaitEventTimeout(&event,self_test?1:32)) do {
            if(event.type==SDL_EVENT_QUIT && !(session.controls.active() && session.controls.view().critical)) quit=true;
            if(event.type==SDL_EVENT_JOYSTICK_ADDED || event.type==SDL_EVENT_JOYSTICK_REMOVED) controller_report();
            if(event.type==SDL_EVENT_GAMEPAD_ADDED && std::none_of(pads.begin(),pads.end(),
               [&](auto* p){return SDL_GetGamepadID(p)==event.gdevice.which;}))
                if(auto* p=SDL_OpenGamepad(event.gdevice.which)) pads.push_back(p);
            if(event.type==SDL_EVENT_GAMEPAD_REMOVED) {
                auto it=std::find_if(pads.begin(),pads.end(),[&](auto* p){return SDL_GetGamepadID(p)==event.gdevice.which;});
                if(it!=pads.end()) { SDL_CloseGamepad(*it); pads.erase(it); }
            }
            if(auto a=home_input.action(event)) {
                if(session.controls.active()) {
                    if(event.type==SDL_EVENT_KEY_DOWN && event.key.key==SDLK_SPACE && session.controls.view().editing) { /* SDL text input supplies the space. */ }
                    else if(event.type==SDL_EVENT_KEY_DOWN && event.key.key==SDLK_RETURN && session.controls.view().editing) session.controls.submitText();
                    else if(event.type==SDL_EVENT_KEY_DOWN && event.key.key==SDLK_BACKSPACE && session.controls.view().editing) session.controls.backspace();
                    else session.controls.navigate(*a);
                    redraw=true;continue;
                }
                if(child.running()) {
                    if(session.mapping_child) continue;
                    if(*a==Action::Quick) {
                        if(model.overlay) resume();
                        else { model.overlay=true;SDL_ShowWindow(window);SDL_RaiseWindow(window); }
                    } else if(model.overlay && (*a==Action::Back || *a==Action::Accept)) resume();
                    else if(model.overlay && *a==Action::Details) {
                        model.overlay=false;child.stop();model.message="Closing application and saving progress...";
                    } else if(model.overlay && *a==Action::Favorite) {
                        model.overlay=false;session.controls.open(PanelKind::Settings);
                    }
                    redraw=true;continue;
                }
                if(model.overlay && *a==Action::Accept) {
                    model.overlay=false;session.controls.open(PanelKind::Settings);redraw=true;continue;
                }
                if(model.details && model.selected().system=="GameCube" && (*a==Action::Left || *a==Action::Right)) {
                    try { session.progress.performance(model.selected().id,(model.selected().performance+(*a==Action::Right?1:2))%3);session.refresh(model); }
                    catch(const std::exception& error) {model.message=error.what();}
                    redraw=true;continue;
                }
                if(*a==Action::Favorite && model.selected().game) {
                    try { session.progress.favorite(model.selected().id,!model.selected().favorite);session.refresh(model); }
                    catch(const std::exception& error) { model.message=error.what(); }
                }
                else if(*a==Action::Accept) activate(model,child,session); else if(*a==Action::Back && model.page!=Page::Home && !model.overlay && !model.details && model.message.empty()) session.show(model,Page::Home);
                else model.navigate(*a);
                std::printf("SURFACE_INPUT action=%d tile=%zu zone=%d overlay=%d\n",int(*a),model.tile,int(model.zone),model.overlay);
                std::fflush(stdout); redraw=true;
            }
            if(event.type==SDL_EVENT_TEXT_INPUT && session.controls.active() && session.controls.view().editing) {
                session.controls.inputText(event.text.text);redraw=true;
            }
            if(event.type==SDL_EVENT_FINGER_DOWN ||
               (event.type==SDL_EVENT_MOUSE_BUTTON_DOWN && event.button.which!=SDL_TOUCH_MOUSEID)) {
                float x=event.type==SDL_EVENT_FINGER_DOWN?event.tfinger.x:event.button.x;
                float y=event.type==SDL_EVENT_FINGER_DOWN?event.tfinger.y:event.button.y;
                if(event.type==SDL_EVENT_FINGER_DOWN) { int w=0,h=0; SDL_GetWindowSize(window,&w,&h); x*=w; y*=h; }
                SDL_RenderCoordinatesFromWindow(renderer,x,y,&x,&y);
                if(session.controls.active()) {
                    session.controls.touch(x,y);
                }
                else if(child.running()) {
                    if(model.overlay && Rect{295,650,300,75}.contains(x,y)) resume();
                    else if(model.overlay && Rect{660,650,300,75}.contains(x,y)) {
                        model.overlay=false;child.stop();model.message="Closing application and saving progress...";
                    }
                }
                else if(model.overlay || !model.message.empty() || model.details) model.navigate(Action::Back);
                else {
                    const auto selected=model.tile;
                    for(std::size_t i=0;i<model.apps.size();++i) if(tile_rect(i,selected).contains(x,y)) {
                        model.tile=i; model.zone=Zone::Carousel; activate(model,child,session); break;
                    }
                    for(std::size_t i=0;i<dock_count;++i) if(dock_rect(i).contains(x,y)) {
                        model.dock=i; model.zone=Zone::Dock; activate(model,child,session); break;
                    }
                }
                redraw=true;
            }
            if(event.type==SDL_EVENT_MOUSE_WHEEL && session.controls.active()) {
                session.controls.scroll(event.wheel.y>0?-3:3);redraw=true;
            }
            if(event.type==SDL_EVENT_WINDOW_EXPOSED || event.type==SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED) redraw=true;
        } while(SDL_PollEvent(&event));
        if(global_home.poll(child.running())) {
            child.stop();model.overlay=false;model.message="Closing application and saving progress...";
            std::puts("SURFACE_GLOBAL_HOME_REQUEST");std::fflush(stdout);
        }
        if(auto code=child.poll()) {
            std::printf("SURFACE_APPLICATION_RETURN status=%d\n",*code);std::fflush(stdout);
            if(session.mapping_child) { SDL_AddGamepadMappingsFromFile("/data/config/gamecontrollerdb.txt");session.mapping_child=false; }
            model.application_running=false;model.overlay=false;model.message.clear();
            if(!session.playing.empty()) {
                try { session.progress.played(session.playing,(SDL_GetTicks()-session.started)/1000,std::time(nullptr));session.refresh(model); }
                catch(const std::exception& error) { model.message=error.what(); }
                session.playing.clear();
            }
            if(*code!=0) model.message="Application exited with status "+std::to_string(*code);
            SDL_ShowWindow(window);SDL_RaiseWindow(window); redraw=true;
            home_focus_pending=true;
        }
        if(SDL_GetTicks()>=next_status && !pending.valid()) {
            session.reload(model);
            pending=std::async(std::launch::async,[]{return read_status();}); next_status=SDL_GetTicks()+2000;
        }
        if(pending.valid() && pending.wait_for(std::chrono::seconds(0))==std::future_status::ready) {
            status=pending.get(); redraw=true;
        }
        session.controls.update();
        if(session.controls.takeLockRequest()) { profile_lock_pending=true;child.stop();model.overlay=false; }
        if(profile_lock_pending && !child.running()) {
            profile_lock_pending=false;session.controls.lockProfiles();
            SDL_ShowWindow(window);SDL_RaiseWindow(window);redraw=true;
        }
        if(session.controls.takeProfileChanged()) {
            if(const auto profile=session.controls.currentProfile()) session.profileChanged(model,*profile);
            else { model.apps=session.pinned;model.tile=0;model.overlay=false;model.details=false;model.message.clear(); }
            redraw=true;
        }
        if(auto query=session.controls.takeSearch()) {session.query=*query;session.show(model,Page::Search);redraw=true;}
        if(session.controls.active()) {
            const auto& view=session.controls.view();
            std::string snapshot=view.title+view.status+(view.secret?std::string(view.input.size(),'*'):view.input)+std::to_string(view.selected)+std::to_string(view.keyboard_index)+std::to_string(view.editing)+std::to_string(view.busy);
            for(const auto& row:view.rows) snapshot+=row;
            if(snapshot!=control_snapshot) {control_snapshot=std::move(snapshot);redraw=true;}
        } else if(!control_snapshot.empty()) {control_snapshot.clear();redraw=true;}
        const bool wants_text=session.controls.active() && session.controls.view().editing;
        if(wants_text!=text_input) { if(wants_text) SDL_StartTextInput(window);else SDL_StopTextInput(window);text_input=wants_text; }
        if(auto command=session.controls.takeLaunch()) {
            if(!child.start(*command)) model.message=child.error;
            else session.mapping_child=!command->empty() && command->front()=="/usr/bin/surface-controller-setup";
        }
        if(child.running() && !model.application_running) {
            std::puts("SURFACE_APPLICATION_START");std::fflush(stdout);
            model.application_running=true;model.overlay=false;model.message.clear();SDL_HideWindow(window);
        }
        if(child.running() && !session.controls.active() && !model.overlay && model.message.empty()) SDL_HideWindow(window);
        redraw=home->update() || redraw;
        if(redraw) { draw(); if(!SDL_RenderPresent(renderer)) return 1; }
        if(auto notice=session.controls.takeProfileNotice()) { std::puts(notice->c_str());std::fflush(stdout); }
        const auto prompt=session.controls.passwordPrompt();
        if(text_input && !prompt.empty() && prompt!=password_prompt) {
            std::printf("SURFACE_PROFILE_PASSWORD_READY phase=%s\n",prompt.c_str());std::fflush(stdout);
        }
        password_prompt=prompt;
        if(home_focus_pending && !child.running() &&
           (SDL_GetWindowFlags(window)&SDL_WINDOW_INPUT_FOCUS)) {
            std::puts("SURFACE_HOME_FOCUS_READY");std::fflush(stdout);
            home_focus_pending=false;
        }
        if(self_test) {
            const bool valid=(test_step==0 && model.tile==1)||(test_step==1 && model.tile==2)||
                             (test_step==2 && model.overlay)||(test_step==3 && !model.overlay)||
                             (test_step==4 && model.shutdown_confirmation)||
                             (test_step==5 && !model.shutdown_confirmation && model.message.empty());
            if(!valid) { std::fprintf(stderr,"Injected input failed step=%d\n",test_step); return 2; }
            if(++test_step==6) { std::puts("SURFACE_SELF_TEST PASS keyboard gamepad touch overlay power-cancel"); quit=true; }
        }
    }
    child.stop();
    while(child.running()) { child.poll(); SDL_Delay(20); }
    for(auto* pad:pads) SDL_CloseGamepad(pad);
    home.reset();
    SDL_DestroyRenderer(renderer); SDL_DestroyWindow(window); SDL_Quit();
    return 0;
}
