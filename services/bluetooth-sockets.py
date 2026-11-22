#!/usr/bin/python3
"""Check the local Bluetooth socket APIs used for controller connections.

This creates unconnected sockets, binds L2CAP only to BDADDR_ANY/PSM 0, and
reads or changes options on that private socket. It never opens an HCI socket,
selects an adapter, listens, connects, pairs, or accesses device state.
"""
from contextlib import contextmanager
import errno
import json
import signal
import socket
import struct

# Linux 6.12/BlueZ public Bluetooth ABI, independent of Python's optional
# exports for individual socket options/protocols.
AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0
BTPROTO_RFCOMM = 3
BTPROTO_HIDP = 6
SOL_L2CAP = 6
L2CAP_OPTIONS = 1
SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1
BT_SECURITY_MEDIUM = 2
BDADDR_ANY = '00:00:00:00:00:00'
L2CAP_OPTIONS_FORMAT = '@HHHBBBxH'
L2CAP_STEPS = ('l2cap_bind_any', 'l2cap_security_low',
               'l2cap_security_medium', 'l2cap_options')


@contextmanager
def deadline():
    # These are local syscalls, without network waits. Bound a missing/broken
    # kernel implementation too; socket timeouts can insert unwanted polling.
    def expired(_signal, _frame):
        raise TimeoutError(errno.ETIMEDOUT, 'Local Bluetooth socket check timed out')
    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        signal.signal(signal.SIGALRM, previous)


def attempt(results, operation, action):
    row = dict(operation=operation, status='PASS')
    value = None
    try:
        with deadline():
            value = action()
    except (OSError, ValueError, AttributeError) as error:
        row.update(status='FAIL', error=str(error), errno=getattr(error, 'errno', None))
    results.append(row)
    return value


def security(sock, level):
    sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, bytes((level, 0)))
    answer = sock.getsockopt(SOL_BLUETOOTH, BT_SECURITY, 2)
    if len(answer) != 2 or answer[0] != level:
        raise ValueError('BT_SECURITY readback did not match the requested level')


def options(sock):
    size = struct.calcsize(L2CAP_OPTIONS_FORMAT)
    answer = sock.getsockopt(SOL_L2CAP, L2CAP_OPTIONS, size)
    if len(answer) != size:
        raise ValueError('L2CAP_OPTIONS returned an incomplete structure')
    struct.unpack(L2CAP_OPTIONS_FORMAT, answer)


def check():
    results = []
    sock = attempt(results, 'l2cap_create', lambda: socket.socket(
        AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP))
    if sock is None:
        results.extend(dict(operation=step, status='SKIP',
                            error='L2CAP socket creation failed', errno=None)
                       for step in L2CAP_STEPS)
    else:
        try:
            attempt(results, 'l2cap_bind_any', lambda: sock.bind((BDADDR_ANY, 0)))
            attempt(results, 'l2cap_security_low', lambda: security(sock, BT_SECURITY_LOW))
            attempt(results, 'l2cap_security_medium', lambda: security(sock, BT_SECURITY_MEDIUM))
            attempt(results, 'l2cap_options', lambda: options(sock))
        finally:
            attempt(results, 'l2cap_close', sock.close)

    # Socket creation only; no HIDP ioctls or RFCOMM device operations.
    for name, kind, protocol in (('rfcomm', socket.SOCK_STREAM, BTPROTO_RFCOMM),
                                 ('hidp', socket.SOCK_RAW, BTPROTO_HIDP)):
        sock = attempt(results, name + '_create', lambda: socket.socket(
            AF_BLUETOOTH, kind, protocol))
        if sock is not None:
            attempt(results, name + '_close', sock.close)

    return dict(event='bluetooth-socket-check', status='PASS' if all(
        row['status'] == 'PASS' for row in results) else 'FAIL', checks=results,
        scope='unconnected-local-sockets', physical_bluetooth_verified=False)


def main():
    result = check()
    print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
