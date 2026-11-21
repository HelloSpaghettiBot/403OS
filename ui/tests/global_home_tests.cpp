#include "surface/global_home.hpp"
#include <iostream>
#include <linux/input.h>
#include <stdexcept>
#include <sys/stat.h>
using surface::global_home_detail::EventParser;
#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (false)
namespace {
bool report(EventParser& parser) { return parser.consume(EV_SYN, SYN_REPORT, 0); }
void key(EventParser& parser, unsigned code, int value) { CHECK(!parser.consume(EV_KEY, code, value)); }
void contact(EventParser& parser, int slot, int tracking) {
    CHECK(!parser.consume(EV_ABS, ABS_MT_SLOT, slot));
    CHECK(!parser.consume(EV_ABS, ABS_MT_TRACKING_ID, tracking));
}
void chord(EventParser& parser, bool right = false) {
    key(parser, right ? KEY_RIGHTCTRL : KEY_LEFTCTRL, 1);
    key(parser, right ? KEY_RIGHTALT : KEY_LEFTALT, 1);
    key(parser, KEY_HOME, 1);
}
}
int main() {
    { // Construction and an inactive poll never open/read input devices.
        surface::GlobalHomeInput reader;
        CHECK(!reader.poll(false));
        struct stat marker{};
        if (lstat("/etc/surface-image-id", &marker) != 0)
            CHECK(!reader.poll(true)); // The normal host is a guarded no-op.
    }
    { // Plain keys, partial chords and repeats do not return HOME.
        EventParser parser; parser.setApplicationRunning(true);
        key(parser, KEY_A, 1); CHECK(!report(parser));
        key(parser, KEY_HOME, 1); CHECK(!report(parser));
        key(parser, KEY_HOME, 0); CHECK(!report(parser));
        chord(parser); CHECK(report(parser)); CHECK(!report(parser));
        key(parser, KEY_HOME, 2); CHECK(!report(parser));
        key(parser, KEY_HOME, 0); CHECK(!report(parser));
        key(parser, KEY_HOME, 1); CHECK(report(parser));
    }
    {
        EventParser parser; parser.setApplicationRunning(true);
        chord(parser, true); CHECK(report(parser));
    }
    { // Three distinct Type B contacts; slot updates are not extra fingers.
        EventParser parser; parser.setApplicationRunning(true);
        contact(parser, 0, 10); CHECK(!report(parser));
        contact(parser, 0, 11); CHECK(!report(parser));
        contact(parser, 3, 12); CHECK(!report(parser));
        contact(parser, 7, 13); CHECK(report(parser)); CHECK(!report(parser));
        contact(parser, 7, -1); CHECK(!report(parser));
        contact(parser, 7, 14); CHECK(!report(parser));
        contact(parser, 0, -1); contact(parser, 3, -1); contact(parser, 7, -1); CHECK(!report(parser));
        contact(parser, 1, 20); contact(parser, 2, 21); contact(parser, 3, 22); CHECK(report(parser));
    }
    { // Invalid slot/index cannot modify a valid slot or invent contacts.
        EventParser parser; parser.setApplicationRunning(true);
        contact(parser, -1, 1); contact(parser, 64, 2); contact(parser, 100000, 3); CHECK(!report(parser));
        contact(parser, 0, 1); contact(parser, 1, 2); CHECK(!report(parser));
    }
    { // Gestures held on HOME cannot close a subsequently started app.
        EventParser parser;
        chord(parser); CHECK(!report(parser)); parser.setApplicationRunning(true); CHECK(!report(parser));
        key(parser, KEY_LEFTCTRL, 0); key(parser, KEY_LEFTALT, 0); key(parser, KEY_HOME, 0); CHECK(!report(parser));
        chord(parser); CHECK(report(parser));
        parser.setApplicationRunning(false); contact(parser, 0, 1); contact(parser, 1, 2); contact(parser, 2, 3);
        CHECK(!report(parser)); parser.setApplicationRunning(true); CHECK(!report(parser));
    }
    { // A dropped frame invalidates partial state and ignores events to report.
        EventParser parser; parser.setApplicationRunning(true); chord(parser);
        CHECK(!parser.consume(EV_SYN, SYN_DROPPED, 0));
        contact(parser, 0, 1); contact(parser, 1, 2); contact(parser, 2, 3); CHECK(!report(parser));
        CHECK(!report(parser)); // Fresh neutral synchronization before rearming.
        contact(parser, 0, 10); contact(parser, 1, 11); contact(parser, 2, 12); CHECK(report(parser));
    }
    // All gesture tests feed parser values; no test opens actual input devices.
    std::cout << "PASS global HOME keyboard, multitouch, release, stale and dropped event parsing\n";
}
