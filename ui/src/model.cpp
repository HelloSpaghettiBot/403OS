#include "surface/model.hpp"
#include <stdexcept>
#include <utility>

namespace surface {
Model::Model(std::vector<App> applications) : apps(std::move(applications)) {
    if (apps.empty()) throw std::invalid_argument("At least one built-in application is required");
}
const App& Model::selected() const { return apps.at(tile); }
void Model::navigate(Action action) {
    if (action == Action::Back) { overlay = false; details = false; shutdown_confirmation = false; message.clear(); return; }
    if (action == Action::Quick) {
        details = false;
        shutdown_confirmation = false;
        message.clear();
        overlay = !overlay;
        return;
    }
    if(action == Action::Details) { details=!details; return; }
    if (overlay || !message.empty()) return;
    if (action == Action::Up) zone = Zone::Carousel;
    if (action == Action::Down) zone = Zone::Dock;
    auto& index = zone == Zone::Carousel ? tile : dock;
    const auto count = zone == Zone::Carousel ? apps.size() : dock_count;
    if (action == Action::Left) index = (index + count - 1) % count;
    if (action == Action::Right) index = (index + 1) % count;
}
bool Rect::contains(float px, float py) const {
    return px >= x && py >= y && px < x + w && py < y + h;
}
Rect tile_rect(std::size_t index, std::size_t selected) {
    // Scroll the selected tile into the first slot; all navigation stays visible.
    const auto offset=static_cast<float>(index)-static_cast<float>(selected);
    if(index==selected) return {64,154,400,430};
    return {offset>0 ? 490+(offset-1)*282 : 64+offset*282,
            205,260,365};
}
Rect dock_rect(std::size_t index) { return {60.0F + static_cast<float>(index)*187.0F, 832.0F, 170.0F, 145.0F}; }
}
