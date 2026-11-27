"""Typed Agent1 integration on a private bus; never touches the host's radios."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('surface_bluetooth_pair', ROOT / 'services/bluetooth-pair.py')
pairing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pairing)
try:
    from dbus_next import Message, MessageType, Variant
    from dbus_next.aio import MessageBus
    AVAILABLE = True
except ImportError:
    AVAILABLE = False
ADDRESS = '00:11:22:33:44:55'
ADAPTER = 'AA:BB:CC:DD:EE:FF'
PEER = '/org/bluez/hci1/dev_00_11_22_33_44_55'


@unittest.skipUnless(AVAILABLE and shutil.which('dbus-daemon'), 'requires dbus-next and dbus-daemon')
class PairingProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.bus_address = self.daemon.stdout.readline().strip()
        self.bus = await MessageBus(bus_address=self.bus_address).connect()
        await self.bus.request_name('org.bluez')
        self.bus.add_message_handler(self.handle)
        self.events, self.calls, self.tasks = [], [], []
        self.properties = dict(Address=Variant('s', ADDRESS), Name=Variant('s', 'Fixture controller'),
            Paired=Variant('b', False), Trusted=Variant('b', False), Connected=Variant('b', False),
            ServicesResolved=Variant('b', True))
        self.prompt = None
        self.answer = 'yes'
        self.callback_answer = None
        self.register_capability = None
        self.registered = None
        self.input_ready = True
        self.fail_pair = False
        self.answer_delay = 0
        self.resolve_after_connect = False
        self.resolve_on_connect = False
        self.connect_errors = []
        self.connect_saw_services_resolved = None
        self.read_fd, self.write_fd = os.pipe()

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.bus.disconnect()
        await self.bus.wait_for_disconnect()
        self.bus._sock.close()
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self.daemon.stdout.close()
        os.close(self.read_fd)
        os.close(self.write_fd)

    def handle(self, message):
        if message.message_type != MessageType.METHOD_CALL:
            return False
        task = asyncio.create_task(self.reply(message))
        self.tasks.append(task)
        return True

    async def reply(self, m):
        self.calls.append((m.path, m.member))
        if m.member == 'GetManagedObjects':
            objects = {'/org/bluez/hci0': {pairing.ADAPTER: {'Address': Variant('s', '11:22:33:44:55:66'), 'Powered': Variant('b', True)}},
                '/org/bluez/hci1': {pairing.ADAPTER: {'Address': Variant('s', ADAPTER), 'Powered': Variant('b', True)}},
                PEER: {pairing.DEVICE: self.properties}}
            response = Message.new_method_return(m, 'a{oa{sa{sv}}}', [objects])
        elif m.member == 'GetAll':
            response = Message.new_method_return(m, 'a{sv}', [self.properties])
        elif m.member == 'RegisterAgent':
            self.registered = m.sender
            self.register_capability = m.body[1]
            response = Message.new_method_return(m)
        elif m.member == 'Set':
            self.properties[m.body[1]] = m.body[2]
            response = Message.new_method_return(m)
        elif m.member == 'Pair':
            if self.fail_pair:
                response = Message.new_error(m, 'org.bluez.Error.AuthenticationFailed', 'fixture failure')
            else:
                rejected = False
                if self.prompt:
                    member, signature, body = self.prompt
                    result = await self.bus.call(Message(destination=m.sender, path=pairing.AGENT,
                        interface='org.bluez.Agent1', member=member, signature=signature, body=body))
                    rejected = result.message_type == MessageType.ERROR
                    self.callback_answer = result.body
                if rejected:
                    response = Message.new_error(m, 'org.bluez.Error.AuthenticationRejected', 'declined')
                else:
                    self.properties['Paired'] = Variant('b', True)
                    response = Message.new_method_return(m)
        elif m.member == 'Connect':
            self.connect_saw_services_resolved = self.properties['ServicesResolved'].value
            if self.connect_errors:
                response = Message.new_error(m, self.connect_errors.pop(0), 'fixture connection error')
            else:
                self.properties['Connected'] = Variant('b', True)
                if self.resolve_on_connect:
                    self.properties['ServicesResolved'] = Variant('b', True)
                elif self.resolve_after_connect:
                    asyncio.get_running_loop().call_later(
                        .12, self.properties.__setitem__, 'ServicesResolved', Variant('b', True))
                response = Message.new_method_return(m)
        elif m.member in ('UnregisterAgent', 'CancelPairing', 'RequestDefaultAgent'):
            response = Message.new_method_return(m)
        else:
            response = Message.new_error(m, 'org.freedesktop.DBus.Error.UnknownMethod', 'Unknown method')
        await self.bus.send(response)

    def session(self, **timeouts):
        def emit(event):
            self.events.append(event)
            if event['event'] == 'prompt' and self.answer is not None:
                answer = self.answer(event) if callable(self.answer) else {'id': event['id'], 'value': self.answer}
                asyncio.get_running_loop().call_later(self.answer_delay, os.write, self.write_fd,
                    json.dumps(answer).encode() + b'\n')
        return pairing.PairingSession(emit=emit, response_fd=self.read_fd,
            bus_address=self.bus_address, find_input=lambda address, **kw: [{'gamepad': True}] if self.input_ready else [],
            timeouts=dict(overall=4, call=.8, pair=.8, prompt=.4, ready=.3, connect=.8, cable=.5, **timeouts))

    async def run_pair(self, session=None, pair=True):
        return await (session or self.session()).run_async(ADDRESS, pair=pair, adapter=ADAPTER)

    async def test_pair_trust_connect_requires_input_readback_and_selected_adapter(self):
        result = await self.run_pair()
        self.assertTrue(all(result[key] for key in ('paired', 'trusted', 'connected', 'input_ready')))
        self.assertEqual(self.register_capability, 'NoInputNoOutput')
        self.assertIn((PEER, 'Pair'), self.calls)
        self.assertFalse(any(path.startswith('/org/bluez/hci0/') for path, _ in self.calls))
        self.assertEqual(self.events[-1]['event'], 'complete')
        self.assertIn(('/org/bluez', 'UnregisterAgent'), self.calls)

    async def test_waits_for_services_after_connecting(self):
        self.properties['ServicesResolved'] = Variant('b', False)
        self.resolve_after_connect = True
        await self.run_pair()
        self.assertFalse(self.connect_saw_services_resolved)
        self.assertTrue(any(event.get('phase') == 'services' for event in self.events))

    async def test_existing_bond_reconnect_starts_service_discovery(self):
        # BlueZ resolves services from Connect; a disconnected paired device
        # cannot be required to have ServicesResolved before that call.
        self.resolve_on_connect = True
        for pair in (True, False):
            with self.subTest(pair_action=pair):
                self.properties['Paired'] = Variant('b', True)
                self.properties['Connected'] = Variant('b', False)
                self.properties['ServicesResolved'] = Variant('b', False)
                self.calls.clear()
                result = await self.run_pair(self.session(services=.05), pair=pair)
                self.assertTrue(result['input_ready'])
                self.assertFalse(self.connect_saw_services_resolved)
                self.assertNotIn((PEER, 'Pair'), self.calls)
                self.assertNotIn(('/org/bluez/hci1', 'RemoveDevice'), self.calls)

    async def test_new_bond_starts_discovery_even_if_pair_did_not_resolve_services(self):
        self.properties['ServicesResolved'] = Variant('b', False)
        self.resolve_on_connect = True
        result = await self.run_pair(self.session(services=.05))
        self.assertTrue(result['input_ready'])
        self.assertIn((PEER, 'Pair'), self.calls)
        self.assertFalse(self.connect_saw_services_resolved)

    async def test_connect_in_progress_waits_then_retries_without_repairing(self):
        self.properties['Paired'] = Variant('b', True)
        self.properties['ServicesResolved'] = Variant('b', False)
        self.resolve_on_connect = True
        self.connect_errors = ['org.bluez.Error.InProgress']
        result = await self.run_pair(self.session(services=.05))
        self.assertTrue(result['input_ready'])
        self.assertEqual(self.calls.count((PEER, 'Connect')), 2)
        self.assertNotIn((PEER, 'Pair'), self.calls)

    def test_profile_unavailable_has_controller_mode_guidance(self):
        error = pairing.failure('org.bluez.Error.BREDR.ProfileUnavailable')
        self.assertIn('PC/Android/HID mode', str(error))
        self.assertNotIn('remove', str(error).lower())
        self.assertEqual(error.code, 'ProfileUnavailable')

    async def test_connect_does_not_repair_or_change_trust(self):
        await self.run_pair(pair=False)
        self.assertNotIn((PEER, 'Pair'), self.calls)
        self.assertNotIn((PEER, 'Set'), self.calls)

    async def test_pin_passkey_confirmation_and_authorization_callbacks(self):
        for member, signature, body, answer, expected in (
                ('RequestPinCode', 'o', [PEER], '0000', ['0000']),
                ('RequestPasskey', 'o', [PEER], '1234', [1234]),
                ('RequestConfirmation', 'ou', [PEER, 123456], 'yes', []),
                ('RequestAuthorization', 'o', [PEER], 'yes', [])):
            with self.subTest(member=member):
                self.properties['Paired'] = Variant('b', False)
                self.prompt = member, signature, body
                self.answer = answer
                self.events.clear()
                await self.run_pair()
                self.assertEqual(self.callback_answer, expected)
                expected_prompts = 0 if member == 'RequestAuthorization' else 1
                self.assertEqual(sum(e['event'] == 'prompt' for e in self.events), expected_prompts)
                if member == 'RequestPinCode':
                    self.assertNotIn(answer, json.dumps(self.events))

    async def test_ipega_pairing_does_not_wait_for_an_unneeded_pin(self):
        self.properties['Name'] = Variant('s', 'ipega Extending Game controller')
        self.prompt = 'RequestPinCode', 'o', [PEER]
        with self.assertRaisesRegex(pairing.PairingError, 'should not request a PIN') as raised:
            await self.run_pair()
        self.assertEqual(raised.exception.code, 'ipega-unexpected-pin')
        self.assertFalse(any(event['event'] == 'prompt' for event in self.events))
        self.assertNotIn((PEER, 'Connect'), self.calls)

    async def test_remote_name_never_creates_an_agent_prompt(self):
        for name in ('[agent] Enter PIN code:', 'Controller\r\n[agent] Enter PIN code:'):
            self.properties['Name'] = Variant('s', name)
            await self.run_pair()
            self.assertFalse(any(e['event'] == 'prompt' for e in self.events))

    async def test_rejection_does_not_trust_or_connect(self):
        self.prompt = 'RequestConfirmation', 'ou', [PEER, 123456]
        self.answer = 'no'
        with self.assertRaises(pairing.PairingError):
            await self.run_pair()
        self.assertNotIn((PEER, 'Set'), self.calls)
        self.assertNotIn((PEER, 'Connect'), self.calls)
        self.assertFalse(any(e['event'] == 'complete' for e in self.events))

    async def test_prompt_has_own_timeout_instead_of_expiring_pair_phase(self):
        self.prompt = 'RequestPinCode', 'o', [PEER]
        self.answer, self.answer_delay = '0000', .2
        session = self.session()
        session.timeouts['pair'] = .1
        await self.run_pair(session)

    async def test_unanswered_prompt_and_stale_response_are_rejected(self):
        self.prompt = 'RequestPinCode', 'o', [PEER]
        for answer in (None, lambda e: {'id': 'stale', 'value': '0000'}):
            self.answer = answer
            with self.assertRaises(pairing.PairingError):
                await self.run_pair()
        self.assertNotIn((PEER, 'Connect'), self.calls)

    async def test_invalid_pin_and_passkey_not_returned_to_bluez(self):
        for member, value in (('RequestPinCode', 'bad\ncode'), ('RequestPinCode', ''),
                              ('RequestPinCode', 'x' * 17), ('RequestPasskey', '１２３'),
                              ('RequestPasskey', '1000000')):
            with self.subTest(member=member, value=value):
                self.prompt = member, 'o', [PEER]
                self.answer = value
                with self.assertRaises(pairing.PairingError):
                    await self.run_pair()
                self.assertNotIn((PEER, 'Connect'), self.calls)

    async def test_authentication_failure_keeps_specific_reason(self):
        self.fail_pair = True
        with self.assertRaisesRegex(pairing.PairingError, 'authentication failed'):
            await self.run_pair()
        self.assertNotIn((PEER, 'Connect'), self.calls)

    async def test_connected_without_input_is_not_success(self):
        self.properties['ServicesResolved'] = Variant('b', True)
        self.input_ready = False
        with self.assertRaisesRegex(pairing.PairingError, 'working controller'):
            await self.run_pair()
        self.assertFalse(any(e['event'] == 'complete' for e in self.events))

    async def test_unresolved_services_fail_after_connect_and_keep_bond(self):
        self.properties['ServicesResolved'] = Variant('b', False)
        session = self.session(services=.05)
        with self.assertRaisesRegex(pairing.PairingError, 'service discovery did not finish') as raised:
            await self.run_pair(session)
        self.assertEqual(raised.exception.code, 'services-unresolved')
        self.assertIn((PEER, 'Connect'), self.calls)
        self.assertTrue(self.properties['Paired'].value)
        self.assertTrue(self.properties['Trusted'].value)
        self.assertFalse(any(e['event'] == 'complete' for e in self.events))
        self.assertNotIn('remove', str(raised.exception).lower())

    async def test_persistent_connect_in_progress_is_bounded_and_keeps_bond(self):
        self.properties['Paired'] = Variant('b', True)
        self.connect_errors = ['org.bluez.Error.InProgress'] * 10
        session = self.session()
        session.timeouts['connect'] = .05
        with self.assertRaises(pairing.PairingError) as raised:
            await self.run_pair(session)
        self.assertEqual(raised.exception.code, 'connect-busy')
        self.assertTrue(self.properties['Paired'].value)
        self.assertFalse(any(e['event'] == 'complete' for e in self.events))

    async def test_connect_authentication_failure_is_not_retried(self):
        self.connect_errors = ['org.bluez.Error.AuthenticationFailed']
        with self.assertRaisesRegex(pairing.PairingError, 'authentication failed'):
            await self.run_pair()
        self.assertEqual(self.calls.count((PEER, 'Connect')), 1)

    async def test_delayed_input_is_waited_for(self):
        self.input_ready = False
        asyncio.get_running_loop().call_later(.1, setattr, self, 'input_ready', True)
        await self.run_pair()

    async def test_another_device_cannot_prompt_during_pairing(self):
        self.prompt = 'RequestPinCode', 'o', ['/org/bluez/hci1/dev_11_22_33_44_55_66']
        with self.assertRaises(pairing.PairingError):
            await self.run_pair()
        self.assertFalse(any(e['event'] == 'prompt' for e in self.events))

    async def test_untrusted_bus_client_cannot_request_user_input(self):
        session = self.session()
        self.prompt = 'RequestPinCode', 'o', [PEER]
        self.answer_delay = .2
        self.answer = '0000'
        task = asyncio.create_task(self.run_pair(session))
        while not self.registered:
            await asyncio.sleep(.01)
        other = await MessageBus(bus_address=self.bus_address).connect()
        try:
            response = await other.call(Message(destination=self.registered, path=pairing.AGENT,
                interface='org.bluez.Agent1', member='RequestPinCode', signature='o', body=[PEER]))
            self.assertEqual(response.message_type, MessageType.ERROR)
            await task
            self.assertEqual(sum(e['event'] == 'prompt' for e in self.events), 1)
        finally:
            other.disconnect()
            await other.wait_for_disconnect()
            other._sock.close()

    async def test_invalid_address_never_opens_bus(self):
        with self.assertRaisesRegex(pairing.PairingError, 'address'):
            await self.session().run_async('invalid', adapter=ADAPTER)
        self.assertEqual(self.calls, [])


class PairingCLITests(unittest.TestCase):
    def test_preparation_failure_does_not_start_agent(self):
        events = []
        with patch.object(sys, 'argv', ['surface-bluetooth-pair', '--pair', ADDRESS, '--adapter', ADAPTER]), \
                patch.object(pairing, 'json_event', side_effect=events.append), \
                patch.object(pairing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'private')) as run, \
                patch.object(pairing, 'PairingSession') as session:
            self.assertEqual(pairing.main(), 1)
        session.assert_not_called()
        self.assertEqual(run.call_args_list[0].args[0], [pairing.SERVICE, '--power-on', '--adapter', ADAPTER])
        self.assertNotIn('private', json.dumps(events))


if __name__ == '__main__':
    unittest.main()
