import importlib.util
from pathlib import Path
import struct
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ipega_hid_replay', ROOT / 'scripts/ipega-hid-replay.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class IpegaHidReplayTests(unittest.TestCase):
    def test_original_descriptor_identity_and_create2_layout(self):
        self.assertEqual(len(replay.DESCRIPTOR), 305)
        packet = replay.create_packet()
        self.assertEqual(struct.unpack_from('=I', packet)[0], 11)
        identity = struct.unpack_from('=128s64s64sHHIIII', packet, 4)
        self.assertEqual(identity[3:], (305, 5, 0x1949, 0x0402, 0x011b, 0))
        self.assertEqual(packet[280:], replay.DESCRIPTOR)

    def test_independent_buttons_hat_and_sticks_match_report_seven_bits(self):
        report = replay.neutral_report()
        report = replay.command_report(report, {'button': 'south', 'pressed': True})
        report = replay.command_report(report, {'button': 'rightstick', 'pressed': True})
        report = replay.command_report(report, {'hat': 'right'})
        report = replay.command_report(report, {'axis': 'leftx', 'value': 255})
        self.assertEqual(report, bytes((7, 255, 128, 128, 128, 2, 1, 64, 0, 0, 0)))
        report = replay.command_report(report, {'button': 'south', 'pressed': False})
        self.assertEqual(report[6:8], bytes((0, 64)))
        self.assertEqual(replay.command_report(report, {'neutral': True}), replay.neutral_report())

    def test_get_report_returns_current_controller_state(self):
        report = replay.command_report(replay.neutral_report(), {'hat': 'down'})
        request = replay.packet(replay.UHID_GET_REPORT, struct.pack('=IBB', 543, 7, 2))
        reply = replay.reply_to_request(request, report)
        self.assertEqual(struct.unpack_from('=IIHH', reply), (10, 543, 0, 11))
        self.assertEqual(reply[12:], report)

    def test_invalid_commands_do_not_mutate_state(self):
        report = replay.neutral_report()
        for value in ({'axis': 'leftx', 'value': 256}, {'button': 'south', 'pressed': 1},
                      {'hat': 'invalid'}, {'button': 'south', 'pressed': True, 'extra': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replay.command_report(report, value)
            self.assertEqual(report, replay.neutral_report())

    def test_inspection_never_opens_uhid(self):
        with mock.patch.object(replay.os, 'open', side_effect=AssertionError('Device creation forbidden')), \
                mock.patch.object(replay, 'emit'):
            self.assertEqual(replay.main(['--inspect-only']), 0)

    def test_host_is_rejected_before_opening_device(self):
        with mock.patch.object(replay.Path, 'read_text', return_value='A physical host'), \
                mock.patch.object(replay.os, 'open', side_effect=AssertionError('Device creation forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'disposable'):
                replay.main([])


if __name__ == '__main__':
    unittest.main()
