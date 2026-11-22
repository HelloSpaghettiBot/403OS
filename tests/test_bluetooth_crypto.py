"""Failure semantics for public-vector AF_ALG checks; no radio access."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('bluetooth_crypto', Path(__file__).resolve().parents[1]/'services/bluetooth-crypto.py')
crypto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crypto)


class CryptoCheckTests(unittest.TestCase):
    def test_missing_kernel_interface_cannot_report_success(self):
        with patch.object(crypto.socket, 'socket', side_effect=OSError(97, 'Address family not supported')):
            result = crypto.check()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(len(result['checks']), 2)
        self.assertTrue(all(row['status'] == 'FAIL' and row['errno'] == 97 for row in result['checks']))
        self.assertFalse(result['physical_bluetooth_verified'])

    def test_wrong_known_answer_is_rejected_and_sockets_closed(self):
        parent, operation = Mock(), Mock()
        parent.__enter__ = Mock(return_value=parent)
        parent.__exit__ = Mock(return_value=False)
        operation.__enter__ = Mock(return_value=operation)
        operation.__exit__ = Mock(return_value=False)
        parent.accept.return_value = (operation, None)
        operation.sendmsg.return_value = 16
        operation.recv.return_value = bytes(16)
        with patch.object(crypto.socket, 'socket', return_value=parent):
            with self.assertRaisesRegex(ValueError, 'known-answer'):
                crypto.known_answer(*crypto.VECTORS[0])
        parent.__exit__.assert_called_once()
        operation.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
