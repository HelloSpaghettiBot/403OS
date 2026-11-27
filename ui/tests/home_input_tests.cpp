#include "surface/home_input.hpp"
#include <cassert>
#include <iostream>

int main() {
    using namespace surface;
    HomeInput input;
    SDL_Event event{};
    event.type=SDL_EVENT_GAMEPAD_AXIS_MOTION;
    event.gaxis.which=1;
    event.gaxis.axis=SDL_GAMEPAD_AXIS_LEFTX;
    event.gaxis.value=22000;
    assert(input.action(event)==Action::Right);
    assert(!input.action(event)); // Held/noisy axis must not race through menus.
    event.gaxis.value=14000;assert(!input.action(event));
    event.gaxis.value=21000;assert(!input.action(event));
    event.gaxis.value=0;assert(!input.action(event));
    event.gaxis.value=-24000;assert(input.action(event)==Action::Left);
    event.gaxis.axis=SDL_GAMEPAD_AXIS_LEFTY;
    assert(input.action(event)==Action::Up);
    event.gaxis.value=26000;assert(input.action(event)==Action::Down);
    event.gaxis.axis=SDL_GAMEPAD_AXIS_RIGHTX;assert(!input.action(event));
    event.gaxis.axis=SDL_GAMEPAD_AXIS_LEFTX;
    event.gaxis.which=2;assert(input.action(event)==Action::Right);
    event.type=SDL_EVENT_GAMEPAD_REMOVED;event.gdevice.which=2;assert(!input.action(event));
    event.type=SDL_EVENT_GAMEPAD_AXIS_MOTION;
    event.gaxis.which=2;event.gaxis.axis=SDL_GAMEPAD_AXIS_LEFTX;event.gaxis.value=26000;
    assert(input.action(event)==Action::Right);
    event.type=SDL_EVENT_GAMEPAD_BUTTON_DOWN;
    for(auto [button,expected]:{std::pair{SDL_GAMEPAD_BUTTON_SOUTH,Action::Accept},
        {SDL_GAMEPAD_BUTTON_EAST,Action::Back},{SDL_GAMEPAD_BUTTON_DPAD_RIGHT,Action::Right},
        {SDL_GAMEPAD_BUTTON_START,Action::Quick}}) {
        event.gbutton.button=button;assert(input.action(event)==expected);
    }
    std::cout<<"PASS HOME analog stick, dead zone, independent controllers, reconnect and buttons\n";
}
