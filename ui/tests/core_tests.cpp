#include "surface/model.hpp"
#include "surface/process.hpp"
#include "surface/status.hpp"
#include "surface/progress.hpp"
#include "surface/library.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <unistd.h>
using namespace surface;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(false)
int main() {
    Model m({{"one","One","",""},{"two","Two","",""},{"three","Three","",""}});
    m.navigate(Action::Left); CHECK(m.tile==2);
    m.navigate(Action::Right); CHECK(m.tile==0);
    m.navigate(Action::Down); m.navigate(Action::Left); CHECK(m.dock==7);
    m.navigate(Action::Quick); m.navigate(Action::Right); CHECK(m.dock==7 && m.overlay);
    m.navigate(Action::Back); CHECK(!m.overlay);
    m.shutdown_confirmation=true;
    m.message="Confirm power off";
    m.navigate(Action::Quick);
    CHECK(m.overlay && !m.shutdown_confirmation && m.message.empty());
    m.navigate(Action::Back);
    m.navigate(Action::Up); m.navigate(Action::Right); CHECK(m.tile==1);
    CHECK(tile_rect(100,100).x==64);
    CHECK(dock_rect(7).x+dock_rect(7).w<=ui_width);
    CHECK((!Rect{0,0,10,10}.contains(10,5)));
    const auto temp=std::filesystem::temp_directory_path()/ ("surface-test-"+std::to_string(getpid()));
    std::filesystem::create_directories(temp/"class/net/eth0");
    std::filesystem::create_directories(temp/"class/power_supply/BAT0");
    std::ofstream(temp/"class/net/eth0/operstate")<<"up\n";
    std::ofstream(temp/"class/power_supply/BAT0/type")<<"Battery\n";
    std::ofstream(temp/"class/power_supply/BAT0/capacity")<<"62\n";
    std::ofstream(temp/"class/power_supply/BAT0/status")<<"Discharging\n";
    auto s=read_status(temp); CHECK(s.network=="Ethernet link up"); CHECK(s.battery=="62% Discharging");
    CHECK(s.bluetooth=="BT unavailable"); CHECK(!s.time.empty());
    {
        const auto game=temp/"Game with spaces.gba";
        std::ofstream(game)<<"fixture";
        std::ofstream(temp/"catalog.tsv")<<"game1\tFixture title\tgba\t"<<game.string()<<"\t\n"
                                        <<"bad\tMissing\tnds\t/missing/game.nds\t\n";
        auto library=read_library(temp/"catalog.tsv");
        CHECK(library.games.size()==1 && library.games[0].game);
        CHECK(library.games[0].arguments[3]==game.string());
        ProgressStore state(temp/"home/progress.tsv");
        state.favorite("game1",true);state.played("game1",71,123456);state.performance("game1",2);
        ProgressStore reloaded(temp/"home/progress.tsv");reloaded.apply(library.games);
        CHECK(library.games[0].favorite && library.games[0].play_seconds==71 && library.games[0].last_played==123456 && library.games[0].performance==2);
        reloaded.favorite("game1",false);reloaded.played("game1",9,123999);
        ProgressStore again(temp/"home/progress.tsv");again.apply(library.games);
        CHECK(!library.games[0].favorite && library.games[0].play_seconds==80 && library.games[0].last_played==123999);
    }
    std::filesystem::remove_all(temp);
    Process p; CHECK(!p.start({"/missing/surface-browser"})); CHECK(!p.running()); CHECK(!p.error.empty());
    CHECK(p.start({"/bin/false"})); CHECK(!p.start({"/bin/true"}));
    std::optional<int> code;
    for(int i=0;i<100 && !code;++i) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); code=p.poll(); }
    CHECK(code && *code==1); CHECK(!p.running());
    CHECK(p.start({"/bin/sleep","60"}));p.stop();code.reset();
    for(int i=0;i<100 && !code;++i) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); code=p.poll(); }
    CHECK(code && *code==0 && !p.running());
    std::filesystem::create_directories(temp);
    const auto ready=temp/"supervisor-ready";
    CHECK(p.start({"/bin/sh","-c","trap 'exit 143' TERM; : > \"$1\"; while :; do sleep 1; done","surface-test",ready.string()}));
    for(int i=0;i<200 && !std::filesystem::exists(ready);++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    CHECK(std::filesystem::exists(ready));p.stop();code.reset();
    for(int i=0;i<100 && !code;++i) { std::this_thread::sleep_for(std::chrono::milliseconds(10));code=p.poll(); }
    CHECK(code && *code==0);std::filesystem::remove_all(temp);
    CHECK(p.start({"/bin/sh","-c","exit 143"}));code.reset();
    for(int i=0;i<100 && !code;++i) { std::this_thread::sleep_for(std::chrono::milliseconds(10));code=p.poll(); }
    CHECK(code && *code==143);
    CHECK(p.start({"/bin/true"}));
    for(int i=0;i<100;++i) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); if(auto c=p.poll()) { CHECK(*c==0); std::cout<<"PASS navigation, status, process exit and recovery\n"; return 0; } }
    throw std::runtime_error("Child did not exit");
}
