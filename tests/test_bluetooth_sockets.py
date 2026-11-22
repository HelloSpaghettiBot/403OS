"""Bluetooth socket capability checks with mocks; never touch host radios."""
from contextlib import nullcontext
import errno
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, call, patch

SPEC = importlib.util.spec_from_file_location('bluetooth_sockets',
    Path(__file__).resolve().parents[1] / 'services/bluetooth-sockets.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BluetoothSocketCheckTests(unittest.TestCase):
    def setUp(self):
        self.l2cap, self.rfcomm, self.hidp = Mock(), Mock(), Mock()
        self.l2cap.getsockopt.side_effect = [
            bytes((probe.BT_SECURITY_LOW, 0)),
            bytes((probe.BT_SECURITY_MEDIUM, 0)),
            struct.pack(probe.L2CAP_OPTIONS_FORMAT, 672, 672, 65535, 0, 1, 3, 63),
        ]
        self.socket_patch = patch.object(probe.socket, 'socket', side_effect=[
            self.l2cap, self.rfcomm, self.hidp])
        self.make_socket = self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)
        deadline_patch = patch.object(probe, 'deadline', side_effect=lambda: nullcontext())
        deadline_patch.start()
        self.addCleanup(deadline_patch.stop)

    def test_success_uses_only_unconnected_local_sockets_and_closes_them(self):
        result = probe.check()
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['physical_bluetooth_verified'])
        self.assertEqual(self.make_socket.call_args_list, [
            call(31, probe.socket.SOCK_SEQPACKET, 0),
            call(31, probe.socket.SOCK_STREAM, 3),
            call(31, probe.socket.SOCK_RAW, 6),
        ])
        self.l2cap.bind.assert_called_once_with(('00:00:00:00:00:00', 0))
        self.assertEqual(self.l2cap.setsockopt.call_args_list, [
            call(274, 4, bytes((1, 0))), call(274, 4, bytes((2, 0)))])
        self.assertEqual(self.l2cap.getsockopt.call_args_list,
                         [call(274, 4, 2), call(274, 4, 2), call(6, 1, 12)])
        for sock in (self.l2cap, self.rfcomm, self.hidp):
            sock.close.assert_called_once()
            self.assertFalse(set(name for name, _, _ in sock.mock_calls) &
                             {'connect', 'connect_ex', 'listen', 'accept', 'send',
                              'sendall', 'sendmsg', 'recv', 'ioctl'})
        self.rfcomm.bind.assert_not_called()
        self.hidp.bind.assert_not_called()

    def test_missing_l2cap_reports_errno_skips_dependents_and_checks_other_protocols(self):
        self.make_socket.side_effect = [OSError(errno.EAFNOSUPPORT, 'Unavailable'),
                                        self.rfcomm, self.hidp]
        result = probe.check()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['checks'][0]['errno'], errno.EAFNOSUPPORT)
        self.assertEqual([row['status'] for row in result['checks'][1:5]], ['SKIP'] * 4)
        self.assertEqual(result['checks'][5]['operation'], 'rfcomm_create')
        self.rfcomm.close.assert_called_once()
        self.hidp.close.assert_called_once()

    def test_bind_failure_is_retained_and_socket_is_closed(self):
        self.l2cap.bind.side_effect = OSError(errno.EACCES, 'Permission denied')
        result = probe.check()
        self.assertEqual(result['status'], 'FAIL')
        row = next(row for row in result['checks'] if row['operation'] == 'l2cap_bind_any')
        self.assertEqual(row['errno'], errno.EACCES)
        self.l2cap.close.assert_called_once()

    def test_security_failure_does_not_hide_remaining_capability_checks(self):
        self.l2cap.setsockopt.side_effect = [OSError(errno.ENOPROTOOPT, 'Unavailable'), None]
        self.l2cap.getsockopt.side_effect = [bytes((probe.BT_SECURITY_MEDIUM, 0)), bytes(12)]
        result = probe.check()
        rows = {row['operation']: row for row in result['checks']}
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(rows['l2cap_security_low']['errno'], errno.ENOPROTOOPT)
        self.assertEqual(rows['l2cap_security_medium']['status'], 'PASS')
        self.assertEqual(rows['l2cap_options']['status'], 'PASS')
        self.l2cap.close.assert_called_once()

    def test_incorrect_security_readback_is_failure(self):
        self.l2cap.getsockopt.side_effect = [bytes((0, 0)), bytes((2, 0)), bytes(12)]
        result = probe.check()
        row = next(row for row in result['checks'] if row['operation'] == 'l2cap_security_low')
        self.assertEqual(row['status'], 'FAIL')
        self.assertIsNone(row['errno'])
        self.assertIn('readback', row['error'])

    def test_incomplete_l2cap_options_is_failure(self):
        self.l2cap.getsockopt.side_effect = [bytes((1, 0)), bytes((2, 0)), bytes(10)]
        result = probe.check()
        row = next(row for row in result['checks'] if row['operation'] == 'l2cap_options')
        self.assertEqual(row['status'], 'FAIL')
        self.assertIn('incomplete', row['error'])
        self.l2cap.close.assert_called_once()

    def test_rfcomm_and_hidp_creation_errors_are_visible(self):
        self.make_socket.side_effect = [self.l2cap,
            OSError(errno.EPROTONOSUPPORT, 'RFCOMM unavailable'),
            OSError(errno.EPERM, 'HIDP denied')]
        result = probe.check()
        rows = {row['operation']: row for row in result['checks']}
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(rows['rfcomm_create']['errno'], errno.EPROTONOSUPPORT)
        self.assertEqual(rows['hidp_create']['errno'], errno.EPERM)
        self.l2cap.close.assert_called_once()

    def test_timeout_retains_errno(self):
        self.l2cap.bind.side_effect = TimeoutError(errno.ETIMEDOUT, 'Timed out')
        result = probe.check()
        row = next(row for row in result['checks'] if row['operation'] == 'l2cap_bind_any')
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['errno'], errno.ETIMEDOUT)
        self.l2cap.close.assert_called_once()

    def test_cli_json_and_exit_status_are_consistent(self):
        for status, expected in (('PASS', 0), ('FAIL', 1)):
            with self.subTest(status=status), patch.object(probe, 'check', return_value={
                    'event': 'bluetooth-socket-check', 'status': status}), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(probe.main(), expected)
                self.assertEqual(json.loads(output.getvalue())['status'], status)


class DeadlineTests(unittest.TestCase):
    def test_deadline_is_bounded_and_prior_handler_and_timer_are_restored(self):
        previous = object()
        with patch.object(probe.signal, 'signal', return_value=previous) as handler, \
                patch.object(probe.signal, 'setitimer', return_value=(0.75, 0)) as timer:
            with probe.deadline():
                timer.assert_called_once_with(probe.signal.ITIMER_REAL, 2)
            self.assertEqual(timer.call_args_list[-1], call(probe.signal.ITIMER_REAL, 0.75, 0))
            self.assertEqual(handler.call_args_list[-1], call(probe.signal.SIGALRM, previous))

    def test_timeout_has_errno_and_restores_alarm_state(self):
        previous = object()
        with patch.object(probe.signal, 'signal', return_value=previous) as handler, \
                patch.object(probe.signal, 'setitimer', return_value=(0, 0)) as timer:
            with self.assertRaises(TimeoutError) as failure:
                with probe.deadline():
                    callback = handler.call_args_list[0].args[1]
                    callback(probe.signal.SIGALRM, None)
            self.assertEqual(failure.exception.errno, errno.ETIMEDOUT)
            self.assertEqual(timer.call_args_list[-1], call(probe.signal.ITIMER_REAL, 0, 0))
            self.assertEqual(handler.call_args_list[-1], call(probe.signal.SIGALRM, previous))


if __name__ == '__main__':
    unittest.main()
