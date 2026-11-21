"""Audit pinned upstream names and exercise real patched publication code.

No host Dolphin, SDL desktop or game is launched. The small native fixture
compiles only SaveSurfaceDefaultsIfAbsent with test controller/INI adapters.
"""
import configparser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT/"artifacts/upstream-emulators/dolphin"
PATCH = ROOT/"external/patches/surface-dolphin/0001-sdl-gamepad-defaults-and-persistent-hotkeys.patch"
PIN = "2fe162866282fa2806d62f69aa053315d750ab91"
FILES = ["Source/Core/Core/HW/GCPadEmu.cpp", "Source/Core/InputCommon/InputConfig.h",
         "Source/Core/InputCommon/InputConfig.cpp", "Source/Core/Core/HotkeyManager.cpp",
         "Source/Core/InputCommon/ControllerInterface/SDL/SDL.cpp"]


def function(source, signature):
    first = source.index(signature)
    brace = source.index("{", first)
    depth = 1
    cursor = brace+1
    while depth:
        depth += (source[cursor] == "{")-(source[cursor] == "}")
        cursor += 1
    return source[first:cursor]


@unittest.skipUnless((UPSTREAM/FILES[0]).is_file(), "Fetch the pinned Dolphin source before source audits")
class DolphinDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="surface-dolphin-input-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        metadata = json.loads((UPSTREAM/"surface-source.json").read_text())
        if metadata["sha"] != PIN:
            raise AssertionError("Source audit must use the recipe's pinned Dolphin revision")
        for name in FILES:
            (cls.work/name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(UPSTREAM/name, cls.work/name)
        subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=cls.work,
                       check=True, capture_output=True, timeout=10)
        subprocess.run(["git", "apply", str(PATCH)], cwd=cls.work,
                       check=True, capture_output=True, timeout=10)
        cls.pad = (cls.work/FILES[0]).read_text()
        cls.config = (cls.work/FILES[2]).read_text()

    def test_pad_inputs_match_official_profile_and_axis_direction(self):
        profile = configparser.ConfigParser(interpolation=None)
        profile.optionxform = str
        profile.read(UPSTREAM/"Data/Sys/Profiles/GCPad/SDL Gamepad.ini")
        selected = profile["Profile"]
        arrays = {
            "buttons": ["Buttons/A", "Buttons/B", "Buttons/X", "Buttons/Y", "Buttons/Z", "Buttons/Start"],
            "main_axes": ["Main Stick/Up", "Main Stick/Down", "Main Stick/Left", "Main Stick/Right"],
            "c_axes": ["C-Stick/Up", "C-Stick/Down", "C-Stick/Left", "C-Stick/Right"],
            "pad_buttons": ["D-Pad/Up", "D-Pad/Down", "D-Pad/Left", "D-Pad/Right"],
        }
        for name, controls in arrays.items():
            assigned = re.search(r"\b"+name+r" = \{([^}]+)\}", self.pad)[1]
            self.assertEqual(re.findall(r'"([^"]+)"', assigned), [selected[key].strip("`") for key in controls])
        for name in ["Trigger L", "Trigger R", "Motor L", "Motor R"]:
            self.assertIn(name, selected["Rumble/Motor"] if name.startswith("Motor") else selected["Triggers/L-Analog"]+selected["Triggers/R-Analog"])
            self.assertIn(name, self.pad)

    def test_actual_ciface_qualifier_and_keyboard_source_are_used(self):
        self.assertIn("gamepad.FromDevice(device.get())", self.pad)
        self.assertIn("keyboard.FromDevice(device.get())", self.pad)
        self.assertIn("GetExpressionForControl(key, keyboard, gamepad)", self.pad)
        self.assertNotIn("SDL_GetJoystickID", self.pad)
        keyboard = (UPSTREAM/"Source/Core/InputCommon/ControllerInterface/Xlib/XInput2.cpp").read_text()
        self.assertIn('SOURCE_NAME = "XInput2"', keyboard)
        backend = (UPSTREAM/"Source/Core/InputCommon/ControllerInterface/SDL/SDLGamepad.cpp").read_text()
        self.assertIn('return "SDL"', backend)
        self.assertIn('name == "Button A"', backend)

    def test_keyboard_escape_and_guide_behavior_match_requested_flow(self):
        hotkeys = (self.work/FILES[3]).read_text()
        self.assertIn('set_key_expression(HK_EXIT, "Escape")', hotkeys)
        self.assertIn('set_key_expression(HK_STOP, "Escape")', hotkeys)
        additions = "\n".join(line[1:] for line in PATCH.read_text().splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
        self.assertNotIn("Guide", additions)
        platform = (UPSTREAM/"Source/Core/DolphinNoGUI/PlatformX11.cpp").read_text()
        self.assertRegex(platform, r"key == XK_Escape\)\s*\{\s*RequestShutdown\(\)")

    def test_mapping_file_hint_is_set_before_sdl_init_without_file_writes(self):
        backend = (self.work/FILES[4]).read_text()
        hint = 'SDL_SetHint(SDL_HINT_GAMECONTROLLERCONFIG_FILE, "/data/config/gamecontrollerdb.txt")'
        self.assertIn("SDL_GetHint(SDL_HINT_GAMECONTROLLERCONFIG_FILE) == nullptr", backend)
        self.assertLess(backend.index(hint), backend.index("SDL_Init("))
        header = ROOT/"output/build/surface-sdl3-3.4.20/include/SDL3/SDL_hints.h"
        if header.exists():
            self.assertIn('#define SDL_HINT_GAMECONTROLLERCONFIG_FILE "SDL_GAMECONTROLLERCONFIG_FILE"', header.read_text())

    def test_late_first_gamepad_can_initialize_absent_configuration(self):
        callback = function(self.config, "void InputConfig::RegisterHotplugCallback()")
        self.assertIn('m_ini_name == "GCPadNew"', callback)
        self.assertIn("!File::Exists", callback)
        self.assertIn("m_controllers[0]->LoadDefaults(g_controller_interface)", callback)
        self.assertIn("SaveSurfaceDefaultsIfAbsent()", callback)
        self.assertIn("ControllerEmu::EmulatedController::GetStateLock()", callback)
        self.assertIn("const auto state_lock = GetStateLock()", self.pad)

    @unittest.skipUnless(shutil.which("c++"), "A native C++ compiler is required for the small persistence fixture")
    def test_native_publication_preserves_existing_racing_and_linked_profiles(self):
        method = function(self.config, "void InputConfig::SaveSurfaceDefaultsIfAbsent()")
        fixture = r'''
#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>
constexpr int D_CONFIG_IDX = 0;
namespace File {
std::string root;
std::string GetUserPath(int) { return root + "/"; }
bool Exists(const std::string& name) { return std::filesystem::exists(name); }
bool Delete(const std::string& name) { return std::filesystem::remove(name); }
}
namespace Common {
bool inject_race = false;
class IniFile {
 public:
 int section = 0;
 int* GetOrCreateSection(const std::string&) { return &section; }
 bool Save(const std::string& name) {
   std::ofstream output(name); output << "generated defaults"; output.close();
   if (inject_race) {
     std::ofstream owner(File::root + "/GCPadNew.ini"); owner << "owner racing profile";
   }
   return bool(output);
 }
};
}
struct Qualifier { std::string source = "SDL"; };
struct Controller {
 Qualifier qualifier;
 std::string GetName() const { return "GCPad1"; }
 Qualifier GetDefaultDevice() const { return qualifier; }
 void SaveConfig(int*) {}
};
class InputConfig {
 public:
 std::string m_ini_name = "GCPadNew";
 std::vector<std::unique_ptr<Controller>> m_controllers;
 void SaveSurfaceDefaultsIfAbsent();
};
std::string contents(const std::string& name) {
 std::ifstream input(name); return {std::istreambuf_iterator<char>(input), {}};
}
'''+method+r'''
int main(int argc, char** argv) {
 assert(argc == 2); File::root = argv[1];
 InputConfig input; input.m_controllers.emplace_back(std::make_unique<Controller>());
 const std::string target = File::root + "/GCPadNew.ini";
 input.m_controllers[0]->qualifier.source = "XInput2";
 input.SaveSurfaceDefaultsIfAbsent(); assert(!File::Exists(target));
 input.m_controllers[0]->qualifier.source = "SDL";
 input.SaveSurfaceDefaultsIfAbsent(); assert(contents(target) == "generated defaults");
 { std::ofstream owner(target); owner << "owner existing profile"; }
 input.SaveSurfaceDefaultsIfAbsent(); assert(contents(target) == "owner existing profile");
 std::filesystem::remove(target); Common::inject_race = true;
 input.SaveSurfaceDefaultsIfAbsent(); assert(contents(target) == "owner racing profile");
 Common::inject_race = false; std::filesystem::remove(target);
 std::filesystem::create_symlink("absent-owner-profile", target);
 input.SaveSurfaceDefaultsIfAbsent(); assert(std::filesystem::is_symlink(target));
 assert(!File::Exists(File::root + "/absent-owner-profile"));
 std::filesystem::remove(target); input.m_ini_name = "Hotkeys";
 input.m_controllers[0]->qualifier.source = "XInput2";
 input.SaveSurfaceDefaultsIfAbsent(); assert(File::Exists(File::root + "/Hotkeys.ini"));
 for (const auto& entry : std::filesystem::directory_iterator(File::root))
   assert(entry.path().filename().string().find(".surface-") == std::string::npos);
}
'''
        source, binary = self.work/"persistence-fixture.cpp", self.work/"persistence-fixture"
        source.write_text(fixture)
        subprocess.run(["c++", "-std=c++20", "-O0", str(source), "-o", str(binary)],
                       check=True, capture_output=True, timeout=20)
        data = self.work/"fixture-data"
        data.mkdir()
        subprocess.run([str(binary), str(data)], check=True, capture_output=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
