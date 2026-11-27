#pragma once
#include "surface/model.hpp"
#include <SDL3/SDL.h>
#include <array>
#include <map>
#include <optional>
#include <cstdlib>

namespace surface {
class HomeInput {
    std::map<SDL_JoystickID,std::array<int,2>> axes;
public:
    std::optional<Action> action(const SDL_Event& event) {
        if(event.type==SDL_EVENT_GAMEPAD_REMOVED) axes.erase(event.gdevice.which);
        if(event.type==SDL_EVENT_GAMEPAD_AXIS_MOTION && event.gaxis.axis<=SDL_GAMEPAD_AXIS_LEFTY) {
            auto& state=axes[event.gaxis.which][event.gaxis.axis];
            const int value=event.gaxis.value;
            const int direction=value < -18000 ? -1 : value > 18000 ? 1 : 0;
            if(std::abs(value)<9000) state=0;
            if(direction && direction!=state) {
                state=direction;
                if(event.gaxis.axis==SDL_GAMEPAD_AXIS_LEFTX) return direction<0?Action::Left:Action::Right;
                return direction<0?Action::Up:Action::Down;
            }
            return {};
        }
    if (event.type == SDL_EVENT_KEY_DOWN && !event.key.repeat) {
        switch(event.key.key) {
        case SDLK_LEFT: return Action::Left;
        case SDLK_RIGHT: return Action::Right;
        case SDLK_UP: return Action::Up;
        case SDLK_DOWN: return Action::Down;
        case SDLK_RETURN: case SDLK_SPACE: return Action::Accept;
        case SDLK_ESCAPE: case SDLK_BACKSPACE: return Action::Back;
        case SDLK_TAB: return Action::Quick;
        case SDLK_F: return Action::Favorite;
        case SDLK_O: return Action::Details;
        default: break;
        }
    }
    if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN) {
        switch(event.gbutton.button) {
        case SDL_GAMEPAD_BUTTON_DPAD_LEFT: case SDL_GAMEPAD_BUTTON_LEFT_SHOULDER: return Action::Left;
        case SDL_GAMEPAD_BUTTON_DPAD_RIGHT: case SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER: return Action::Right;
        case SDL_GAMEPAD_BUTTON_DPAD_UP: return Action::Up;
        case SDL_GAMEPAD_BUTTON_DPAD_DOWN: return Action::Down;
        case SDL_GAMEPAD_BUTTON_SOUTH: return Action::Accept;
        case SDL_GAMEPAD_BUTTON_EAST: return Action::Back;
        case SDL_GAMEPAD_BUTTON_START: case SDL_GAMEPAD_BUTTON_GUIDE: return Action::Quick;
        case SDL_GAMEPAD_BUTTON_NORTH: return Action::Favorite;
        case SDL_GAMEPAD_BUTTON_WEST: return Action::Details;
        default: break;
        }
    }
    return std::nullopt;
}
};
}
