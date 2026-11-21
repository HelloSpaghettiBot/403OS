#pragma once
#include "surface/model.hpp"
#include "surface/status.hpp"
#include <memory>
struct SDL_Renderer;
namespace surface {
struct ControlView;
class HomeRenderer {
public:
    explicit HomeRenderer(SDL_Renderer* renderer);
    ~HomeRenderer();
    void draw(const Model&,const Status&,const ControlView* controls=nullptr);
    bool update();
private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
}
