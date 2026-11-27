import fnmatch
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'external/board/surface-go1/users.txt'
RULE = ROOT / 'external/board/surface-go1/overlay/etc/udev/rules.d/72-surface-gamepad.rules'


spec = importlib.util.spec_from_file_location("controller_classify", ROOT / "services/controller-classify.py")
classifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classifier)


def controller_rule(event):
    """Evaluate only these declarative match/assignment rules on fake events.

    An unsupported construct fails the fixture instead of being ignored. No
    udev daemon, host device node, or host permission is touched by this test.
    """
    selected = {}
    properties = dict(event)
    for line in RULE.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        pairs = []
        for field in line.split(', '):
            match = re.fullmatch(r'([A-Z_]+(?:\{[^}]+\})?)(==|!=|=)"([^"]*)"', field)
            if not match:
                raise AssertionError('Unsupported rule syntax: ' + field)
            pairs.append(match.groups())
        if all((fnmatch.fnmatchcase(properties.get(key, ''), value) == (op == '=='))
               for key, op, value in pairs if op != '='):
            for key, op, value in pairs:
                if key == 'IMPORT{program}' and op == '=':
                    assert value == '/usr/sbin/surface-controller-classify %k'
                    props = classifier.classify(event.get('keys', 0), event.get('axes', 0))
                    selected.update(('ENV{' + name + '}', value) for name, value in props.items())
                    properties.update(selected)
            selected.update((key, value) for key, op, value in pairs if op == '=' and key != 'IMPORT{program}')
            properties.update((key, value) for key, op, value in pairs if op == '=')
    return selected


class ControllerPermissionsTests(unittest.TestCase):
    def event(self, kernel='event9', **properties):
        result = dict(ACTION='add', SUBSYSTEM='input', KERNEL=kernel,
                      **{'ENV{ID_INPUT_JOYSTICK}': '1'})
        result.update(properties)
        return result

    def test_classified_controller_events_and_legacy_js_are_private_to_gamepad(self):
        for kernel in ('event9', 'js0'):
            with self.subTest(kernel=kernel):
                self.assertEqual(controller_rule(self.event(kernel)), {'GROUP': 'gamepad', 'MODE': '0660'})

    def test_ipega_hid_is_classified_when_keyboard_only_or_mixed_keyboard_and_joystick(self):
        for extra in ({'ATTRS{id/product}': '0402', 'ATTRS{name}': 'PG-9023S Keyboard'},
                      {'ATTRS{id/product}': '9999', 'ATTRS{name}': 'Unidentified'}):
            for joystick in ('', '1'):
                with self.subTest(extra=extra, joystick=joystick):
                    event = self.event(**{
                        'ENV{ID_INPUT_JOYSTICK}': joystick,
                        'ENV{ID_INPUT_KEYBOARD}': '1',
                        'ATTRS{id/vendor}': '1949', 'keys': 1 << 0x130, 'axes': 3, **extra,
                    })
                    self.assertEqual(controller_rule(event), {
                        'ENV{ID_INPUT_JOYSTICK}': '1', 'ENV{ID_INPUT_KEYBOARD}': '0', 'ENV{ID_INPUT_MOUSE}': '0',
                        'GROUP': 'gamepad', 'MODE': '0660'})

    def test_composite_keyboard_and_mouse_never_take_a_player_slot(self):
        for keys, axes in ((sum(1 << n for n in range(1, 100)), 0), (1 << 0x110, 0)):
            for kernel in ('event22', 'js1'):
                result = controller_rule(self.event(kernel, **{'ATTRS{id/vendor}': '1949',
                    'ENV{ID_INPUT_KEYBOARD}': '1', 'keys': keys, 'axes': axes}))
                self.assertEqual(result, {'ENV{ID_INPUT_JOYSTICK}': '0'})

    def test_capability_word_order(self):
        self.assertEqual(classifier.bitmap('1 0000000000000002'), (1 << 64) | 2)
        self.assertEqual(classifier.bitmap('0'), 0)
        with self.assertRaises(ValueError):
            classifier.bitmap('not-hex')

    def test_non_ipega_keyboard_does_not_gain_gamepad_access(self):
        event = self.event(**{'ENV{ID_INPUT_JOYSTICK}': '',
                              'ENV{ID_INPUT_KEYBOARD}': '1',
                              'ATTRS{id/vendor}': '1234'})
        self.assertEqual(controller_rule(event), {})

    def test_keyboard_and_touch_events_never_gain_gamepad_access(self):
        for key in ('KEYBOARD', 'TOUCHSCREEN', 'TOUCHPAD'):
            with self.subTest(key=key):
                event = self.event(**{'ENV{ID_INPUT_' + key + '}': '1'})
                self.assertEqual(controller_rule(event), {})

    def test_positive_classification_and_input_node_are_required(self):
        for key, value in [('ENV{ID_INPUT_JOYSTICK}', ''), ('ENV{ID_INPUT_JOYSTICK}', '0'),
                           ('SUBSYSTEM', 'hidraw'), ('KERNEL', 'hidraw0'), ('KERNEL', 'mouse0'),
                           ('ACTION', 'remove')]:
            with self.subTest(key=key, value=value):
                event = self.event()
                event[key] = value
                self.assertEqual(controller_rule(event), {})

    def test_upstream_mkusers_creates_fixed_group_without_keyboard_membership(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts/tests') as temporary:
            target = Path(temporary)
            etc = target / 'etc'
            etc.mkdir()
            (etc / 'passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
            (etc / 'shadow').write_text('root:!:0:0:99999:7:::\n')
            (etc / 'group').write_text('root:x:0:\ninput:x:104:\nvideo:x:28:\naudio:x:29:\nrender:x:105:\n')
            config = target / 'buildroot.config'
            config.write_text('BR2_TARGET_GENERIC_PASSWD_METHOD="sha-256"\n')
            before = (etc / 'passwd').read_bytes()
            result = subprocess.run(['bash', str(ROOT / 'buildroot/support/scripts/mkusers'), str(TABLE), str(target)],
                                    env=dict(os.environ, BR2_CONFIG=str(config)), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            groups = {row[0]: row for row in (line.split(':') for line in (etc / 'group').read_text().splitlines())}
            self.assertEqual(groups['gamepad'][2], '1100')
            self.assertIn('gaming', groups['gamepad'][3].split(','))
            self.assertNotIn('gaming', groups['input'][3].split(','))
            self.assertNotEqual((etc / 'passwd').read_bytes(), before)
            self.assertNotIn('gamepad:', (etc / 'passwd').read_text())
            # mkusers emits the later fakeroot chown script; it never runs it.
            self.assertIn(str(target), result.stdout)


if __name__ == '__main__':
    unittest.main()
