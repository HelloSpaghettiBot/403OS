#include "surface/status.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
using namespace surface;
namespace fs=std::filesystem;
#define CHECK(value) do { if(!(value)) throw std::runtime_error(#value); } while(false)
int main() {
    auto state=parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":true}})");
    CHECK(state.connected && state.text=="Tailscale connected");
    state=parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":false},"Peer":{"remote":{"Online":true}}})");
    CHECK(!state.connected && state.text=="Tailscale offline");
    state=parse_tailscale_status(R"({"BackendState":"Running","Peer":{"remote":{"Online":true}}})");
    CHECK(!state.connected && state.text=="Tailscale state unverified");
    state=parse_tailscale_status(R"({"BackendState":"NeedsLogin","Self":{"Online":true}})");
    CHECK(!state.connected && state.text=="Tailscale sign-in required");
    state=parse_tailscale_status(R"({"BackendState":"NeedsMachineAuth"})");
    CHECK(!state.connected && state.text=="Tailscale approval required");
    state=parse_tailscale_status(R"({"BackendState":"Stopped","Peer":{"BackendState":"Running","Self":{"Online":true}}})");
    CHECK(!state.connected && state.text=="Tailscale stopped");
    CHECK(!parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":"true"}})").connected);
    CHECK(!parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":true},"Self":{"Online":false}})").connected);
    CHECK(!parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":true}}garbage)").connected);
    CHECK(!parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":true}})").text.empty());
    CHECK(parse_tailscale_status(R"({"Backend\u0053tate":"Running","Self":{"Onl\u0069ne":true},"Ignored":[-1.5e2,null,false,"\ud83d\ude00"]})").connected);
    CHECK(!parse_tailscale_status(R"({"BackendState":"Running","Self":{"Online":true},"Bad":"\ud800"})").connected);
    CHECK(!parse_tailscale_status(std::string(300000,' ')).connected);
    const auto temp=fs::temp_directory_path()/("surface-status-test-"+std::to_string(getpid())); fs::create_directories(temp);
    const auto cli=temp/"tailscale fixture;false";
    {
        std::ofstream script(cli);
        script<<"#!/bin/sh\n[ \"$#\" -eq 2 ] && [ \"$1\" = status ] && [ \"$2\" = --json ] || exit 8\n"
                "printf '%s' '{\"BackendState\":\"Running\",\"Self\":{\"Online\":true}}'\n";
    }
    chmod(cli.c_str(),0700); state=read_tailscale_status(cli); CHECK(state.connected);
    {
        std::ofstream script(cli); script<<"#!/bin/sh\nsleep 4\n";
    }
    const auto began=std::chrono::steady_clock::now(); state=read_tailscale_status(cli,std::chrono::milliseconds(100));
    const auto elapsed=std::chrono::steady_clock::now()-began;
    CHECK(!state.connected && state.text=="Tailscale status timed out"); CHECK(elapsed<std::chrono::seconds(1));
    CHECK(!read_tailscale_status(temp/"missing").connected);
    fs::remove_all(temp);
    std::cout<<"PASS truthful Tailscale JSON scope, malformed data, argv preservation and bounded timeout\n";
}
