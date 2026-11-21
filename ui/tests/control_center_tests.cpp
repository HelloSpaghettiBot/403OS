#include "surface/control_center.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>

namespace {
void select(surface::ControlCenter& center,const std::string& prefix) {
    const auto& rows=center.view().rows;
    std::size_t target=rows.size();
    for(std::size_t index=0;index<rows.size();++index) if(rows[index].starts_with(prefix)) { target=index; break; }
    assert(target<rows.size());
    for(std::size_t count=0;center.view().selected!=target && count<rows.size();++count) center.navigate(surface::Action::Down);
    assert(center.view().selected==target);
}
void cancelQuery(surface::ControlCenter& center) {
    if(center.view().busy) center.navigate(surface::Action::Back);
}
}

int main() {
    using namespace surface;
    ControlCenter center;
    assert(!center.active());
    center.open(PanelKind::Moonlight);
    assert(center.active());
    assert(center.view().rows.size()>=3);
    center.navigate(Action::Accept);
    assert(center.view().editing);
    assert(center.view().keyboard.size()%13==3);
    assert(center.view().keyboard[center.view().keyboard.size()-3]=="Done");
    center.inputText("A\xc3\xa9\n\t");
    assert(center.view().input=="A\xc3\xa9");
    center.backspace();
    assert(center.view().input=="A");
    center.backspace();
    assert(center.view().input.empty());
    for(unsigned i=0;i<300;++i) {
        center.navigate(Action::Down);
        center.navigate(Action::Right);
        center.navigate(Action::Up);
        center.navigate(Action::Left);
        assert(center.view().keyboard_index<center.view().keyboard.size());
    }
    center.inputText("invalid host; $(anything)");
    center.submitText();
    assert(!center.view().editing);
    assert(center.view().status.find("IP address or DNS")!=std::string::npos);
    assert(!center.takeLaunch());
    if(!std::filesystem::exists("/etc/surface-image-id")) {
        center.navigate(Action::Accept);
        center.inputText("sunshine.example");
        center.submitText();
        assert(center.view().status.find("only inside the Surface OS image")!=std::string::npos);
        assert(!center.takeLaunch());
    }
    center.navigate(Action::Back);
    assert(!center.active());
    center.open(PanelKind::Settings);
    for(unsigned i=0;i<50;++i) center.navigate(Action::Down);
    assert(center.view().selected<center.view().rows.size());
    center.navigate(Action::Back); // Cancel a possible read-only mixer query.
    if(center.active()) center.navigate(Action::Back);
    assert(!center.active());

    center.open(PanelKind::Library);
    select(center,"Search games"); center.navigate(Action::Accept);
    center.inputText("Pokemon Emerald"); center.submitText();
    assert(!center.active());
    assert(center.takeSearch()=="Pokemon Emerald");
    assert(!center.takeSearch());
    center.open(PanelKind::Library);
    center.navigate(Action::Accept); center.submitText();
    assert(center.takeSearch()=="");

    if(!std::filesystem::exists("/etc/surface-image-id")) {
        center.open(PanelKind::Moonlight);
        assert(center.view().rows[4].find("1280x720")!=std::string::npos);
        assert(center.view().rows[6].find("60 FPS")!=std::string::npos);
        select(center,"Custom resolution"); center.navigate(Action::Accept);
        while(!center.view().input.empty()) center.backspace();
        center.inputText("1281x800"); center.submitText();
        assert(center.view().status.find("even dimensions")!=std::string::npos);
        select(center,"Custom resolution"); center.navigate(Action::Accept);
        while(!center.view().input.empty()) center.backspace();
        center.inputText("1800x1200"); center.submitText();
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(center.view().rows[4].find("1280x720")!=std::string::npos);
        select(center,"Discover a Sunshine host"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);

        center.open(PanelKind::Settings); cancelQuery(center);
        select(center,"Display resolution"); center.navigate(Action::Accept);
        assert(center.view().title=="Display resolution");
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);
        center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        center.navigate(Action::Back);
        assert(center.view().title=="Settings"); cancelQuery(center);
        select(center,"Restart device"); center.navigate(Action::Accept);
        assert(center.view().rows[center.view().selected]=="Cancel");
        center.navigate(Action::Accept);
        assert(center.view().status=="Power action cancelled");
        select(center,"Suspend device"); center.navigate(Action::Accept);
        assert(center.view().rows[center.view().selected]=="Cancel");
        center.navigate(Action::Up); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);
        select(center,"Configure controller buttons"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.takeLaunch());
        select(center,"System and storage"); center.navigate(Action::Accept);
        assert(center.view().rows.size()==7);
        assert(center.view().rows[3].starts_with("/data:"));
        center.navigate(Action::Back);
        assert(center.active());

        center.open(PanelKind::Install);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().critical && !center.view().busy);
        center.open(PanelKind::Library);
        select(center,"Scan and download"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);
        center.open(PanelKind::DW3); cancelQuery(center);
        select(center,"Import owner-provided"); center.navigate(Action::Accept);
        assert(center.view().rows[center.view().selected]=="Cancel");
        center.navigate(Action::Back);
        assert(center.active());
        assert(center.view().status=="Import cancelled");
        assert(!center.takeLaunch());
        center.open(PanelKind::PokeMMO); cancelQuery(center);
        select(center,"Install PokeMMO"); center.navigate(Action::Accept);
        assert(center.view().rows[center.view().selected]=="Cancel");
        center.navigate(Action::Accept);
        assert(!center.view().busy);
        select(center,"Scan for supported"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);
        select(center,"ROM source folder"); center.navigate(Action::Accept);
        assert(center.view().editing);
        center.navigate(Action::Back);
        assert(!center.view().editing);
        center.open(PanelKind::Windows); cancelQuery(center);
        select(center,"Choose executable path"); center.navigate(Action::Accept);
        center.inputText("/media/library/owner $(literal); app.exe"); center.submitText();
        assert(center.view().rows[0].find("$(literal);")!=std::string::npos);
        select(center,"Save application reference"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.takeLaunch());
        select(center,"Set up Proton"); center.navigate(Action::Accept);
        assert(center.view().rows[center.view().selected]=="Cancel");
        center.navigate(Action::Accept);
        assert(!center.view().busy);
        center.open(PanelKind::Library);
        select(center,"Refresh external drives"); center.navigate(Action::Accept);
        assert(center.view().status.find("only inside")!=std::string::npos);
        assert(!center.view().busy);
        // Authentication cannot be dismissed into HOME or invoke host services.
        ControlCenter profiles;
        profiles.enableProfiles();
        assert(profiles.active() && profiles.profilesLocked());
        assert(!profiles.administrator() && !profiles.currentProfile());
        assert(profiles.view().status.find("only inside")!=std::string::npos);
        profiles.open(PanelKind::Install);
        assert(profiles.view().title.find("root administrator")!=std::string::npos);
        profiles.navigate(Action::Back);
        assert(profiles.active() && profiles.profilesLocked());
        profiles.navigate(Action::Accept);
        assert(profiles.view().editing && profiles.view().secret);
        assert(profiles.passwordPrompt()=="new");
        profiles.inputText("disposable-fixture-secret");profiles.submitText();
        assert(profiles.view().editing && profiles.view().secret);
        assert(profiles.passwordPrompt()=="confirm");
        profiles.inputText("different-fixture-secret");profiles.submitText();
        assert(!profiles.view().editing && profiles.view().input.empty());
        assert(profiles.profilesLocked() && !profiles.takeLaunch());
        assert(profiles.view().status.find("did not match")!=std::string::npos);
        profiles.navigate(Action::Accept);
        profiles.inputText("disposable-fixture-secret");profiles.submitText();
        profiles.inputText("disposable-fixture-secret");profiles.submitText();
        assert(profiles.profilesLocked() && !profiles.currentProfile());
        assert(profiles.view().status.find("only inside")!=std::string::npos);
        assert(profiles.view().input.empty());
        assert(profiles.view().status.find("disposable-fixture-secret")==std::string::npos);
    }
    std::cout<<"ControlCenter input, navigation, search, confirmation, and host-guard tests pass\n";
}
