#include "surface/renderer.hpp"
#include "surface/control_center.hpp"
#include <SDL3/SDL.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <future>
#include <map>
#include <numbers>
#include <stdexcept>
#include <vector>

namespace surface {
namespace {
constexpr SDL_Color white{245,247,255,255}, muted{184,188,205,255}, cyan{108,225,255,255};
struct Pixels { int width=0,height=0; std::vector<unsigned char> bytes; };
Pixels read_pixels(const std::filesystem::path& path) {
    std::ifstream file(path,std::ios::binary);
    std::array<char,8> magic{};
    std::array<unsigned char,8> dimensions{};
    file.read(magic.data(),8); file.read(reinterpret_cast<char*>(dimensions.data()),8);
    if(!file || std::string(magic.data(),8)!="SGOTEX01") return {};
    auto u32=[&](int at) { unsigned v=0; for(int i=0;i<4;++i) v|=unsigned(dimensions[at+i])<<(i*8); return v; };
    const unsigned w=u32(0),h=u32(4);
    if(w==0 || h==0 || w>8192 || h>8192 || std::uint64_t(w)*h>16*1024*1024) return {};
    Pixels p{int(w),int(h),std::vector<unsigned char>(std::size_t(w)*h*4)};
    file.read(reinterpret_cast<char*>(p.bytes.data()),static_cast<std::streamsize>(p.bytes.size()));
    if(!file) return {};
    return p;
}
SDL_Texture* upload(SDL_Renderer* renderer,const Pixels& p) {
    if(p.bytes.empty()) return nullptr;
    auto* t=SDL_CreateTexture(renderer,SDL_PIXELFORMAT_RGBA32,SDL_TEXTUREACCESS_STATIC,p.width,p.height);
    if(t) { SDL_UpdateTexture(t,nullptr,p.bytes.data(),p.width*4); SDL_SetTextureBlendMode(t,SDL_BLENDMODE_BLEND); SDL_SetTextureScaleMode(t,SDL_SCALEMODE_LINEAR); }
    return t;
}
void rectangle(SDL_Renderer* r,Rect b,SDL_Color c) {
    SDL_SetRenderDrawColor(r,c.r,c.g,c.b,c.a); const SDL_FRect f{b.x,b.y,b.w,b.h}; SDL_RenderFillRect(r,&f);
}
void rounded(SDL_Renderer* r,Rect b,float radius,SDL_Color color,SDL_Texture* texture=nullptr) {
    radius=std::min({radius,b.w/2,b.h/2});
    const SDL_FColor c{color.r/255.0F,color.g/255.0F,color.b/255.0F,color.a/255.0F};
    std::vector<SDL_Vertex> points{{{b.x+b.w/2,b.y+b.h/2},c,{0.5F,0.5F}}};
    for(int corner=0;corner<4;++corner) {
        float cx=(corner==0 || corner==3)?b.x+b.w-radius:b.x+radius;
        float cy=corner<2?b.y+b.h-radius:b.y+radius;
        for(int i=0;i<=12;++i) {
            float a=float(corner)*std::numbers::pi_v<float>/2+float(i)*std::numbers::pi_v<float>/24;
            float x=cx+std::cos(a)*radius,y=cy+std::sin(a)*radius;
            points.push_back({{x,y},c,{(x-b.x)/b.w,(y-b.y)/b.h}});
        }
    }
    std::vector<int> indices;
    for(std::size_t i=1;i<points.size();++i) { indices.push_back(0);indices.push_back(int(i));indices.push_back(i+1<points.size()?int(i+1):1); }
    SDL_RenderGeometry(r,texture,points.data(),int(points.size()),indices.data(),int(indices.size()));
}
std::vector<unsigned> codepoints(const std::string& text) {
    std::vector<unsigned> result;
    for(std::size_t i=0;i<text.size();++i) {
        unsigned c=static_cast<unsigned char>(text[i]);
        if(c>=0xc2 && c<=0xc3 && i+1<text.size()) c=((c&31)<<6)|(static_cast<unsigned char>(text[++i])&63);
        else if(c>=128) { while(i+1<text.size() && (static_cast<unsigned char>(text[i+1])&0xc0)==0x80) ++i; c='?'; }
        result.push_back(c>=32 && c<256?c:' ');
    }
    return result;
}
}
struct HomeRenderer::Impl {
    SDL_Renderer* r;
    std::filesystem::path assets;
    SDL_Texture *wallpaper=nullptr,*icons=nullptr,*regular=nullptr,*bold=nullptr;
    std::array<float,224> regular_width{},bold_width{};
    std::map<std::string,SDL_Texture*> covers;
    std::deque<std::string> loaded_order;
    std::future<Pixels> pending;
    std::string loading;
    explicit Impl(SDL_Renderer* renderer):r(renderer) {
        if(const auto* custom=std::getenv("SURFACE_ASSETS")) assets=custom;
        else assets="/usr/share/surface-home";
        if(!std::filesystem::exists(assets/"wallpaper.sgo") && std::filesystem::exists("ui/assets/wallpaper.sgo")) assets="ui/assets";
        wallpaper=upload(r,read_pixels(assets/"wallpaper.sgo")); icons=upload(r,read_pixels(assets/"icons.sgo"));
        regular=upload(r,read_pixels(assets/"font-regular.sgo")); bold=upload(r,read_pixels(assets/"font-bold.sgo"));
        std::ifstream(assets/"font-regular.metrics",std::ios::binary).read(reinterpret_cast<char*>(regular_width.data()),sizeof(regular_width));
        std::ifstream(assets/"font-bold.metrics",std::ios::binary).read(reinterpret_cast<char*>(bold_width.data()),sizeof(bold_width));
        if(!wallpaper || !icons || !regular || !bold) throw std::runtime_error("HOME assets are missing or invalid: "+assets.string());
        SDL_SetRenderDrawBlendMode(r,SDL_BLENDMODE_BLEND);
    }
    ~Impl() {
        if(pending.valid()) pending.wait();
        for(auto [key,t]:covers) { (void)key; SDL_DestroyTexture(t); }
        for(auto* t:{wallpaper,icons,regular,bold}) SDL_DestroyTexture(t);
    }
    float width(const std::string& s,float size,bool heavy=false) const {
        float w=0; for(auto c:codepoints(s)) w+=(heavy?bold_width:regular_width)[c-32]*size/48; return w;
    }
    void text(float x,float y,const std::string& s,float size=26,SDL_Color c=white,bool heavy=false,float max_width=1600) {
        auto* atlas=heavy?bold:regular;
        SDL_SetTextureColorMod(atlas,c.r,c.g,c.b);SDL_SetTextureAlphaMod(atlas,c.a);
        const float scale=size/48;
        for(auto ch:codepoints(s)) {
            unsigned i=ch-32; float advance=(heavy?bold_width:regular_width)[i]*scale;
            if(x+advance>max_width) break;
            SDL_FRect src{float(i%16)*64,float(i/16)*80,64,80},dst{x-2*scale,y-9*scale,64*scale,80*scale};
            SDL_RenderTexture(r,atlas,&src,&dst);x+=advance;
        }
    }
    void icon(int index,float x,float y,float size,SDL_Color c=white) {
        SDL_SetTextureColorMod(icons,c.r,c.g,c.b);SDL_SetTextureAlphaMod(icons,c.a);
        SDL_FRect src{float(index*96),0,96,96},dst{x,y,size,size}; SDL_RenderTexture(r,icons,&src,&dst);
    }
    void glow(Rect b,SDL_Color c,int amount=12) {
        for(int i=amount;i>0;--i) {
            auto v=c;v.a=static_cast<Uint8>(i<4?40:8);
            rounded(r,{b.x-i,b.y-i,b.w+2*i,b.h+2*i},26+i,v);
        }
    }
    SDL_Texture* art(const std::string& key) {
        if(key.empty()) return nullptr;
        auto found=covers.find(key);if(found!=covers.end()) return found->second;
        if(!pending.valid()) { loading=key;pending=std::async(std::launch::async,[key]{return read_pixels(key);}); }
        return nullptr;
    }
    void card(const App& app,Rect b,bool focused) {
        if(focused) glow(b,cyan,17);
        rounded(r,{b.x-3,b.y-3,b.w+6,b.h+6},24,focused?cyan:SDL_Color{88,89,112,180});
        rounded(r,b,22,{26,30,48,255});
        if(auto* t=art(app.artwork)) rounded(r,{b.x+2,b.y+2,b.w-4,b.h-4},20,white,t);
        else {
            const std::array<SDL_Color,5> palettes{{{39,50,116,255},{55,30,96,255},{15,71,71,255},{65,39,85,255},{23,42,84,255}}};
            auto p=palettes[std::hash<std::string>{}(app.id)%palettes.size()];
            rounded(r,{b.x+2,b.y+2,b.w-4,b.h-4},20,p);
            for(int j=0;j<8;++j) rounded(r,{b.x+b.w/2-65+j*4,b.y+55+j*3,130-j*8.0F,130-j*8.0F},28,{145,180,255,static_cast<Uint8>(8+j*3)});
            int type=app.id=="moonlight"?2:app.id=="browser"?3:app.id=="tailscale"?4:app.game?0:10;
            icon(type,b.x+(b.w-140)/2,b.y+65,140);
            text(b.x+24,b.y+b.h-122,app.title,focused?32:25,white,true,b.x+b.w-14);
            text(b.x+24,b.y+b.h-72,app.system.empty()?"APPLICATION":app.system,18,muted,false,b.x+b.w-12);
        }
        if(focused) rounded(r,{b.x-1,b.y-1,b.w+2,b.h+2},22,{255,255,255,26});
    }
    void draw(const Model& m,const Status& s,const ControlView* controls) {
        SDL_SetRenderDrawColor(r,6,8,18,255);SDL_RenderClear(r);
        SDL_FRect screen{0,0,ui_width,ui_height};SDL_RenderTexture(r,wallpaper,nullptr,&screen);
        rectangle(r,{0,0,ui_width,ui_height},{7,8,22,70});
        for(int i=0;i<24;++i) rectangle(r,{0,650+i*18.0F,ui_width,18},{5,7,16,static_cast<Uint8>(8+i*6)});
        icon(0,62,31,65);text(147,44,"403 OS",40,white,false);
        float right=1535;
        auto clock=s.time; const float clock_width=width(clock,30);
        text(right-clock_width,47,clock,30);right-=clock_width+35;
        const bool has_battery=s.battery!="Battery unavailable";
        const auto percent=has_battery?s.battery.substr(0,s.battery.find(' ')):"--";
        icon(9,right-60,34,60,has_battery?white:muted);
        if(has_battery) {
            const float capacity=std::clamp(std::strtof(percent.c_str(),nullptr),0.0F,100.0F);
            rectangle(r,{right-60+13.1F,57.1F,capacity*0.306F,13.1F},white);
        }
        right-=65+width(percent,27);
        text(right,49,percent,27,has_battery?white:muted);right-=88;
        const bool online=s.network.find(" up")!=std::string::npos;
        icon(8,right,31,61,online?white:muted);right-=85;
        if(s.network.starts_with("Ethernet")) text(right+98,94,"LAN",13,muted);
        icon(5,right,32,58,s.bluetooth=="BT unavailable"?muted:white);right-=85;
        icon(4,right,30,60,s.vpn_connected?white:muted);
        if(!s.vpn_connected) text(right+10,94,"OFF",13,muted);
        if(!has_battery) text(1371,95,"NO BATTERY",12,muted);
        const auto first=m.tile;
        for(std::size_t i=0;i<m.apps.size();++i) {
            if(i==first) continue;
            auto b=tile_rect(i,first);if(b.x+b.w>0 && b.x<ui_width) card(m.apps[i],b,false);
        }
        card(m.selected(),tile_rect(first,first),true);
        text(72,621,m.selected().title,38,white,true,1530);
        icon(10,74,684,38,{175,134,255,255});
        std::string metadata=(m.selected().favorite?"Favorite  |  ":"")+m.selected().subtitle;
        if(m.selected().game) metadata+=m.selected().last_played?"  |  Played recently":"  |  Not played yet";
        text(124,691,metadata,26,muted,false,1515);
        const char* section=m.page==Page::Systems?"EMULATOR SYSTEMS":m.page==Page::Games?"ALL GAMES":m.page==Page::Favorites?"FAVORITES":m.page==Page::Recent?"RECENTLY PLAYED":m.page==Page::Search?"SEARCH RESULTS":"YOUR LIBRARY";
        text(75,762,section,15,{174,179,204,255},true);
        if(m.selected().game) text(75,794,"Game data on your library drive",18,muted);
        static const std::array<std::string,8> labels{"Games","Emulators","Moonlight","Browser","Tailscale","Controllers","Settings","Power"};
        for(std::size_t i=0;i<dock_count;++i) {
            auto b=dock_rect(i);
            bool focused=m.zone==Zone::Dock && m.dock==i;
            bool active=(m.page==Page::Systems?i==1:i==0);
            if(focused || active) glow(b,{156,115,255,255},focused?14:8);
            rounded(r,{b.x-2,b.y-2,b.w+4,b.h+4},27,focused?SDL_Color{233,220,255,255}:active?SDL_Color{187,160,255,255}:SDL_Color{110,112,137,100});
            rounded(r,b,25,active || focused?SDL_Color{94,48,188,245}:SDL_Color{30,32,46,230});
            if(active || focused) for(int band=0;band<18;++band) rectangle(r,{b.x+14,b.y+15+band*6.0F,b.w-28,6},{129,80,235,static_cast<Uint8>(43-band*2)});
            icon(int(i),b.x+(b.w-78)/2,b.y+15,78);
            auto tw=width(labels[i],24);text(b.x+(b.w-tw)/2,b.y+108,labels[i],24,white,false);
        }
        rounded(r,{1230,1018,31,31},16,white);text(1238,1021,"A",22,{15,17,27,255},true);
        text(1273,1021,"Open",23);rounded(r,{1395,1018,31,31},16,white);text(1402,1019,"+",25,{15,17,27,255},true);text(1438,1021,"Options",23);
        text(73,1023,"B  Back     L / R  Browse",18,muted);
        text(460,1023,"Close app: Ctrl+Alt+Home / 3-finger touch",17,muted);
        if(m.overlay || !m.message.empty()) {
            rectangle(r,{0,0,ui_width,ui_height},{4,5,15,190});
            glow({240,275,1120,475},{121,93,255,255},20);
            rounded(r,{238,273,1124,479},30,{128,116,176,200});rounded(r,{240,275,1120,475},28,{24,26,43,252});
            text(295,320,m.overlay?"Quick settings":"Application",34,white,true);
            if(m.overlay) {
                const std::array<std::string,4> rows{s.network,s.bluetooth,s.battery,s.vpn};
                for(std::size_t i=0;i<rows.size();++i) { icon(i==0?8:i==1?5:i==2?9:4,295,385+i*60.0F,40,muted);text(358,394+i*60.0F,rows[i],26); }
            } else {
                std::size_t at=0;int row=0;
                while(at<m.message.size() && row<5) {
                    std::size_t end=std::min(at+68,m.message.size());
                    if(end<m.message.size()) { auto space=m.message.rfind(' ',end);if(space>at && space!=std::string::npos)end=space; }
                    text(295,402+row*43.0F,m.message.substr(at,end-at),25);at=end+(end<m.message.size() && m.message[end]==' '?1:0);++row;
                }
            }
            if(m.overlay && m.application_running) {
                rounded(r,{295,650,300,75},14,{69,51,115,255});text(315,674,"A / B   Resume",24);
                rounded(r,{660,650,300,75},14,{69,51,115,255});text(680,674,"X   Close app",24);
                text(1000,674,"Y   Settings",24,muted);
            } else text(295,690,"B / Esc   Close",23,muted);
        }
        if(m.details) {
            rectangle(r,{0,0,ui_width,ui_height},{4,5,15,210});
            rounded(r,{170,210,1260,650},30,{31,30,48,252});
            card(m.selected(),{205,250,330,400},false);
            text(585,265,m.selected().title,32,white,true,1380);
            text(585,325,m.selected().system,26,muted);
            text(585,395,m.selected().favorite?"Favorite: Yes":"Favorite: No",26);
            text(585,445,"Playtime: "+std::to_string(m.selected().play_seconds/60)+" min",26);
            text(585,495,m.selected().last_played?"Last played: recently":"Not played yet",26,muted);
            text(585,575,"A / Enter   Play",27,cyan,true);
            text(585,625,"Y / F   Toggle favorite",24,muted);
            if(m.selected().system=="GameCube") {
                const std::array<const char*,3> presets{"Native 1x","Compatibility 1x","Performance 1x"};
                text(585,675,"L / R   "+std::string(presets[std::min(m.selected().performance,2U)]),23,muted);
                text(230,725,"Performance changes can affect compatibility. Full speed is not guaranteed.",21,muted);
            } else
            text(230,725,"Save: F5    Load: F8    Quick settings: Guide    Exit: Esc",23,muted);
            text(230,790,"B / Esc   Back",24);
        }
        if(controls) {
            const auto& view=*controls;
            rectangle(r,{0,0,ui_width,ui_height},{4,5,15,220});
            rounded(r,{135,135,1330,805},30,{29,29,46,252});
            text(185,175,view.title,37,white,true);
            if(view.editing) {
                rounded(r,{185,252,1230,74},15,{12,15,29,255});
                const auto input=view.secret?std::string(view.input.size(),'*'):view.input;
                text(211,273,input+"_",29,white,false,1385);
                for(std::size_t i=0;i<view.keyboard.size();++i) {
                    const Rect box{190+float(i%13)*92,355+float(i/13)*55,83,48};
                    const bool selected=i==view.keyboard_index;
                    rounded(r,box,12,selected?SDL_Color{118,72,214,255}:SDL_Color{49,50,71,255});
                    const float size=view.keyboard[i].size()>2?19:28;
                    const float tw=width(view.keyboard[i],size);
                    text(box.x+(box.w-tw)/2,box.y+12,view.keyboard[i],size);
                }
            } else {
                const std::size_t begin=view.selected>=7?view.selected-6:0;
                for(std::size_t i=begin;i<view.rows.size() && i<begin+7;++i) {
                    const Rect box{185,250+float(i-begin)*68,1230,57};
                    const bool selected=i==view.selected;
                    rounded(r,box,12,selected?SDL_Color{99,57,191,255}:SDL_Color{39,40,60,255});
                    text(box.x+21,box.y+15,view.rows[i],25,white,selected,box.x+box.w-20);
                }
            }
            std::size_t at=0;int row=0;
            while(at<view.status.size() && row<3) {
                auto end=std::min(at+103,view.status.size());
                if(end<view.status.size()) {auto space=view.status.rfind(' ',end);if(space>at && space!=std::string::npos)end=space;}
                text(190,775+row*31.0F,view.status.substr(at,end-at),21,muted,false,1400);
                at=end+(end<view.status.size() && view.status[end]==' '?1:0);++row;
            }
            text(190,894,view.critical?"Writing disk - keep power connected":view.busy?"Working...   B Back":"A Select     B Back",22,muted);
        }
    }
};
HomeRenderer::HomeRenderer(SDL_Renderer* r):impl(std::make_unique<Impl>(r)) {}
HomeRenderer::~HomeRenderer()=default;
void HomeRenderer::draw(const Model& m,const Status& s,const ControlView* controls) { impl->draw(m,s,controls); }
bool HomeRenderer::update() {
    if(impl->pending.valid() && impl->pending.wait_for(std::chrono::seconds(0))==std::future_status::ready) {
        auto* texture=upload(impl->r,impl->pending.get());
        impl->covers[impl->loading]=texture;
        impl->loaded_order.push_back(impl->loading);
        while(impl->loaded_order.size()>24) {
            const auto oldest=impl->loaded_order.front();impl->loaded_order.pop_front();
            if(auto it=impl->covers.find(oldest);it!=impl->covers.end()) { SDL_DestroyTexture(it->second);impl->covers.erase(it); }
        }
        if(texture) { std::printf("SURFACE_ARTWORK_LOADED %s\n",impl->loading.c_str());std::fflush(stdout); }
        return true;
    }
    return false;
}
}
