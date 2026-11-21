#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace surface {
enum class Action { Left, Right, Up, Down, Accept, Back, Quick, Favorite, Details };
enum class Zone { Carousel, Dock };
struct App {
    std::string id, title, subtitle, executable;
    std::string artwork{}, system{};
    std::vector<std::string> arguments{};
    bool game = false;
    bool favorite = false;
    std::uint64_t play_seconds = 0, last_played = 0;
    unsigned performance = 0;
};
enum class Page { Home, Systems, Games, Favorites, Recent, Search };
inline constexpr float ui_width=1600.0F, ui_height=1066.0F;
class Model {
public:
    explicit Model(std::vector<App> applications);
    void navigate(Action action);
    const App& selected() const;
    std::vector<App> apps;
    std::size_t tile = 0;
    std::size_t dock = 0;
    Zone zone = Zone::Carousel;
    bool overlay = false;
    bool shutdown_confirmation = false;
    bool details = false;
    bool application_running = false;
    std::string message;
    Page page = Page::Home;
};
inline constexpr std::size_t dock_count = 8;
struct Rect { float x, y, w, h; bool contains(float px, float py) const; };
Rect tile_rect(std::size_t index, std::size_t selected);
Rect dock_rect(std::size_t index);
}
