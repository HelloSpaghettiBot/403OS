// Exercise the same window controller used by the executable, with SDL's dummy
// video driver and a real SDL virtual joystick. No host window or guest guard bypass.
#define SURFACE_CONTROLLER_SETUP_NO_MAIN
#include "../src/controller_setup.cpp"
#include <iostream>

#define CHECK(value) do {if(!(value))throw std::runtime_error(#value);}while(false)
void key(Setup& setup,SDL_Keycode code) {SDL_Event event{};event.type=SDL_EVENT_KEY_DOWN;event.key.key=code;setup.event(event);}
void dispatch(Setup& setup) {SDL_UpdateJoysticks();SDL_Event event;while(SDL_PollEvent(&event))setup.event(event);}
void arm(Setup& setup) {setup.tick();SDL_Delay(240);setup.tick();}
int main() {
    CHECK(device_name("Unidentified",0x1949,0x0402)=="iPega PG-9023");
    CHECK(device_name("PG-9023S Keyboard",0x1949,0x0402)=="iPega PG-9023");
    CHECK(device_name("Unidentified",0x1949,0x9999)=="iPega controller");
    CHECK(device_name("Unidentified",0,0)=="Unnamed controller");
    SDL_Window* window=nullptr;SDL_Renderer* renderer=nullptr;
    try {
        CHECK(SDL_SetHintWithPriority(SDL_HINT_VIDEO_DRIVER,"dummy",SDL_HINT_OVERRIDE));SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI,"0");
        CHECK(SDL_Init(SDL_INIT_VIDEO|SDL_INIT_JOYSTICK|SDL_INIT_GAMEPAD));
        window=SDL_CreateWindow("Headless controller test",1200,800,0);CHECK(window);
        renderer=SDL_CreateRenderer(window,"software");CHECK(renderer);
        CHECK(SDL_SetRenderLogicalPresentation(renderer,int(ui_width),int(ui_height),SDL_LOGICAL_PRESENTATION_LETTERBOX));
        SDL_VirtualJoystickDesc description{};SDL_INIT_INTERFACE(&description);
        description.type=SDL_JOYSTICK_TYPE_UNKNOWN;description.vendor_id=0x1209;description.product_id=0x0404;
        description.naxes=6;description.nbuttons=16;description.nhats=1;description.name="Wizard Interaction Test";
        const auto id=SDL_AttachVirtualJoystick(&description);CHECK(id);
        auto* joystick=SDL_OpenJoystick(id);CHECK(joystick);
        char guid[33]{};SDL_GUIDToString(SDL_GetJoystickGUID(joystick),guid,sizeof(guid));
        const auto mapping=std::string(guid)+",Wizard Interaction Test,a:b0,b:b1,x:b2,y:b3,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1~,rightx:a2,righty:a3,lefttrigger:+a4,righttrigger:+a5,platform:Linux,";
        CHECK(SDL_AddGamepadMapping(mapping.c_str())>=0);
        {
            Setup setup(window,renderer);dispatch(setup);
            auto view=setup.view();CHECK(view.rows.back()=="Return to HOME");CHECK(view.rows[view.rows.size()-2]=="Refresh controller list");
            std::size_t index=0;
            while(index<view.rows.size() && view.rows[index].find("Wizard Interaction Test")==std::string::npos)++index;
            CHECK(index<view.rows.size());CHECK(view.rows[index].find("[connected, mapped]")!=std::string::npos);
            for(std::size_t i=0;i<index;++i)key(setup,SDLK_DOWN);
            key(setup,SDLK_RETURN);CHECK(setup.view().rows[0].starts_with("Release every button"));
            SDL_Delay(10);setup.tick();SDL_Delay(950);setup.tick();arm(setup);
            CHECK(setup.view().rows[0].find("A / south")!=std::string::npos);
            CHECK(SDL_SetJoystickVirtualButton(joystick,3,true));dispatch(setup);
            CHECK(setup.view().rows[0].find("B / east")!=std::string::npos);
            CHECK(SDL_SetJoystickVirtualButton(joystick,3,false));dispatch(setup);arm(setup);
            // This produces both raw b1 and mapped B. Mapped B must not cancel capture.
            CHECK(SDL_SetJoystickVirtualButton(joystick,1,true));dispatch(setup);
            CHECK(setup.view().rows[0].find("X / west")!=std::string::npos);
            CHECK(SDL_SetJoystickVirtualButton(joystick,1,false));dispatch(setup);
            for(std::size_t i=2;i<steps.size();++i)key(setup,SDLK_SPACE);
            view=setup.view();CHECK(view.title=="Review controller mapping");
            CHECK(view.rows[1].find("b3")!=std::string::npos);
            CHECK(view.rows[2].find("b1")!=std::string::npos);
            CHECK(view.rows[15].find("a0")!=std::string::npos);
            CHECK(view.rows[16].find("a1~")!=std::string::npos);
            // Select an existing binding for editing and explicitly cancel using Escape.
            key(setup,SDLK_DOWN);key(setup,SDLK_RETURN);
            CHECK(setup.view().title.starts_with("Map "));
            key(setup,SDLK_ESCAPE);CHECK(setup.view().title=="Controllers / button mapping");
            // A touch on the visible Cancel row must discard calibration safely.
            setup.start(id);float x=0,y=0;
            CHECK(SDL_RenderCoordinatesToWindow(renderer,300,410,&x,&y));
            SDL_Event touch{};touch.type=SDL_EVENT_MOUSE_BUTTON_DOWN;touch.button.which=1;touch.button.x=x;touch.button.y=y;
            setup.event(touch);CHECK(setup.view().title=="Controllers / button mapping");
            CHECK(!setup.finished());
        }
        SDL_CloseJoystick(joystick);CHECK(SDL_DetachVirtualJoystick(id));
        SDL_DestroyRenderer(renderer);renderer=nullptr;SDL_DestroyWindow(window);window=nullptr;SDL_Quit();
        std::cout<<"PASS guided raw capture, mapped B capture without accidental cancel, skipped analog assignments, keyboard edit/cancel and touch cancel on SDL dummy display\n";
    } catch(const std::exception& error) {
        std::cerr<<"FAIL "<<error.what()<<" SDL="<<SDL_GetError()<<'\n';
        if(renderer)SDL_DestroyRenderer(renderer);
        if(window)SDL_DestroyWindow(window);
        SDL_Quit();return 1;
    }
}
