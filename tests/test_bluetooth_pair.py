"""Pairing protocol tests use a scripted child, never the host Bluetooth radio."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('surface_bluetooth_pair', ROOT / 'services/bluetooth-pair.py')
pairing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pairing)
ADDRESS = '00:11:22:33:44:55'

FAKE_CLIENT = r'''
import json, os, subprocess, sys, time
scenario = os.environ.get('PAIR_SCENARIO', 'ok')
def output(value):
    sys.stdout.write(value)
    sys.stdout.flush()
if scenario == 'orphan':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    open(os.environ['PAIR_CHILD_PID'], 'w').write(str(child.pid))
if scenario in ('registration-timeout', 'orphan'):
    time.sleep(60)
    sys.exit(0)
if scenario == 'early-exit':
    sys.exit(0)
output('\x1b[0;32mAgent reg')
time.sleep(.01)
output('istered\x1b[0m\n[bluetooth]# ')
for row in sys.stdin:
    command = row.rstrip('\n')
    with open(os.environ['PAIR_COMMAND_LOG'], 'a') as stream:
        stream.write(json.dumps(command) + '\n')
    if command.startswith('pair '):
        if scenario in ('pin', 'passkey', 'confirm', 'authorize'):
            prompts = dict(pin='Enter PIN code:', passkey='Enter passkey (number in 0-999999):',
                           confirm='Confirm passkey 123456 (yes/no):', authorize='Accept pairing (yes/no):')
            output('[agent] ' + prompts[scenario] + ' ')
            answer = sys.stdin.readline().rstrip('\n')
            with open(os.environ['PAIR_COMMAND_LOG'], 'a') as stream:
                stream.write(json.dumps('ANSWER:' + answer) + '\n')
            if scenario in ('confirm', 'authorize') and answer != 'yes':
                output('Failed to pair: org.bluez.Error.AuthenticationRejected\n')
                continue
        elif scenario == 'failed':
            output('Failed to pair: org.bluez.Error.AuthenticationFailed\n')
            continue
        elif scenario == 'prefixed-failed':
            # The shipped client uses "[<device alias>]> " after selecting
            # a connected device; readline redraws can leave it on this line.
            output('\r[Fixture controller]> Failed to pair: org.bluez.Error.AuthenticationFailed\n')
            continue
        elif scenario == 'already-paired':
            output('Failed to pair: org.bluez.Error.AlreadyExists\n')
            continue
        elif scenario == 'display':
            output('[agent] PIN code: 1234\n')
        output('Pairing successful\n')
    elif command.startswith('trust '):
        output('Changing ' + command[6:] + ' trust succeeded\n')
    elif command.startswith('connect '):
        output('Connection successful\n')
    elif command.startswith('info '):
        if scenario != 'no-info-header':
            output('Device ' + command[5:] + ' (public)\n')
        output('\tPaired: yes\n\tTrusted: yes\n\tConnected: ' + ('no' if scenario == 'false-readback' else 'yes') + '\n')
    else:
        sys.exit(3)
'''.replace('\\x1b', '\x1b')


class PairingProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.fake = self.base / 'client.py'
        self.fake.write_text(FAKE_CLIENT)
        self.log = self.base / 'commands.jsonl'
        self.child_pid = self.base / 'child.pid'
        self.events = []
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, self.read_fd)
        self.addCleanup(os.close, self.write_fd)

    def session(self, scenario='ok', answer=None):
        def emit(event):
            self.events.append(event)
            if event['event'] == 'prompt' and answer is not None:
                response = answer(event) if callable(answer) else dict(id=event['id'], value=answer)
                os.write(self.write_fd, json.dumps(response).encode() + b'\n')
        env = dict(os.environ, PAIR_SCENARIO=scenario, PAIR_COMMAND_LOG=str(self.log),
                   PAIR_CHILD_PID=str(self.child_pid))
        return pairing.PairingSession([sys.executable, str(self.fake)], env=env,
            emit=emit, response_fd=self.read_fd,
            timeouts=dict(overall=2, register=.4, pair=.5, trust=.5, connect=.5, info=.4, prompt=.3))

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_pair_keeps_agent_for_pair_trust_connect_and_actual_readback(self):
        session = self.session()
        self.assertEqual(session.run(ADDRESS), dict(paired=True, trusted=True, connected=True))
        self.assertEqual(self.commands(), ['pair ' + ADDRESS, 'trust ' + ADDRESS,
                                          'connect ' + ADDRESS, 'info ' + ADDRESS])
        self.assertEqual(self.events[-1]['event'], 'complete')
        self.assertEqual(self.events[-1]['status'], 'PASS')
        self.assertIsNotNone(session.process.returncode)
        self.assertEqual(session.process.args[-2:], ['--agent', 'KeyboardDisplay'])

    def test_connect_does_not_pair_or_trust_again(self):
        self.session().run(ADDRESS, pair=False)
        self.assertEqual(self.commands(), ['connect ' + ADDRESS, 'info ' + ADDRESS])

    def test_partial_prompt_and_code_input_are_handled_without_secret_events(self):
        for scenario, answer, expected_kind in (('pin', '0000', 'pin'),
                ('passkey', '123456', 'passkey'), ('confirm', 'yes', 'confirm'),
                ('authorize', 'yes', 'confirm')):
            with self.subTest(scenario=scenario):
                self.events.clear()
                self.log.unlink(missing_ok=True)
                self.session(scenario, answer).run(ADDRESS)
                prompts = [event for event in self.events if event['event'] == 'prompt']
                self.assertEqual(len(prompts), 1)
                self.assertEqual(prompts[0]['kind'], expected_kind)
                self.assertIn('ANSWER:' + answer, self.commands())
                if scenario == 'pin':
                    self.assertNotIn('0000', json.dumps(self.events))

    def test_decline_never_trusts_or_connects(self):
        with self.assertRaisesRegex(pairing.PairingError, 'declined'):
            self.session('confirm', 'no').run(ADDRESS)
        self.assertEqual(self.commands()[0], 'pair ' + ADDRESS)
        self.assertFalse(any(row.startswith(('trust ', 'connect ')) for row in self.commands()))
        self.assertFalse(any(event['event'] == 'complete' for event in self.events))

    def test_wrong_prompt_id_cannot_answer_or_advance_pairing(self):
        with self.assertRaisesRegex(pairing.PairingError, 'active request'):
            self.session('pin', lambda _: dict(id='stale', value='0000')).run(ADDRESS)
        self.assertEqual(self.commands(), ['pair ' + ADDRESS])

    def test_pin_control_characters_and_oversized_input_are_not_forwarded(self):
        for value in ('bad\nconnect ' + ADDRESS, '0' * 17, '\x1b', ''):
            with self.subTest(value=repr(value)):
                self.log.unlink(missing_ok=True)
                with self.assertRaises(pairing.PairingError):
                    self.session('pin', value).run(ADDRESS)
                self.assertEqual(self.commands(), ['pair ' + ADDRESS])
                self.assertNotIn(value, json.dumps(self.events)) if value else None

    def test_passkey_requires_ascii_digits_within_six_digit_limit(self):
        for value in ('1000000', '-1', '12a', '１２３', ''):
            with self.subTest(value=value):
                self.log.unlink(missing_ok=True)
                with self.assertRaises(pairing.PairingError):
                    self.session('passkey', value).run(ADDRESS)
                self.assertEqual(self.commands(), ['pair ' + ADDRESS])

    def test_confirmation_requires_explicit_yes_or_no(self):
        with self.assertRaisesRegex(pairing.PairingError, 'Yes or No'):
            self.session('confirm', 'true').run(ADDRESS)
        self.assertEqual(self.commands(), ['pair ' + ADDRESS])

    def test_no_automatic_confirmation_and_bounded_prompt_timeout(self):
        with self.assertRaisesRegex(pairing.PairingError, 'not answered'):
            self.session('confirm').run(ADDRESS)
        self.assertEqual(self.commands(), ['pair ' + ADDRESS])

    def test_failure_never_advances_to_trust_or_connect(self):
        with self.assertRaisesRegex(pairing.PairingError, 'authentication failed'):
            self.session('failed').run(ADDRESS)
        self.assertEqual(self.commands(), ['pair ' + ADDRESS])

    def test_device_prompt_prefixed_failure_is_reported_without_waiting_for_timeout(self):
        with self.assertRaisesRegex(pairing.PairingError, 'authentication failed'):
            self.session('prefixed-failed').run(ADDRESS)
        self.assertEqual(self.commands(), ['pair ' + ADDRESS])
        self.assertEqual(self.events[-1]['code'], 'AuthenticationFailed')

    def test_success_messages_cannot_override_negative_info_readback(self):
        with self.assertRaisesRegex(pairing.PairingError, 'without a confirmed'):
            self.session('false-readback').run(ADDRESS)
        self.assertFalse(any(event['event'] == 'complete' for event in self.events))

    def test_property_notifications_without_info_header_do_not_count_as_readback(self):
        with self.assertRaisesRegex(pairing.PairingError, 'timed out'):
            self.session('no-info-header').run(ADDRESS)

    def test_already_paired_is_only_accepted_after_final_readback(self):
        self.assertTrue(self.session('already-paired').run(ADDRESS)['connected'])
        self.assertEqual(len(self.commands()), 4)

    def test_display_code_is_presented_for_entry_on_the_device(self):
        self.session('display').run(ADDRESS)
        self.assertTrue(any(event['event'] == 'message' and '1234' in event['message']
                            for event in self.events))

    def test_dead_client_is_reported_before_completion(self):
        with self.assertRaisesRegex(pairing.PairingError, 'exited'):
            self.session('early-exit').run(ADDRESS)
        self.assertFalse(any(event['event'] == 'complete' for event in self.events))

    def test_registration_timeout_cleans_client_and_descendants(self):
        session = self.session('orphan')
        with self.assertRaisesRegex(pairing.PairingError, 'register'):
            session.run(ADDRESS)
        self.assertIsNotNone(session.process.returncode)
        child = int(self.child_pid.read_text())
        state = Path('/proc') / str(child) / 'stat'
        # A terminated child may await init's reaper briefly; it must not run.
        try:
            child_state = state.read_text().split()[2]
        except FileNotFoundError:
            child_state = None
        self.assertIn(child_state, (None, 'Z'))

    def test_invalid_address_never_launches_client(self):
        with patch.object(subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(pairing.PairingError, 'address'):
                self.session().run('00:11:22:33:44:55\nquit')
            popen.assert_not_called()

    def test_cli_power_preparation_failure_does_not_launch_agent(self):
        events = []
        with patch.object(sys, 'argv', ['surface-bluetooth-pair', '--pair', ADDRESS]), \
                patch.object(pairing, 'json_event', side_effect=events.append), \
                patch.object(pairing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'private')) as run, \
                patch.object(pairing, 'PairingSession') as session:
            self.assertEqual(pairing.main(), 1)
        session.assert_not_called()
        self.assertEqual(run.call_args_list[0].args[0], [pairing.SERVICE, '--power-on'])
        self.assertEqual(run.call_args_list[-1].args[0],
                         [pairing.SERVICE, '--record-error', 'pairing-session-adapter-not-ready'])
        self.assertNotIn('private', json.dumps(events))


if __name__ == '__main__':
    unittest.main()
