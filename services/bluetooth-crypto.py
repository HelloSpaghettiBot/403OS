#!/usr/bin/python3
"""Read-only known-answer checks for the AF_ALG interfaces used by BlueZ.

Only public NIST/RFC test vectors are used. No radio, key store, host setting
or application state is accessed or changed.
"""
import json
import signal
import socket
import struct

SOL_ALG = 279
ALG_SET_KEY = 1
ALG_SET_OP = 3
ALG_OP_ENCRYPT = 1
VECTORS = (
    ('skcipher', 'ecb(aes)', '000102030405060708090a0b0c0d0e0f',
     '00112233445566778899aabbccddeeff', '69c4e0d86a7b0430d8cdb78070b4c55a'),
    ('hash', 'cmac(aes)', '2b7e151628aed2a6abf7158809cf4f3c',
     '6bc1bee22e409f96e93d7e117393172a', '070a16b46b4d4144f79bdd9dd04a287c'),
)


def known_answer(kind, algorithm, key, message, expected):
    # AF_ALG's accept/hash sockets do not provide ordinary listening/read
    # poll readiness. Python socket timeouts poll first and would time out
    # before issuing valid syscalls. Bound blocking operations with a timer.
    def expired(_signal, _frame):
        raise TimeoutError('AF_ALG known-answer check exceeded its deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        with socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0) as parent:
            parent.bind((kind, algorithm))
            parent.setsockopt(SOL_ALG, ALG_SET_KEY, bytes.fromhex(key))
            operation, _ = parent.accept()
            with operation:
                data = bytes.fromhex(message)
                if kind == 'skcipher':
                    sent = operation.sendmsg([data], [(SOL_ALG, ALG_SET_OP, struct.pack('I', ALG_OP_ENCRYPT))])
                    if sent != len(data):
                        raise ValueError('Incomplete AF_ALG test-vector request')
                else:
                    operation.sendall(data)
                answer = operation.recv(len(bytes.fromhex(expected)))
                if answer.hex() != expected:
                    raise ValueError('AF_ALG known-answer result did not match')
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        signal.signal(signal.SIGALRM, previous)


def check():
    results = []
    for vector in VECTORS:
        result = dict(interface=vector[0], algorithm=vector[1], status='PASS')
        try:
            known_answer(*vector)
        except (OSError, ValueError, AttributeError) as error:
            result.update(status='FAIL', error=str(error), errno=getattr(error, 'errno', None))
        results.append(result)
    return dict(event='bluetooth-crypto-check', status='PASS' if all(
        value['status'] == 'PASS' for value in results) else 'FAIL', checks=results,
        scope='public-known-answer-vectors', physical_bluetooth_verified=False)


def main():
    result = check()
    print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
