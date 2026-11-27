#pragma once
#include "surface/model.hpp"
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace surface {
enum class PanelKind { Settings, Network, Bluetooth, Tailscale, Moonlight, Install, PokeMMO, Library, DW3, Windows, Display, Profiles, Updates };
struct ProfileIdentity {
    std::string name,home,state;
    unsigned uid=0,gid=0;
    bool admin=false;
};
struct ControlView {
    std::string title;
    std::vector<std::string> rows;
    std::size_t selected = 0;
    std::string status;
    bool editing = false;
    bool secret = false;
    std::string input;
    std::size_t keyboard_index = 0;
    // Render these exact cells, 13 columns per row. Final row: Done/Delete/Cancel.
    std::vector<std::string> keyboard;
    bool busy = false;
    bool critical = false; // Installation/update active: keep the session and power on.
};
class ControlCenter {
public:
    ControlCenter();
    ~ControlCenter();
    ControlCenter(const ControlCenter&) = delete;
    ControlCenter& operator=(const ControlCenter&) = delete;
    void open(PanelKind panel);
    bool active() const;
    void navigate(Action action);
    void touch(float x,float y);
    void scroll(int rows);
    void inputText(std::string text);
    void backspace();
    void submitText();
    void update();
    const ControlView& view() const;
    std::optional<std::vector<std::string>> takeLaunch();
    std::optional<std::string> takeSearch();
    void enableProfiles();
    bool profilesLocked() const;
    bool administrator() const;
    std::optional<ProfileIdentity> currentProfile() const;
    bool takeProfileChanged();
    bool takeLockRequest();
    void lockProfiles();
    std::string passwordPrompt() const;
    std::optional<std::string> takeProfileNotice();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
