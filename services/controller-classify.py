#!/usr/bin/python3
"""Classify iPega HID collections by capabilities, not their shared vendor ID."""
from pathlib import Path
import re
import sys


def bitmap(value):
    # sysfs prints machine-word chunks, most significant first (x86_64 target).
    words = value.split()
    if not words or any(not re.fullmatch(r'[0-9a-fA-F]{1,16}', word) for word in words):
        raise ValueError('Invalid capability bitmap')
    return sum(int(word, 16) << (64 * index) for index, word in enumerate(reversed(words)))


def classify(keys, axes):
    buttons = any(keys & (1 << code) for code in range(0x120, 0x140))
    buttons |= any(keys & (1 << code) for code in range(0x100, 0x110))
    dpad = any(keys & (1 << code) for code in range(0x220, 0x224))
    if (buttons and axes) or (buttons and dpad):
        return {'ID_INPUT_JOYSTICK': '1', 'ID_INPUT_KEYBOARD': '0', 'ID_INPUT_MOUSE': '0'}
    # Composite keyboard/mouse reports must not occupy player one or hide the
    # actual gamepad in a list of identical VID/PID/name-derived mappings.
    return {'ID_INPUT_JOYSTICK': '0'}


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r'event[0-9]+|js[0-9]+', sys.argv[1]):
        return 1
    device = Path('/sys/class/input') / sys.argv[1] / 'device'
    try:
        if (device / 'id/vendor').read_text().strip() != '1949':
            return 1
        props = classify(bitmap((device / 'capabilities/key').read_text()),
                         bitmap((device / 'capabilities/abs').read_text()))
    except (OSError, ValueError):
        return 1
    for key, value in props.items():
        print(key + '=' + value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
