// Native SDL3 libretro frontend. No ROM, firmware or game artwork is embedded.
#include "surface/libretro.h"
#include "surface/persistent_memory.hpp"
#include <SDL3/SDL.h>
#include <GLES2/gl2.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdlib>
#include <cstdarg>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

namespace fs = std::filesystem;
namespace {
volatile std::sig_atomic_t stop_requested=0;
void request_stop(int) { stop_requested=1; }
struct Options {
    std::string core, rom, system_dir="/data/bios", save_dir="/data/saves", screenshot;
    std::map<std::string,std::string> variables;
    unsigned long long frames=0;
    bool probe=false, windowed=false, audio=true, test_input=false, state_roundtrip_test=false;
};
Options arguments(int argc,char** argv) {
    Options out;
    for(int i=1;i<argc;++i) {
        const std::string arg=argv[i];
        auto value=[&]() -> std::string { if(++i>=argc) throw std::runtime_error("Missing value for "+arg); return argv[i]; };
        if(arg=="--help") {
            std::puts("surface-emulator --core CORE.so --rom GAME [--system-dir /data/bios] [--save-dir /data/saves]\n"
                      "                 [--option key=value] [--windowed] [--no-audio] [--frames N] [--screenshot FILE.bmp]\n"
                      "                 [--test-input] [--state-roundtrip-test]\n"
                      "surface-emulator --probe-core --core CORE.so\n"
                      "Keyboard: arrows, Z=B, X=A, A=Y, S=X, Q=L, W=R, 1=L2, 2=R2, Enter=Start, Backspace=Select.\n"
                      "Gamepads: standard RetroPad layout. F5 saves state; F8 loads state. Back+Y saves; Back+X loads.\n"
                      "Esc or F10 saves SRAM and returns to HOME. Guide opens the HOME quick controls.");
            std::exit(0);
        } else if(arg=="--core") out.core=value();
        else if(arg=="--rom") out.rom=value();
        else if(arg=="--system-dir") out.system_dir=value();
        else if(arg=="--save-dir") out.save_dir=value();
        else if(arg=="--screenshot") out.screenshot=value();
        else if(arg=="--frames") { const auto raw=value(); std::size_t consumed=0; out.frames=std::stoull(raw,&consumed); if(consumed!=raw.size() || !out.frames) throw std::runtime_error("--frames must be a positive integer"); }
        else if(arg=="--option") { const auto option=value(); const auto eq=option.find('='); if(eq==0 || eq==std::string::npos) throw std::runtime_error("--option requires key=value"); out.variables[option.substr(0,eq)]=option.substr(eq+1); }
        else if(arg=="--state-roundtrip-test") out.state_roundtrip_test=true;
        else if(arg=="--test-input") out.test_input=true;
        else if(arg=="--probe-core") out.probe=true;
        else if(arg=="--windowed") out.windowed=true;
        else if(arg=="--no-audio") out.audio=false;
        else throw std::runtime_error("Unknown argument: "+arg);
    }
    if(out.core.empty() || (!out.probe && out.rom.empty())) throw std::runtime_error("Use --core CORE.so --rom GAME; see --help");
    return out;
}
void log_core(enum retro_log_level, const char* format,...) {
    std::va_list args; va_start(args,format); std::fputs("CORE: ",stderr); std::vfprintf(stderr,format,args); va_end(args);
}
struct Core {
    void* library=nullptr;
#define CORE_FUNCTION(name) decltype(&retro_##name) name=nullptr
    CORE_FUNCTION(api_version); CORE_FUNCTION(init); CORE_FUNCTION(deinit);
    CORE_FUNCTION(get_system_info); CORE_FUNCTION(get_system_av_info);
    CORE_FUNCTION(set_environment); CORE_FUNCTION(set_video_refresh); CORE_FUNCTION(set_audio_sample);
    CORE_FUNCTION(set_audio_sample_batch); CORE_FUNCTION(set_input_poll); CORE_FUNCTION(set_input_state);
    CORE_FUNCTION(set_controller_port_device); CORE_FUNCTION(load_game); CORE_FUNCTION(unload_game);
    CORE_FUNCTION(run); CORE_FUNCTION(get_memory_data); CORE_FUNCTION(get_memory_size);
    CORE_FUNCTION(serialize_size); CORE_FUNCTION(serialize); CORE_FUNCTION(unserialize);
#undef CORE_FUNCTION
    explicit Core(const std::string& path) {
        library=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL);
        if(!library) throw std::runtime_error(std::string("Cannot load core: ")+dlerror());
        try {
#define LOAD(name) name=reinterpret_cast<decltype(name)>(dlsym(library,"retro_" #name)); if(!name) throw std::runtime_error("Core missing retro_" #name)
            LOAD(api_version); LOAD(init); LOAD(deinit); LOAD(get_system_info); LOAD(get_system_av_info);
            LOAD(set_environment); LOAD(set_video_refresh); LOAD(set_audio_sample); LOAD(set_audio_sample_batch);
            LOAD(set_input_poll); LOAD(set_input_state); LOAD(set_controller_port_device); LOAD(load_game);
            LOAD(unload_game); LOAD(run); LOAD(get_memory_data); LOAD(get_memory_size);
            LOAD(serialize_size); LOAD(serialize); LOAD(unserialize);
#undef LOAD
            if(api_version()!=RETRO_API_VERSION) throw std::runtime_error("Unsupported libretro ABI");
        } catch(...) { dlclose(library); library=nullptr; throw; }
    }
    ~Core() { if(library) dlclose(library); }
    Core(const Core&)=delete; Core& operator=(const Core&)=delete;
};
// Every GL entry point is resolved from SDL's selected context, allowing GLES2,
// GLES3 and desktop GL cores without forcing a desktop OpenGL dependency.
struct GL {
#define GL_ENTRY(type,name) type name=nullptr
    GL_ENTRY(PFNGLGENFRAMEBUFFERSPROC,GenFramebuffers); GL_ENTRY(PFNGLBINDFRAMEBUFFERPROC,BindFramebuffer);
    GL_ENTRY(PFNGLFRAMEBUFFERTEXTURE2DPROC,FramebufferTexture2D); GL_ENTRY(PFNGLCHECKFRAMEBUFFERSTATUSPROC,CheckFramebufferStatus);
    GL_ENTRY(PFNGLGENTEXTURESPROC,GenTextures); GL_ENTRY(PFNGLBINDTEXTUREPROC,BindTexture);
    GL_ENTRY(PFNGLTEXIMAGE2DPROC,TexImage2D); GL_ENTRY(PFNGLTEXPARAMETERIPROC,TexParameteri);
    GL_ENTRY(PFNGLGENRENDERBUFFERSPROC,GenRenderbuffers); GL_ENTRY(PFNGLBINDRENDERBUFFERPROC,BindRenderbuffer);
    GL_ENTRY(PFNGLRENDERBUFFERSTORAGEPROC,RenderbufferStorage); GL_ENTRY(PFNGLFRAMEBUFFERRENDERBUFFERPROC,FramebufferRenderbuffer);
    GL_ENTRY(PFNGLCREATESHADERPROC,CreateShader); GL_ENTRY(PFNGLSHADERSOURCEPROC,ShaderSource);
    GL_ENTRY(PFNGLCOMPILESHADERPROC,CompileShader); GL_ENTRY(PFNGLGETSHADERIVPROC,GetShaderiv);
    GL_ENTRY(PFNGLGETSHADERINFOLOGPROC,GetShaderInfoLog); GL_ENTRY(PFNGLCREATEPROGRAMPROC,CreateProgram);
    GL_ENTRY(PFNGLATTACHSHADERPROC,AttachShader); GL_ENTRY(PFNGLBINDATTRIBLOCATIONPROC,BindAttribLocation);
    GL_ENTRY(PFNGLLINKPROGRAMPROC,LinkProgram); GL_ENTRY(PFNGLGETPROGRAMIVPROC,GetProgramiv);
    GL_ENTRY(PFNGLUSEPROGRAMPROC,UseProgram); GL_ENTRY(PFNGLDELETESHADERPROC,DeleteShader);
    GL_ENTRY(PFNGLGENBUFFERSPROC,GenBuffers); GL_ENTRY(PFNGLBINDBUFFERPROC,BindBuffer);
    GL_ENTRY(PFNGLBUFFERDATAPROC,BufferData); GL_ENTRY(PFNGLVERTEXATTRIBPOINTERPROC,VertexAttribPointer);
    GL_ENTRY(PFNGLENABLEVERTEXATTRIBARRAYPROC,EnableVertexAttribArray); GL_ENTRY(PFNGLACTIVETEXTUREPROC,ActiveTexture);
    GL_ENTRY(PFNGLGETUNIFORMLOCATIONPROC,GetUniformLocation); GL_ENTRY(PFNGLUNIFORM1IPROC,Uniform1i);
    GL_ENTRY(PFNGLVIEWPORTPROC,Viewport); GL_ENTRY(PFNGLCLEARCOLORPROC,ClearColor);
    GL_ENTRY(PFNGLCLEARPROC,Clear); GL_ENTRY(PFNGLDISABLEPROC,Disable);
    GL_ENTRY(PFNGLDRAWARRAYSPROC,DrawArrays); GL_ENTRY(PFNGLREADPIXELSPROC,ReadPixels);
    GL_ENTRY(PFNGLGETERRORPROC,GetError); GL_ENTRY(PFNGLGETINTEGERVPROC,GetIntegerv);
    GL_ENTRY(PFNGLGETBOOLEANVPROC,GetBooleanv); GL_ENTRY(PFNGLGETFLOATVPROC,GetFloatv);
    GL_ENTRY(PFNGLISENABLEDPROC,IsEnabled); GL_ENTRY(PFNGLENABLEPROC,Enable);
    GL_ENTRY(PFNGLGETVERTEXATTRIBIVPROC,GetVertexAttribiv); GL_ENTRY(PFNGLGETVERTEXATTRIBPOINTERVPROC,GetVertexAttribPointerv);
    GL_ENTRY(PFNGLDISABLEVERTEXATTRIBARRAYPROC,DisableVertexAttribArray);
    GL_ENTRY(PFNGLSCISSORPROC,Scissor); GL_ENTRY(PFNGLCOLORMASKPROC,ColorMask);
    using GenVertexArraysFn=void (*)(GLsizei,GLuint*);
    using BindVertexArrayFn=void (*)(GLuint);
    GenVertexArraysFn GenVertexArrays=nullptr; BindVertexArrayFn BindVertexArray=nullptr;
    void load() {
#define RESOLVE(name) name=reinterpret_cast<decltype(name)>(SDL_GL_GetProcAddress("gl" #name)); if(!name) throw std::runtime_error("Missing GL entry point gl" #name)
        RESOLVE(GenFramebuffers); RESOLVE(BindFramebuffer); RESOLVE(FramebufferTexture2D); RESOLVE(CheckFramebufferStatus);
        RESOLVE(GenTextures); RESOLVE(BindTexture); RESOLVE(TexImage2D); RESOLVE(TexParameteri);
        RESOLVE(GenRenderbuffers); RESOLVE(BindRenderbuffer); RESOLVE(RenderbufferStorage); RESOLVE(FramebufferRenderbuffer);
        RESOLVE(CreateShader); RESOLVE(ShaderSource); RESOLVE(CompileShader); RESOLVE(GetShaderiv); RESOLVE(GetShaderInfoLog);
        RESOLVE(CreateProgram); RESOLVE(AttachShader); RESOLVE(BindAttribLocation); RESOLVE(LinkProgram); RESOLVE(GetProgramiv);
        RESOLVE(UseProgram); RESOLVE(DeleteShader); RESOLVE(GenBuffers); RESOLVE(BindBuffer); RESOLVE(BufferData);
        RESOLVE(VertexAttribPointer); RESOLVE(EnableVertexAttribArray); RESOLVE(ActiveTexture); RESOLVE(GetUniformLocation);
        RESOLVE(Uniform1i); RESOLVE(Viewport); RESOLVE(ClearColor); RESOLVE(Clear); RESOLVE(Disable); RESOLVE(DrawArrays);
        RESOLVE(ReadPixels); RESOLVE(GetError); RESOLVE(GetIntegerv); RESOLVE(Scissor); RESOLVE(ColorMask);
        RESOLVE(GetBooleanv); RESOLVE(GetFloatv); RESOLVE(IsEnabled); RESOLVE(Enable);
        RESOLVE(GetVertexAttribiv); RESOLVE(GetVertexAttribPointerv); RESOLVE(DisableVertexAttribArray);
#undef RESOLVE
        GenVertexArrays=reinterpret_cast<GenVertexArraysFn>(SDL_GL_GetProcAddress("glGenVertexArrays"));
        BindVertexArray=reinterpret_cast<BindVertexArrayFn>(SDL_GL_GetProcAddress("glBindVertexArray"));
    }
#undef GL_ENTRY
};
struct Frontend {
    Options options;
    Core core;
    retro_system_info info{};
    retro_system_av_info av{};
    retro_pixel_format pixel_format=RETRO_PIXEL_FORMAT_0RGB1555;
    retro_hw_render_callback hw{};
    SDL_Window* window=nullptr;
    SDL_Renderer* renderer=nullptr;
    SDL_Texture* texture=nullptr;
    SDL_GLContext context=nullptr;
    SDL_AudioStream* audio=nullptr;
    std::array<SDL_Gamepad*,4> pads{};
    std::array<bool,4> save_combo{},load_combo{};
    bool save_requested=false,load_requested=false;
    GL gl;
    GLuint framebuffer=0, frame_texture=0, depth_buffer=0, program=0, vertex_buffer=0, vertex_array=0;
    unsigned texture_width=0,texture_height=0, frame_width=0,frame_height=0;
    unsigned gl_width=0,gl_height=0,rotation=0;
    bool gl_ready=false, gl_modern=false, context_ready=false, initialized=false, loaded=false, quit=false, failed=false;
    bool have_frame=false, software_seen=false;
    std::string failure, save_directory, save_file, core_directory;
    std::array<surface::PersistentMemory,2> battery_saves;
    std::map<std::string,std::string> variables;
    std::vector<char> content;
    std::vector<std::uint16_t> converted;
    std::array<std::int16_t,2048> single_audio{};
    std::size_t single_audio_count=0;
    unsigned long long rendered_frames=0;
    std::chrono::steady_clock::time_point next_frame;
    static Frontend* active;
    explicit Frontend(Options opts):options(std::move(opts)),core(options.core),variables(options.variables) {
        active=this; core.get_system_info(&info);
        core_directory=fs::absolute(fs::path(options.core).parent_path()).string();
    }
    ~Frontend() {
        if(loaded) { save_ram(); if(context_ready && hw.context_destroy) hw.context_destroy(); core.unload_game(); }
        if(initialized) core.deinit();
        if(audio) SDL_DestroyAudioStream(audio);
        for(auto* pad:pads) if(pad) SDL_CloseGamepad(pad);
        if(texture) SDL_DestroyTexture(texture);
        if(renderer) SDL_DestroyRenderer(renderer);
        if(context) SDL_GL_DestroyContext(context);
        if(window) SDL_DestroyWindow(window);
        SDL_Quit(); active=nullptr;
    }
    void error(const std::string& message) { if(!failed) { failure=message; std::fprintf(stderr,"EMULATOR_ERROR %s\n",message.c_str()); } failed=true; quit=true; }
    static retro_proc_address_t proc_address(const char* symbol) { return reinterpret_cast<retro_proc_address_t>(SDL_GL_GetProcAddress(symbol)); }
    static uintptr_t current_framebuffer() { return active ? active->framebuffer : 0; }
    void default_option(const char* key,const char* value) { if(key && value && !variables.contains(key)) variables[key]=value; }
    template<class Definition> void option_definitions(const Definition* defs) {
        if(defs) for(const auto* d=defs;d->key;++d) default_option(d->key,d->default_value ? d->default_value : d->values[0].value);
    }
    bool environment(unsigned cmd,void* data) {
        switch(cmd) {
        case RETRO_ENVIRONMENT_SET_ROTATION: rotation=*static_cast<unsigned*>(data)%4; return true;
        case RETRO_ENVIRONMENT_GET_OVERSCAN: *static_cast<bool*>(data)=false; return true;
        case RETRO_ENVIRONMENT_GET_CAN_DUPE: *static_cast<bool*>(data)=true; return true;
        case RETRO_ENVIRONMENT_SHUTDOWN: quit=true; return true;
        case RETRO_ENVIRONMENT_SET_MESSAGE: {
            const auto* msg=static_cast<const retro_message*>(data); if(msg && msg->msg) std::fprintf(stderr,"CORE_MESSAGE %s\n",msg->msg); return true;
        }
        case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY: *static_cast<const char**>(data)=options.system_dir.c_str(); return true;
        case RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY: *static_cast<const char**>(data)=save_directory.c_str(); return true;
        case RETRO_ENVIRONMENT_GET_LIBRETRO_PATH: *static_cast<const char**>(data)=options.core.c_str(); return true;
        case RETRO_ENVIRONMENT_GET_CORE_ASSETS_DIRECTORY: *static_cast<const char**>(data)=options.system_dir.c_str(); return true;
        case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT: {
            const auto format=*static_cast<retro_pixel_format*>(data);
            if(format!=RETRO_PIXEL_FORMAT_0RGB1555 && format!=RETRO_PIXEL_FORMAT_RGB565 && format!=RETRO_PIXEL_FORMAT_XRGB8888) return false;
            pixel_format=format; if(texture) { SDL_DestroyTexture(texture); texture=nullptr; } return true;
        }
        case RETRO_ENVIRONMENT_SET_HW_RENDER: return create_hardware(static_cast<retro_hw_render_callback*>(data));
        case RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME: return true; // CLI still requires selected content.
        case RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS: case RETRO_ENVIRONMENT_SET_CONTROLLER_INFO: return true;
        case RETRO_ENVIRONMENT_GET_INPUT_DEVICE_CAPABILITIES: *static_cast<uint64_t*>(data)=(1ULL<<RETRO_DEVICE_JOYPAD)|(1ULL<<RETRO_DEVICE_ANALOG)|(1ULL<<RETRO_DEVICE_POINTER); return true;
        case RETRO_ENVIRONMENT_GET_INPUT_BITMASKS: return true;
        case RETRO_ENVIRONMENT_GET_LOG_INTERFACE: static_cast<retro_log_callback*>(data)->log=log_core; return true;
        case RETRO_ENVIRONMENT_GET_LANGUAGE: *static_cast<unsigned*>(data)=RETRO_LANGUAGE_ENGLISH; return true;
        case RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION: *static_cast<unsigned*>(data)=2; return true;
        case RETRO_ENVIRONMENT_SET_VARIABLES: {
            const auto* vars=static_cast<const retro_variable*>(data);
            for(const auto* v=vars;v && v->key;++v) if(v->value) {
                std::string choices=v->value; const auto delimiter=choices.find(';');
                if(delimiter!=std::string::npos) { choices=choices.substr(delimiter+1); const auto start=choices.find_first_not_of(' '); if(start!=std::string::npos) choices=choices.substr(start); default_option(v->key,choices.substr(0,choices.find('|')).c_str()); }
            }
            return true;
        }
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS: option_definitions(static_cast<const retro_core_option_definition*>(data)); return true;
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_INTL: { const auto* intl=static_cast<const retro_core_options_intl*>(data); option_definitions(intl->us); return true; }
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2: option_definitions(static_cast<const retro_core_options_v2*>(data)->definitions); return false; // Options accepted; categories are not displayed.
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2_INTL: { const auto* intl=static_cast<const retro_core_options_v2_intl*>(data); if(intl->us) option_definitions(intl->us->definitions); return false; }
        case RETRO_ENVIRONMENT_GET_VARIABLE: {
            auto* v=static_cast<retro_variable*>(data); const auto it=variables.find(v->key ? v->key : ""); v->value=it==variables.end() ? nullptr : it->second.c_str(); return v->value!=nullptr;
        }
        case RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE: *static_cast<bool*>(data)=false; return true;
        case RETRO_ENVIRONMENT_SET_GEOMETRY: av.geometry=*static_cast<retro_game_geometry*>(data); return valid_geometry();
        case RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO: av=*static_cast<retro_system_av_info*>(data); if(!valid_geometry()) return false; setup_audio(); return true;
        case RETRO_ENVIRONMENT_GET_AUDIO_VIDEO_ENABLE: *static_cast<int*>(data)=3; return true;
        case RETRO_ENVIRONMENT_GET_FASTFORWARDING: *static_cast<bool*>(data)=false; return true;
        case RETRO_ENVIRONMENT_GET_TARGET_REFRESH_RATE: *static_cast<float*>(data)=60.0F; return true;
        case RETRO_ENVIRONMENT_GET_USERNAME: *static_cast<const char**>(data)="403 OS"; return true;
        case RETRO_ENVIRONMENT_GET_PREFERRED_HW_RENDER: *static_cast<retro_hw_context_type*>(data)=RETRO_HW_CONTEXT_OPENGLES2; return true;
        default: return false; // Do not advertise VFS, Vulkan, rumble or async audio interfaces we do not implement.
        }
    }
    static bool environment_callback(unsigned cmd,void* data) {
        try { return active && active->environment(cmd,data); }
        catch(const std::exception& e) { if(active) active->error(e.what()); return false; }
    }
    bool valid_geometry() {
        return av.geometry.base_width>0 && av.geometry.base_height>0 && av.geometry.max_width<=8192 && av.geometry.max_height<=8192;
    }
    // A core may cache GL state and omit redundant binds. Restore every state
    // touched by our presentation pass so that its cache remains truthful.
    struct GraphicsState {
        Frontend& f;
        GLint framebuffer=0,read_framebuffer=0,program=0,array_buffer=0,renderbuffer=0,vao=0,active_texture=0,texture0=0;
        std::array<GLint,4> viewport{};
        std::array<GLboolean,4> color_mask{};
        std::array<GLfloat,4> clear_color{};
        std::array<GLenum,6> capabilities{GL_DEPTH_TEST,GL_STENCIL_TEST,GL_BLEND,GL_CULL_FACE,GL_SCISSOR_TEST,0x8C89 /* GL_RASTERIZER_DISCARD */};
        std::array<GLboolean,6> enabled{};
        struct Attribute { GLint enabled=0,size=0,stride=0,type=0,normalized=0,buffer=0; void* pointer=nullptr; };
        std::array<Attribute,2> attributes{};
        explicit GraphicsState(Frontend& owner):f(owner) {
            auto& g=f.gl;
            g.GetIntegerv(GL_FRAMEBUFFER_BINDING,&framebuffer);
            if(f.gl_modern) g.GetIntegerv(0x8CAA /* GL_READ_FRAMEBUFFER_BINDING */,&read_framebuffer);
            g.GetIntegerv(GL_CURRENT_PROGRAM,&program); g.GetIntegerv(GL_ARRAY_BUFFER_BINDING,&array_buffer);
            g.GetIntegerv(GL_RENDERBUFFER_BINDING,&renderbuffer);
            g.GetIntegerv(GL_VIEWPORT,viewport.data()); g.GetBooleanv(GL_COLOR_WRITEMASK,color_mask.data());
            g.GetFloatv(GL_COLOR_CLEAR_VALUE,clear_color.data());
            g.GetIntegerv(GL_ACTIVE_TEXTURE,&active_texture); g.ActiveTexture(GL_TEXTURE0);
            g.GetIntegerv(GL_TEXTURE_BINDING_2D,&texture0); g.ActiveTexture(static_cast<GLenum>(active_texture));
            for(std::size_t i=0;i<(f.gl_modern ? 6u : 5u);++i) enabled[i]=g.IsEnabled(capabilities[i]);
            if(f.vertex_array) g.GetIntegerv(0x85B5 /* GL_VERTEX_ARRAY_BINDING */,&vao);
            else for(GLuint i=0;i<attributes.size();++i) {
                auto& a=attributes[i];
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_ENABLED,&a.enabled);
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_SIZE,&a.size);
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_STRIDE,&a.stride);
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_TYPE,&a.type);
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_NORMALIZED,&a.normalized);
                g.GetVertexAttribiv(i,GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,&a.buffer);
                g.GetVertexAttribPointerv(i,GL_VERTEX_ATTRIB_ARRAY_POINTER,&a.pointer);
            }
        }
        ~GraphicsState() {
            auto& g=f.gl;
            if(f.gl_modern) {
                g.BindFramebuffer(0x8CA8 /* GL_READ_FRAMEBUFFER */,static_cast<GLuint>(read_framebuffer));
                g.BindFramebuffer(0x8CA9 /* GL_DRAW_FRAMEBUFFER */,static_cast<GLuint>(framebuffer));
            } else g.BindFramebuffer(GL_FRAMEBUFFER,static_cast<GLuint>(framebuffer));
            g.UseProgram(static_cast<GLuint>(program));
            g.ActiveTexture(GL_TEXTURE0); g.BindTexture(GL_TEXTURE_2D,static_cast<GLuint>(texture0));
            g.ActiveTexture(static_cast<GLenum>(active_texture));
            if(f.vertex_array) g.BindVertexArray(static_cast<GLuint>(vao));
            else for(GLuint i=0;i<attributes.size();++i) {
                const auto& a=attributes[i]; g.BindBuffer(GL_ARRAY_BUFFER,static_cast<GLuint>(a.buffer));
                g.VertexAttribPointer(i,a.size,static_cast<GLenum>(a.type),static_cast<GLboolean>(a.normalized),a.stride,a.pointer);
                if(a.enabled) g.EnableVertexAttribArray(i); else g.DisableVertexAttribArray(i);
            }
            g.BindBuffer(GL_ARRAY_BUFFER,static_cast<GLuint>(array_buffer));
            g.BindRenderbuffer(GL_RENDERBUFFER,static_cast<GLuint>(renderbuffer));
            g.Viewport(viewport[0],viewport[1],viewport[2],viewport[3]);
            g.ColorMask(color_mask[0],color_mask[1],color_mask[2],color_mask[3]);
            g.ClearColor(clear_color[0],clear_color[1],clear_color[2],clear_color[3]);
            for(std::size_t i=0;i<(f.gl_modern ? 6u : 5u);++i) {
                if(enabled[i]) g.Enable(capabilities[i]); else g.Disable(capabilities[i]);
            }
        }
    };
    bool create_hardware(retro_hw_render_callback* requested) {
        if(!requested || context || renderer) return false;
        int profile=SDL_GL_CONTEXT_PROFILE_ES,major=2,minor=0;
        switch(requested->context_type) {
        case RETRO_HW_CONTEXT_OPENGLES2: break;
        case RETRO_HW_CONTEXT_OPENGLES3: major=3; break;
        case RETRO_HW_CONTEXT_OPENGLES_VERSION: major=static_cast<int>(requested->version_major); minor=static_cast<int>(requested->version_minor); break;
        case RETRO_HW_CONTEXT_OPENGL: profile=SDL_GL_CONTEXT_PROFILE_COMPATIBILITY; major=2; minor=1; break;
        case RETRO_HW_CONTEXT_OPENGL_CORE: profile=SDL_GL_CONTEXT_PROFILE_CORE; major=static_cast<int>(requested->version_major); minor=static_cast<int>(requested->version_minor); break;
        default: std::fprintf(stderr,"Unsupported core hardware API %d\n",requested->context_type); return false;
        }
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,profile);
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,major);
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,minor);
        SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER,1);
        SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE,24);
        if(window) SDL_DestroyWindow(window);
        window=SDL_CreateWindow("403 OS / Emulator",1200,800,SDL_WINDOW_OPENGL|SDL_WINDOW_RESIZABLE|(options.windowed ? 0 : SDL_WINDOW_FULLSCREEN));
        context=window ? SDL_GL_CreateContext(window) : nullptr;
        if(!context) { std::fprintf(stderr,"Core GL context unavailable: %s\n",SDL_GetError()); if(window) { SDL_DestroyWindow(window); window=nullptr; } return false; }
        SDL_GL_MakeCurrent(window,context); SDL_GL_SetSwapInterval(0);
        hw=*requested; requested->get_current_framebuffer=current_framebuffer; requested->get_proc_address=proc_address;
        hw.get_current_framebuffer=current_framebuffer; hw.get_proc_address=proc_address;
        gl.load();
        const bool modern=profile==SDL_GL_CONTEXT_PROFILE_CORE;
        gl_modern=modern || (profile==SDL_GL_CONTEXT_PROFILE_ES && major>=3) || (profile==SDL_GL_CONTEXT_PROFILE_COMPATIBILITY && major>=3);
        const char* vertex=modern ? "#version 150\nin vec2 p; in vec2 t; out vec2 uv; void main(){uv=t;gl_Position=vec4(p,0.0,1.0);}" : "attribute vec2 p; attribute vec2 t; varying vec2 uv; void main(){uv=t;gl_Position=vec4(p,0.0,1.0);}";
        const char* fragment=modern ? "#version 150\nin vec2 uv; out vec4 color; uniform sampler2D image; void main(){color=texture(image,uv);}" : (profile==SDL_GL_CONTEXT_PROFILE_ES ? "precision mediump float; varying vec2 uv; uniform sampler2D image; void main(){gl_FragColor=texture2D(image,uv);}" : "varying vec2 uv; uniform sampler2D image; void main(){gl_FragColor=texture2D(image,uv);}");
        auto shader=[&](GLenum type,const char* source) {
            const GLuint s=gl.CreateShader(type); gl.ShaderSource(s,1,&source,nullptr); gl.CompileShader(s);
            GLint ok=0; gl.GetShaderiv(s,GL_COMPILE_STATUS,&ok); if(!ok) { char message[1024]{}; gl.GetShaderInfoLog(s,sizeof(message),nullptr,message); throw std::runtime_error(std::string("Presentation shader: ")+message); } return s;
        };
        const GLuint vs=shader(GL_VERTEX_SHADER,vertex), ps=shader(GL_FRAGMENT_SHADER,fragment);
        program=gl.CreateProgram(); gl.AttachShader(program,vs); gl.AttachShader(program,ps); gl.BindAttribLocation(program,0,"p"); gl.BindAttribLocation(program,1,"t"); gl.LinkProgram(program); gl.DeleteShader(vs); gl.DeleteShader(ps);
        GLint ok=0; gl.GetProgramiv(program,GL_LINK_STATUS,&ok); if(!ok) throw std::runtime_error("Presentation program linking failed");
        gl.GenBuffers(1,&vertex_buffer);
        if(gl_modern && gl.GenVertexArrays && gl.BindVertexArray) { gl.GenVertexArrays(1,&vertex_array); gl.BindVertexArray(vertex_array); }
        gl.GenFramebuffers(1,&framebuffer); gl.GenTextures(1,&frame_texture);
        gl.GenRenderbuffers(1,&depth_buffer);
        gl_ready=true;
        std::printf("EMULATOR_HW_CONTEXT api=%d version=%d.%d\n",hw.context_type,major,minor); std::fflush(stdout);
        return true;
    }
    void prepare_framebuffer() {
        if(!gl_ready) return;
        const unsigned width=std::max(av.geometry.max_width,av.geometry.base_width),height=std::max(av.geometry.max_height,av.geometry.base_height);
        if(width==gl_width && height==gl_height) return;
        const GraphicsState restore(*this);
        if(!width || !height || width>8192 || height>8192) throw std::runtime_error("Invalid hardware framebuffer geometry");
        gl.ActiveTexture(GL_TEXTURE0); gl.BindTexture(GL_TEXTURE_2D,frame_texture); gl.TexImage2D(GL_TEXTURE_2D,0,GL_RGBA,static_cast<GLsizei>(width),static_cast<GLsizei>(height),0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR); gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE); gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
        gl.BindFramebuffer(GL_FRAMEBUFFER,framebuffer); gl.FramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,frame_texture,0);
        if(hw.depth) {
            gl.BindRenderbuffer(GL_RENDERBUFFER,depth_buffer); gl.RenderbufferStorage(GL_RENDERBUFFER,hw.stencil ? 0x88F0 /* GL_DEPTH24_STENCIL8 */ : GL_DEPTH_COMPONENT16,static_cast<GLsizei>(width),static_cast<GLsizei>(height));
            gl.FramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,depth_buffer);
            if(hw.stencil) gl.FramebufferRenderbuffer(GL_FRAMEBUFFER,GL_STENCIL_ATTACHMENT,GL_RENDERBUFFER,depth_buffer);
        }
        if(gl.CheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("Core framebuffer is incomplete");
        gl_width=width; gl_height=height;
    }
    SDL_FRect destination(unsigned width,unsigned height) const {
        int ww=1200,wh=800; SDL_GetWindowSizeInPixels(window,&ww,&wh);
        double aspect=av.geometry.aspect_ratio>0 ? av.geometry.aspect_ratio : static_cast<double>(width)/height;
        if(rotation%2) aspect=1.0/aspect;
        const float w=static_cast<float>(std::min(static_cast<double>(ww),wh*aspect));
        const float h=static_cast<float>(w/aspect);
        return {(ww-w)/2,(wh-h)/2,w,h};
    }
    void present_gl(unsigned width,unsigned height) {
        if(width>gl_width || height>gl_height) throw std::runtime_error("Core frame exceeds declared maximum geometry");
        const GraphicsState restore(*this);
        const auto box=destination(width,height); int ww=0,wh=0; SDL_GetWindowSizeInPixels(window,&ww,&wh);
        if(gl_modern) gl.Disable(0x8C89 /* GL_RASTERIZER_DISCARD */);
        gl.BindFramebuffer(GL_FRAMEBUFFER,0); gl.Disable(GL_SCISSOR_TEST); gl.Disable(GL_DEPTH_TEST); gl.Disable(GL_STENCIL_TEST); gl.Disable(GL_BLEND); gl.Disable(GL_CULL_FACE); gl.ColorMask(GL_TRUE,GL_TRUE,GL_TRUE,GL_TRUE);
        gl.Viewport(0,0,ww,wh); gl.ClearColor(0,0,0,1); gl.Clear(GL_COLOR_BUFFER_BIT);
        gl.Viewport(static_cast<GLint>(box.x),static_cast<GLint>(wh-box.y-box.h),static_cast<GLsizei>(box.w),static_cast<GLsizei>(box.h));
        gl.UseProgram(program); gl.ActiveTexture(GL_TEXTURE0); gl.BindTexture(GL_TEXTURE_2D,frame_texture);
        gl.Uniform1i(gl.GetUniformLocation(program,"image"),0);
        const float right=static_cast<float>(width)/gl_width, top=static_cast<float>(height)/gl_height;
        std::array<std::array<float,2>,4> uv{{{0,0},{right,0},{0,top},{right,top}}};
        if(!hw.bottom_left_origin) for(auto& v:uv) v[1]=top-v[1];
        for(unsigned turns=0;turns<rotation;++turns) { const auto old=uv; uv={old[1],old[3],old[0],old[2]}; }
        const float vertices[]{-1,-1,uv[0][0],uv[0][1],1,-1,uv[1][0],uv[1][1],-1,1,uv[2][0],uv[2][1],1,1,uv[3][0],uv[3][1]};
        if(vertex_array && gl.BindVertexArray) gl.BindVertexArray(vertex_array);
        gl.BindBuffer(GL_ARRAY_BUFFER,vertex_buffer); gl.BufferData(GL_ARRAY_BUFFER,sizeof(vertices),vertices,GL_STREAM_DRAW);
        gl.EnableVertexAttribArray(0); gl.EnableVertexAttribArray(1);
        gl.VertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,4*sizeof(float),nullptr);
        gl.VertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,4*sizeof(float),reinterpret_cast<void*>(2*sizeof(float)));
        gl.DrawArrays(GL_TRIANGLE_STRIP,0,4);
        SDL_GL_SwapWindow(window);
        // GraphicsState restores bindings, vertex attributes and enable flags.

    }
    void video(const void* data,unsigned width,unsigned height,std::size_t pitch) {
        if(!data) return; // Libretro duplicate-frame signal; retain previous output.
        if(!width || !height || width>8192 || height>8192) throw std::runtime_error("Invalid video frame dimensions");
        frame_width=width; frame_height=height;
        if(data==RETRO_HW_FRAME_BUFFER_VALID) {
            if(!gl_ready) throw std::runtime_error("Core delivered hardware video without a hardware context");
            present_gl(width,height);
        } else {
            if(gl_ready) throw std::runtime_error("Core mixed software video with its hardware context");
            if(!renderer) throw std::runtime_error("Software renderer has not initialized");
            const std::size_t bytes_per_pixel=pixel_format==RETRO_PIXEL_FORMAT_XRGB8888 ? 4 : 2;
            if(pitch<static_cast<std::size_t>(width)*bytes_per_pixel || pitch>static_cast<std::size_t>(std::numeric_limits<int>::max())) throw std::runtime_error("Invalid video pitch");
            const SDL_PixelFormat format=pixel_format==RETRO_PIXEL_FORMAT_XRGB8888 ? SDL_PIXELFORMAT_XRGB8888 : SDL_PIXELFORMAT_RGB565;
            if(!texture || width!=texture_width || height!=texture_height) {
                if(texture) SDL_DestroyTexture(texture);
                texture=SDL_CreateTexture(renderer,format,SDL_TEXTUREACCESS_STREAMING,static_cast<int>(width),static_cast<int>(height));
                if(!texture) throw std::runtime_error(std::string("Video texture: ")+SDL_GetError());
                texture_width=width; texture_height=height; SDL_SetTextureScaleMode(texture,SDL_SCALEMODE_LINEAR);
            }
            if(pixel_format==RETRO_PIXEL_FORMAT_0RGB1555) {
                converted.resize(static_cast<std::size_t>(width)*height);
                for(unsigned y=0;y<height;++y) {
                    const auto* row=static_cast<const unsigned char*>(data)+y*pitch;
                    for(unsigned x=0;x<width;++x) { uint16_t p=0; std::memcpy(&p,row+x*2,2); converted[y*width+x]=static_cast<uint16_t>(((p&0x7fe0)<<1)|(p&0x1f)|((p&0x200)>>4)); }
                }
                if(!SDL_UpdateTexture(texture,nullptr,converted.data(),static_cast<int>(width*2))) throw std::runtime_error(SDL_GetError());
            } else if(!SDL_UpdateTexture(texture,nullptr,data,static_cast<int>(pitch))) throw std::runtime_error(SDL_GetError());
            SDL_SetRenderDrawColor(renderer,0,0,0,255); SDL_RenderClear(renderer);
            const auto box=destination(width,height);
            if(rotation%2) {
                SDL_FRect rotated{box.x+(box.w-box.h)/2,box.y+(box.h-box.w)/2,box.h,box.w};
                SDL_RenderTextureRotated(renderer,texture,nullptr,&rotated,rotation*90,nullptr,SDL_FLIP_NONE);
            } else SDL_RenderTextureRotated(renderer,texture,nullptr,&box,rotation*90,nullptr,SDL_FLIP_NONE);
            if(!SDL_RenderPresent(renderer)) throw std::runtime_error(SDL_GetError());
            software_seen=true;
        }
        have_frame=true; ++rendered_frames;
    }
    static void video_callback(const void* data,unsigned width,unsigned height,std::size_t pitch) {
        try { if(active) active->video(data,width,height,pitch); } catch(const std::exception& e) { if(active) active->error(e.what()); }
    }
    void setup_audio() {
        if(!options.audio || !(av.timing.sample_rate>=8000 && av.timing.sample_rate<=384000)) return;
        SDL_AudioSpec spec{}; spec.freq=static_cast<int>(av.timing.sample_rate); spec.channels=2; spec.format=SDL_AUDIO_S16;
        if(audio) { SDL_DestroyAudioStream(audio); audio=nullptr; }
        audio=SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,&spec,nullptr,nullptr);
        if(!audio) std::fprintf(stderr,"EMULATOR_AUDIO_UNAVAILABLE %s\n",SDL_GetError());
        else if(!SDL_ResumeAudioStreamDevice(audio)) std::fprintf(stderr,"EMULATOR_AUDIO_UNAVAILABLE %s\n",SDL_GetError());
    }
    std::size_t audio_batch(const int16_t* samples,std::size_t frames) {
        if(audio && samples && frames && frames<=static_cast<std::size_t>(std::numeric_limits<int>::max()/4)) {
            // Bound queue growth when a core runs faster than real time or the
            // output device stalls. The next frame continues at the core FPS.
            const int limit=static_cast<int>(av.timing.sample_rate)*4/4;
            if(SDL_GetAudioStreamQueued(audio)>limit) SDL_ClearAudioStream(audio);
            if(!SDL_PutAudioStreamData(audio,samples,static_cast<int>(frames*4))) error(std::string("Audio queue: ")+SDL_GetError());
        }
        return frames;
    }
    static std::size_t batch_callback(const int16_t* samples,std::size_t frames) { return active ? active->audio_batch(samples,frames) : frames; }
    static void sample_callback(int16_t left,int16_t right) {
        if(!active) return;
        auto& a=*active; a.single_audio[a.single_audio_count++]=left; a.single_audio[a.single_audio_count++]=right;
        if(a.single_audio_count==a.single_audio.size()) { a.audio_batch(a.single_audio.data(),a.single_audio_count/2); a.single_audio_count=0; }
    }
    void attach(SDL_JoystickID id) {
        for(auto* pad:pads) if(pad && SDL_GetGamepadID(pad)==id) return;
        for(auto& pad:pads) if(!pad) { pad=SDL_OpenGamepad(id); break; }
    }
    void events() {
        SDL_Event event;
        while(SDL_PollEvent(&event)) {
            if(event.type==SDL_EVENT_QUIT) quit=true;
            if(event.type==SDL_EVENT_KEY_DOWN && (event.key.key==SDLK_ESCAPE || event.key.key==SDLK_F10)) quit=true;
            if(event.type==SDL_EVENT_KEY_DOWN && !event.key.repeat && event.key.key==SDLK_F5) save_requested=true;
            if(event.type==SDL_EVENT_KEY_DOWN && !event.key.repeat && event.key.key==SDLK_F8) load_requested=true;
            if(event.type==SDL_EVENT_GAMEPAD_ADDED) attach(event.gdevice.which);
            if(event.type==SDL_EVENT_GAMEPAD_REMOVED) for(auto& pad:pads) if(pad && SDL_GetGamepadID(pad)==event.gdevice.which) { SDL_CloseGamepad(pad); pad=nullptr; }
        }
        for(std::size_t i=0;i<pads.size();++i) {
            const bool back=pads[i] && SDL_GetGamepadButton(pads[i],SDL_GAMEPAD_BUTTON_BACK);
            const bool save=back && SDL_GetGamepadButton(pads[i],SDL_GAMEPAD_BUTTON_NORTH);
            const bool load=back && SDL_GetGamepadButton(pads[i],SDL_GAMEPAD_BUTTON_WEST);
            if(save && !save_combo[i]) save_requested=true;
            if(load && !load_combo[i]) load_requested=true;
            save_combo[i]=save; load_combo[i]=load;
        }
    }
    static void input_poll() { SDL_PumpEvents(); }
    int16_t button(unsigned port,unsigned id) const {
        static constexpr std::array<SDL_GamepadButton,16> buttons{
            SDL_GAMEPAD_BUTTON_SOUTH,SDL_GAMEPAD_BUTTON_WEST,SDL_GAMEPAD_BUTTON_BACK,SDL_GAMEPAD_BUTTON_START,
            SDL_GAMEPAD_BUTTON_DPAD_UP,SDL_GAMEPAD_BUTTON_DPAD_DOWN,SDL_GAMEPAD_BUTTON_DPAD_LEFT,SDL_GAMEPAD_BUTTON_DPAD_RIGHT,
            SDL_GAMEPAD_BUTTON_EAST,SDL_GAMEPAD_BUTTON_NORTH,SDL_GAMEPAD_BUTTON_LEFT_SHOULDER,SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER,
            SDL_GAMEPAD_BUTTON_INVALID,SDL_GAMEPAD_BUTTON_INVALID,SDL_GAMEPAD_BUTTON_LEFT_STICK,SDL_GAMEPAD_BUTTON_RIGHT_STICK};
        static constexpr std::array<SDL_Scancode,16> keys{
            SDL_SCANCODE_Z,SDL_SCANCODE_A,SDL_SCANCODE_BACKSPACE,SDL_SCANCODE_RETURN,SDL_SCANCODE_UP,SDL_SCANCODE_DOWN,SDL_SCANCODE_LEFT,SDL_SCANCODE_RIGHT,
            SDL_SCANCODE_X,SDL_SCANCODE_S,SDL_SCANCODE_Q,SDL_SCANCODE_W,SDL_SCANCODE_1,SDL_SCANCODE_2,SDL_SCANCODE_3,SDL_SCANCODE_4};
        if(port>=pads.size() || id>=buttons.size()) return 0;
        if((save_combo[port] || load_combo[port]) && (id==RETRO_DEVICE_ID_JOYPAD_SELECT || id==RETRO_DEVICE_ID_JOYPAD_X || id==RETRO_DEVICE_ID_JOYPAD_Y)) return 0;
        bool down=port==0 && SDL_GetKeyboardState(nullptr)[keys[id]];
        if((options.test_input || options.state_roundtrip_test) && port==0 && id==RETRO_DEVICE_ID_JOYPAD_A && test_frame>=30 && test_frame<45) down=true;
        if(auto* pad=pads[port]) {
            if(id==RETRO_DEVICE_ID_JOYPAD_L2) down=down || SDL_GetGamepadAxis(pad,SDL_GAMEPAD_AXIS_LEFT_TRIGGER)>16384;
            else if(id==RETRO_DEVICE_ID_JOYPAD_R2) down=down || SDL_GetGamepadAxis(pad,SDL_GAMEPAD_AXIS_RIGHT_TRIGGER)>16384;
            else down=down || SDL_GetGamepadButton(pad,buttons[id]);
        }
        return down ? 1 : 0;
    }
    int16_t input(unsigned port,unsigned device,unsigned index,unsigned id) const {
        device&=RETRO_DEVICE_MASK;
        if(device==RETRO_DEVICE_JOYPAD) {
            if(id==RETRO_DEVICE_ID_JOYPAD_MASK) { uint16_t bits=0; for(unsigned i=0;i<16;++i) if(button(port,i)) bits|=static_cast<uint16_t>(1u<<i); return static_cast<int16_t>(bits); }
            return button(port,id);
        }
        if(device==RETRO_DEVICE_ANALOG && port<pads.size() && index<=1 && id<=1) {
            const SDL_GamepadAxis axis=index==RETRO_DEVICE_INDEX_ANALOG_LEFT ? (id==RETRO_DEVICE_ID_ANALOG_X ? SDL_GAMEPAD_AXIS_LEFTX : SDL_GAMEPAD_AXIS_LEFTY) : (id==RETRO_DEVICE_ID_ANALOG_X ? SDL_GAMEPAD_AXIS_RIGHTX : SDL_GAMEPAD_AXIS_RIGHTY);
            int16_t value=pads[port] ? SDL_GetGamepadAxis(pads[port],axis) : 0;
            if(std::abs(static_cast<int>(value))<4000) value=0;
            if(port==0 && index==RETRO_DEVICE_INDEX_ANALOG_LEFT) {
                const int minus=button(0,id==RETRO_DEVICE_ID_ANALOG_X ? RETRO_DEVICE_ID_JOYPAD_LEFT : RETRO_DEVICE_ID_JOYPAD_UP);
                const int plus=button(0,id==RETRO_DEVICE_ID_ANALOG_X ? RETRO_DEVICE_ID_JOYPAD_RIGHT : RETRO_DEVICE_ID_JOYPAD_DOWN);
                if(minus || plus) value=static_cast<int16_t>((plus-minus)*32767);
            }
            return value;
        }
        if(device==RETRO_DEVICE_POINTER && port==0 && index==0 && window) {
            float x=0,y=0; const auto mouse=SDL_GetMouseState(&x,&y); int ww=0,wh=0,pw=0,ph=0; SDL_GetWindowSize(window,&ww,&wh); SDL_GetWindowSizeInPixels(window,&pw,&ph);
            if(ww<=0 || wh<=0) return 0;
            x*=static_cast<float>(pw)/ww; y*=static_cast<float>(ph)/wh;
            const auto box=destination(std::max(av.geometry.base_width,1u),std::max(av.geometry.base_height,1u));
            if(id==RETRO_DEVICE_ID_POINTER_PRESSED) return (mouse&SDL_BUTTON_LMASK) && x>=box.x && x<box.x+box.w && y>=box.y && y<box.y+box.h;
            if(id==RETRO_DEVICE_ID_POINTER_COUNT) return (mouse&SDL_BUTTON_LMASK) ? 1 : 0;
            if(id==RETRO_DEVICE_ID_POINTER_X) return static_cast<int16_t>((std::clamp((x-box.x)/box.w,0.0F,1.0F)*2-1)*32767);
            if(id==RETRO_DEVICE_ID_POINTER_Y) return static_cast<int16_t>((std::clamp((y-box.y)/box.h,0.0F,1.0F)*2-1)*32767);
        }
        return 0;
    }
    static int16_t input_callback(unsigned port,unsigned device,unsigned index,unsigned id) { return active ? active->input(port,device,index,id) : 0; }
    static std::string safe_name(const std::string& name) {
        std::string out; for(unsigned char c:name) if(out.size()<72) out+=std::isalnum(c) || c=='-' || c=='_' ? static_cast<char>(c) : '_';
        return out.empty() ? "game" : out;
    }
    void prepare_directories() {
        options.system_dir=fs::absolute(options.system_dir).string(); options.core=fs::absolute(options.core).string(); options.rom=fs::canonical(options.rom).string();
        std::uint64_t hash=14695981039346656037ULL; for(unsigned char c:options.rom) { hash^=c; hash*=1099511628211ULL; }
        std::ostringstream id; id<<safe_name(fs::path(options.rom).stem().string())<<'-'<<std::hex<<hash;
        save_directory=(fs::absolute(options.save_dir)/safe_name(fs::path(options.core).stem().string())/id.str()).string();
        fs::create_directories(save_directory); fs::create_directories(options.system_dir);
        save_file=(fs::path(save_directory)/"save.srm").string();
        battery_saves[0].path(save_file);
        battery_saves[1].path(fs::path(save_directory)/"save.rtc");
    }
    void load_ram() {
        const std::array<unsigned,2> ids{RETRO_MEMORY_SAVE_RAM,RETRO_MEMORY_RTC};
        for(std::size_t i=0;i<ids.size();++i) {
            const auto size=core.get_memory_size(ids[i]);
            if(battery_saves[i].load(core.get_memory_data(ids[i]),size))
                std::printf("EMULATOR_%s_LOADED bytes=%zu file=%s\n",i==0?"SRAM":"RTC",size,
                            (fs::path(save_directory)/(i==0?"save.srm":"save.rtc")).c_str());
        }
    }
    void save_ram() noexcept {
        const std::array<unsigned,2> ids{RETRO_MEMORY_SAVE_RAM,RETRO_MEMORY_RTC};
        for(std::size_t i=0;i<ids.size();++i) try {
            const auto* bytes=static_cast<const char*>(core.get_memory_data(ids[i]));
            const auto size=core.get_memory_size(ids[i]);
            if(!battery_saves[i].save(bytes,size)) continue;
            std::uint64_t hash=14695981039346656037ULL;
            for(std::size_t j=0;j<size;++j) { hash^=static_cast<unsigned char>(bytes[j]);hash*=1099511628211ULL; }
            std::printf("EMULATOR_%s_SAVED bytes=%zu hash=%016llx file=%s\n",i==0?"SRAM":"RTC",size,
                        static_cast<unsigned long long>(hash),
                        (fs::path(save_directory)/(i==0?"save.srm":"save.rtc")).c_str());
            std::fflush(stdout);
        } catch(const std::exception& e) { error(e.what()); }
    }
    static void atomic_write(const std::string& path,const char* bytes,std::size_t size) {
        const std::string temporary=path+".tmp-"+std::to_string(getpid());
        const int fd=open(temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
        if(fd<0) throw std::runtime_error("Cannot create temporary state file");
        std::size_t written=0;
        while(written<size) {
            const auto n=write(fd,bytes+written,size-written);
            if(n<=0) { close(fd); unlink(temporary.c_str()); throw std::runtime_error("Incomplete state write"); }
            written+=static_cast<std::size_t>(n);
        }
        const bool synced=fsync(fd)==0,closed=close(fd)==0;
        if(!synced || !closed || rename(temporary.c_str(),path.c_str())!=0) { unlink(temporary.c_str()); throw std::runtime_error("Cannot commit state file"); }
        const int dir=open(fs::path(path).parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
        if(dir>=0) { fsync(dir); close(dir); }
    }
    bool state(bool load) {
        try {
            const auto size=core.serialize_size();
            if(!size || size>256*1024*1024) throw std::runtime_error("Core does not support a usable save state");
            const std::string path=(fs::path(save_directory)/"quick.state").string();
            const std::string identity=std::string(info.library_name ? info.library_name : "")+"\n"+(info.library_version ? info.library_version : "");
            std::uint64_t identity_hash=14695981039346656037ULL;
            for(unsigned char c:identity) { identity_hash^=c; identity_hash*=1099511628211ULL; }
            constexpr std::array<char,16> magic{'4','0','3','O','S','_','S','T','A','T','E','_','V','1','\n','\0'};
            std::vector<char> bytes(size+24);
            std::copy(magic.begin(),magic.end(),bytes.begin());
            for(unsigned i=0;i<8;++i) bytes[16+i]=static_cast<char>((identity_hash>>(i*8))&255);
            if(load) {
                if(!fs::is_regular_file(path) || fs::file_size(path)!=bytes.size()) throw std::runtime_error("No compatible save state exists for this game/core");
                std::vector<char> saved(bytes.size()); std::ifstream file(path,std::ios::binary); file.read(saved.data(),static_cast<std::streamsize>(saved.size()));
                if(!file || !std::equal(bytes.begin(),bytes.begin()+24,saved.begin())) throw std::runtime_error("Save state belongs to another core version or format");
                if(!core.unserialize(saved.data()+24,size)) throw std::runtime_error("Core rejected saved state");
                if(audio) SDL_ClearAudioStream(audio);
                single_audio_count=0;
            } else {
                if(!core.serialize(bytes.data()+24,size)) throw std::runtime_error("Core could not serialize the current game state");
                atomic_write(path,bytes.data(),bytes.size());
            }
            std::printf("EMULATOR_STATE_%s bytes=%zu file=%s\n",load ? "LOADED" : "SAVED",size,path.c_str()); std::fflush(stdout); return true;
        } catch(const std::exception& e) {
            std::fprintf(stderr,"EMULATOR_STATE_UNAVAILABLE action=%s reason=%s\n",load ? "load" : "save",e.what()); return false;
        }
    }
    void screenshot() {
        if(options.screenshot.empty()) return;
        if(!have_frame) throw std::runtime_error("No video frame available for screenshot");
        SDL_Surface* surface=nullptr;
        if(gl_ready) {
            surface=SDL_CreateSurface(static_cast<int>(frame_width),static_cast<int>(frame_height),SDL_PIXELFORMAT_RGBA32);
            if(!surface) throw std::runtime_error(SDL_GetError());
            gl.BindFramebuffer(GL_FRAMEBUFFER,framebuffer); gl.ReadPixels(0,0,static_cast<GLsizei>(frame_width),static_cast<GLsizei>(frame_height),GL_RGBA,GL_UNSIGNED_BYTE,surface->pixels);
            if(hw.bottom_left_origin) {
                std::vector<unsigned char> row(static_cast<std::size_t>(surface->pitch)); auto* pixels=static_cast<unsigned char*>(surface->pixels);
                for(int y=0;y<surface->h/2;++y) { auto* a=pixels+y*surface->pitch; auto* b=pixels+(surface->h-1-y)*surface->pitch; std::memcpy(row.data(),a,row.size()); std::memcpy(a,b,row.size()); std::memcpy(b,row.data(),row.size()); }
            }
        } else {
            // Render again before reading: reading the backbuffer after Present
            // has undefined contents on some SDL render drivers.
            SDL_SetRenderDrawColor(renderer,0,0,0,255); SDL_RenderClear(renderer);
            const auto box=destination(frame_width,frame_height); SDL_RenderTexture(renderer,texture,nullptr,&box);
            surface=SDL_RenderReadPixels(renderer,nullptr);
        }
        if(!surface) throw std::runtime_error(std::string("Screenshot: ")+SDL_GetError());
        const bool saved=SDL_SaveBMP(surface,options.screenshot.c_str()); SDL_DestroySurface(surface);
        if(!saved) throw std::runtime_error(std::string("Screenshot: ")+SDL_GetError());
        std::printf("EMULATOR_SCREENSHOT %s\n",options.screenshot.c_str());
    }
    int run() {
        std::printf("EMULATOR_CORE name=%s version=%s extensions=%s fullpath=%d\n",info.library_name ? info.library_name : "?",info.library_version ? info.library_version : "?",info.valid_extensions ? info.valid_extensions : "",info.need_fullpath);
        if(options.probe) return 0;
        prepare_directories();
        if(!SDL_Init(SDL_INIT_VIDEO|SDL_INIT_GAMEPAD|(options.audio ? SDL_INIT_AUDIO : 0))) throw std::runtime_error(SDL_GetError());
        SDL_AddGamepadMappingsFromFile("/usr/share/surface/gamecontrollerdb.txt");
        SDL_AddGamepadMappingsFromFile("/data/config/gamecontrollerdb.txt");
        if(const char* personal=std::getenv("SDL_GAMECONTROLLERCONFIG_FILE"))SDL_AddGamepadMappingsFromFile(personal);
        std::signal(SIGTERM,request_stop); std::signal(SIGINT,request_stop);
        core.set_environment(environment_callback); core.set_video_refresh(video_callback); core.set_audio_sample(sample_callback); core.set_audio_sample_batch(batch_callback);
        core.set_input_poll(input_poll); core.set_input_state(input_callback);
        core.init(); initialized=true;
        retro_game_info game{}; game.path=options.rom.c_str();
        if(!info.need_fullpath) {
            const auto size=fs::file_size(options.rom); if(size>1024ULL*1024*1024) throw std::runtime_error("Core requires in-memory content larger than the 1 GiB content limit");
            content.resize(static_cast<std::size_t>(size)); std::ifstream rom(options.rom,std::ios::binary); rom.read(content.data(),static_cast<std::streamsize>(content.size())); if(!rom) throw std::runtime_error("Cannot read complete game file");
            game.data=content.data(); game.size=content.size();
        }
        if(!core.load_game(&game)) throw std::runtime_error("Core rejected game; check its supported format and required BIOS files");
        loaded=true; core.get_system_av_info(&av);
        if(!valid_geometry() || !std::isfinite(av.timing.fps) || av.timing.fps<1 || av.timing.fps>1000) throw std::runtime_error("Core reported invalid AV geometry or timing");
        if(gl_ready) {
            prepare_framebuffer();
            if(hw.context_reset) hw.context_reset();
            context_ready=true;
        } else {
            if(!window) window=SDL_CreateWindow("403 OS / Emulator",1200,800,SDL_WINDOW_RESIZABLE|(options.windowed ? 0 : SDL_WINDOW_FULLSCREEN));
            renderer=window ? SDL_CreateRenderer(window,nullptr) : nullptr; if(!renderer) throw std::runtime_error(std::string("Renderer: ")+SDL_GetError());
        }
        setup_audio(); load_ram();
        for(unsigned port=0;port<pads.size();++port) core.set_controller_port_device(port,RETRO_DEVICE_JOYPAD);
        int count=0; SDL_JoystickID* ids=SDL_GetGamepads(&count); for(int i=0;i<count;++i) attach(ids[i]); SDL_free(ids);
        std::printf("EMULATOR_RUNNING fps=%.6f sample_rate=%.1f video=%s save=%s\n",av.timing.fps,av.timing.sample_rate,gl_ready ? "hardware" : "software",save_directory.c_str()); std::fflush(stdout);
        next_frame=std::chrono::steady_clock::now(); unsigned long long ran=0; auto next_save=next_frame+std::chrono::seconds(30);
        while(!quit && !stop_requested && (!options.frames || ran<options.frames)) {
            events(); if(quit) break;
            test_frame=ran;
            if(options.state_roundtrip_test && ran==30) save_requested=true;
            if(options.state_roundtrip_test && ran==60) load_requested=true;
            if(save_requested) { const bool okay=state(false); save_requested=false; if(!okay && options.state_roundtrip_test) throw std::runtime_error("State roundtrip save failed"); }
            if(load_requested) { const bool okay=state(true); load_requested=false; if(!okay && options.state_roundtrip_test) throw std::runtime_error("State roundtrip load failed"); }
            if(gl_ready) { SDL_GL_MakeCurrent(window,context); prepare_framebuffer(); }
            core.run(); ++ran;
            if(single_audio_count) { audio_batch(single_audio.data(),single_audio_count/2); single_audio_count=0; }
            const auto now=std::chrono::steady_clock::now(); if(now>=next_save) { save_ram(); next_save=now+std::chrono::seconds(30); }
            const auto step=std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(1.0/av.timing.fps));
            next_frame+=step; if(now-next_frame>std::chrono::milliseconds(250)) next_frame=now;
            std::this_thread::sleep_until(next_frame);
        }
        if(!failed) screenshot();
        std::printf("EMULATOR_STOPPED runs=%llu video_frames=%llu test_input=%d\n",ran,rendered_frames,options.test_input); std::fflush(stdout);
        return failed ? 1 : 0;
    }
    unsigned long long test_frame=0;
};
Frontend* Frontend::active=nullptr;
}
int main(int argc,char** argv) {
    try { Frontend frontend(arguments(argc,argv)); return frontend.run(); }
    catch(const std::exception& e) { std::fprintf(stderr,"EMULATOR_ERROR %s\n",e.what()); return 1; }
}
