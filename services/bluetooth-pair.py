#!/usr/bin/python3
"""Pair controllers through typed BlueZ D-Bus calls and explicit JSON questions.

Device names and diagnostic text are never interpreted as agent requests.
Only the current org.bluez bus owner may call this session's Agent1 object.
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

ADDRESS = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z')
SERVICE = '/usr/sbin/surface-bluetooth'
AGENT = '/org/surface/PairingAgent'
DEVICE = 'org.bluez.Device1'
ADAPTER = 'org.bluez.Adapter1'
MANAGER = 'org.bluez.AgentManager1'
PROPERTIES = 'org.freedesktop.DBus.Properties'
CLASSIC_HID_UUID = '00001124-0000-1000-8000-00805f9b34fb'
HID_UUIDS = {CLASSIC_HID_UUID, '00001812-0000-1000-8000-00805f9b34fb'}
MAX_RESPONSE = 2048


class PairingError(ValueError):
    def __init__(self, message, code='failed'):
        super().__init__(message)
        self.code = code


def json_event(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def failure(code):
    code = code.rsplit('.', 1)[-1]
    messages = {
        'AuthenticationFailed': 'Bluetooth authentication failed. Put the device back into pairing mode and try again',
        'AuthenticationRejected': 'The Bluetooth device rejected pairing',
        'AuthenticationCanceled': 'Bluetooth pairing was canceled',
        'AuthenticationTimeout': 'The Bluetooth device did not answer the pairing request',
        'NotReady': 'The Bluetooth adapter is not ready',
        'ConnectionAttemptFailed': 'The Bluetooth device did not accept the connection',
        'ProfileUnavailable': ('Bluetooth paired, but the controller did not expose a connectable input profile. '
                               'Keep its pairing saved, wake it in PC/Android/HID mode, and reconnect'),
        'NotAvailable': 'The Bluetooth device is no longer available. Scan again',
        'Rejected': 'Bluetooth pairing was declined',
    }
    return PairingError(messages.get(code, 'Bluetooth could not complete the request (' + code + ')'), code)


def input_probe():
    path = Path('/usr/libexec/surface/bluetooth-input.py')
    spec = importlib.util.spec_from_file_location('surface_bt_input', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.find_input


class PairingSession:
    def __init__(self, emit=json_event, response_fd=0, timeouts=None, bus_address=None,
                 find_input=None):
        self.emit, self.response_fd = emit, response_fd
        self.timeouts = dict(overall=220, call=12, pair=60, connect=35, prompt=60, ready=15,
                             services=20, cable=120, profile_grace=2)
        self.timeouts.update(timeouts or {})
        self.bus_address, self.find_input = bus_address, find_input
        self.pending = None
        self.responses = b''
        self.prompt_count = 0
        self.tasks = set()
        self.target = None
        self.target_name = ''
        self.adapter_path = None
        self.cable = False
        self.cable_authorized = False
        self.reader_closed = False
        self.state = 'register'

    def event(self, event, message, **fields):
        self.emit(dict(event=event, message=message, **fields))

    def response(self, line):
        try:
            answer = json.loads(line)
        except (ValueError, UnicodeError):
            raise PairingError('The pairing response was invalid', 'invalid-response')
        if (not isinstance(answer, dict) or set(answer) != {'id', 'value'} or
                not self.pending or answer.get('id') != self.pending['id'] or
                not isinstance(answer.get('value'), str)):
            raise PairingError('The pairing response did not match the active request', 'invalid-response')
        value, kind = answer['value'], self.pending['kind']
        if kind == 'confirm':
            if value not in ('yes', 'no'):
                raise PairingError('Choose Yes or No to confirm Bluetooth pairing', 'invalid-response')
        elif kind == 'passkey':
            if not re.fullmatch('[0-9]{1,6}', value):
                raise PairingError('Bluetooth passkeys must contain one to six digits', 'invalid-response')
        elif (not 1 <= len(value.encode('utf-8')) <= 16 or
              any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise PairingError('Bluetooth PINs must contain 1 to 16 bytes without control characters', 'invalid-response')
        if not self.pending['future'].done():
            self.pending['future'].set_result(value)

    def read_response(self):
        try:
            chunk = os.read(self.response_fd, MAX_RESPONSE + 1)
            if not chunk:
                asyncio.get_running_loop().remove_reader(self.response_fd)
                self.reader_closed = True
                if self.pending:
                    raise PairingError('The Bluetooth pairing question was closed', 'canceled')
                return
            self.responses += chunk
            if len(self.responses) > MAX_RESPONSE:
                raise PairingError('The pairing response was too large', 'invalid-response')
            while b'\n' in self.responses:
                line, self.responses = self.responses.split(b'\n', 1)
                self.response(line)
        except (OSError, ValueError, UnicodeError) as error:
            self.abort = error if isinstance(error, PairingError) else PairingError('Invalid pairing input', 'invalid-response')
            if self.pending and not self.pending['future'].done():
                self.pending['future'].set_exception(self.abort)

    async def question(self, kind, message, code=None):
        if self.pending or self.reader_closed:
            raise PairingError('The Bluetooth pairing question is unavailable', 'canceled')
        self.prompt_count += 1
        future = asyncio.get_running_loop().create_future()
        self.pending = dict(id=str(self.prompt_count), kind=kind, future=future)
        fields = dict(id=self.pending['id'], kind=kind)
        if code is not None:
            fields['code'] = code
        self.event('prompt', message, **fields)
        try:
            value = await asyncio.wait_for(future, self.timeouts['prompt'])
            if kind == 'confirm' and value == 'no':
                raise PairingError('Bluetooth pairing was declined', 'rejected')
            return value
        except asyncio.TimeoutError:
            raise PairingError('The Bluetooth pairing question was not answered in time', 'timeout')
        finally:
            self.pending = None
            self.event('prompt-ended', 'Bluetooth pairing question closed')

    async def call(self, path, interface, member, signature='', body=None, seconds=None):
        message = self.Message(destination=self.owner, path=path, interface=interface,
                               member=member, signature=signature, body=body or [])
        task = asyncio.create_task(self.bus.call(message))
        remaining = self.timeouts['call'] if seconds is None else seconds
        try:
            # Prompt time is separate from the operation deadline. The global
            # deadline still bounds the entire session, including all questions.
            while not task.done():
                if self.abort:
                    raise self.abort
                start = asyncio.get_running_loop().time()
                await asyncio.wait({task}, timeout=min(.05, max(0, remaining)))
                if self.pending is None:
                    remaining -= asyncio.get_running_loop().time() - start
                if remaining <= 0 and not task.done():
                    raise PairingError('Bluetooth timed out during the ' + self.state + ' step', 'timeout')
            reply = task.result()
            if reply.message_type == self.MessageType.ERROR:
                if self.abort:
                    raise self.abort
                raise failure(reply.error_name)
            return reply.body
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def incoming(self, message):
        if message.message_type != self.MessageType.METHOD_CALL or message.path != AGENT:
            return False
        if message.sender != self.owner or message.interface != 'org.bluez.Agent1':
            return self.Message.new_error(message, 'org.bluez.Error.Rejected', 'Not authorized')
        signatures = {'Release': '', 'Cancel': '', 'RequestPinCode': 'o',
                      'RequestPasskey': 'o', 'RequestConfirmation': 'ou',
                      'RequestAuthorization': 'o', 'AuthorizeService': 'os',
                      'DisplayPinCode': 'os', 'DisplayPasskey': 'ouq'}
        if signatures.get(message.member) != message.signature:
            return self.Message.new_error(message, 'org.bluez.Error.Rejected', 'Invalid agent request')
        task = asyncio.create_task(self.answer_agent(message))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def answer_agent(self, message):
        try:
            member, args = message.member, message.body
            if member in ('Cancel', 'Release'):
                if self.pending and not self.pending['future'].done():
                    self.pending['future'].set_exception(PairingError('Bluetooth pairing was canceled', 'canceled'))
                if member == 'Release':
                    self.abort = PairingError('The Bluetooth agent was released', 'agent-released')
                result, signature = [], ''
            else:
                peer = str(args[0])
                if self.cable and self.target is None and member == 'RequestAuthorization':
                    if not re.fullmatch(re.escape(self.adapter_path) + r'/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}', peer):
                        raise PairingError('Device is on another Bluetooth adapter', 'rejected')
                    self.target = peer
                if peer != self.target:
                    raise PairingError('The pairing request is for another device', 'rejected')
                result, signature = [], ''
                if self.state == 'input-profile' and member in ('RequestPinCode', 'RequestPasskey', 'RequestConfirmation'):
                    raise PairingError('The saved controller pairing could not authenticate its input profile. '
                                       'The saved pairing was kept', 'input-profile-authentication')
                if member == 'RequestPinCode':
                    if 'ipega' in self.target_name.casefold():
                        raise PairingError('This iPega controller mode should not request a PIN. Power it off, '
                                           'forget its old pairing, then hold X + HOME to pair in gamepad mode',
                                           'ipega-unexpected-pin')
                    result, signature = [await self.question('pin', 'Enter the PIN shown by the Bluetooth device')], 's'
                elif member == 'RequestPasskey':
                    result, signature = [int(await self.question('passkey', 'Enter the passkey shown by the Bluetooth device'))], 'u'
                elif member == 'RequestConfirmation':
                    code = f'{args[1]:06d}'
                    await self.question('confirm', 'Does the device show passkey ' + code + '?', code)
                elif member in ('RequestAuthorization', 'AuthorizeService'):
                    if member == 'AuthorizeService' and str(args[1]).lower() not in HID_UUIDS:
                        raise PairingError('This panel only authorizes Bluetooth input devices', 'rejected')
                    if self.cable:
                        self.cable_authorized = True
                        self.event('message', 'Cable pairing authorized. Unplug the controller and press its PS button to test Bluetooth')
                elif member in ('DisplayPinCode', 'DisplayPasskey'):
                    value = str(args[1]) if member == 'DisplayPinCode' else f'{args[1]:06d}'
                    if not re.fullmatch(r'[ -~]{1,16}', value):
                        raise PairingError('The device provided an invalid pairing code', 'rejected')
                    self.event('message', 'Enter code ' + value + ' on the Bluetooth device', phase=self.state)
            await self.bus.send(self.Message.new_method_return(message, signature, result))
        except (PairingError, asyncio.CancelledError) as error:
            if isinstance(error, PairingError) and error.code not in ('rejected',):
                self.abort = error
            await self.bus.send(self.Message.new_error(message, 'org.bluez.Error.Rejected', 'Pairing request declined'))

    async def properties(self):
        result = await self.call(self.target, PROPERTIES, 'GetAll', 's', [DEVICE])
        return {str(k): v.value for k, v in result[0].items()}

    async def wait_for_services(self):
        """Wait for SDP/GATT resolution after Connect has initiated discovery."""
        deadline = asyncio.get_running_loop().time() + self.timeouts['services']
        while True:
            properties = await self.properties()
            if properties.get('ServicesResolved'):
                return properties
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise PairingError('Controller service discovery did not finish. The saved pairing was kept. '
                                   'Wake the controller in PC/Android/HID mode and reconnect',
                                   'services-unresolved')
            await asyncio.sleep(min(.2, remaining))

    async def connect(self):
        """Let BlueZ initiate discovery, retrying only an already active operation."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeouts['connect']
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PairingError('Bluetooth is still completing the controller connection. '
                                   'The saved pairing was kept; wake the controller and reconnect',
                                   'connect-busy')
            try:
                await self.call(self.target, DEVICE, 'Connect', seconds=remaining)
                return
            except PairingError as error:
                if error.code == 'AlreadyConnected':
                    return
                if error.code != 'InProgress':
                    raise
                # Pair() or an incoming controller connection can still have
                # SDP/GATT running. Do not delete the bond or interrupt that
                # operation with Disconnect; retry within one shared deadline.
                await asyncio.sleep(min(.2, max(0, deadline - loop.time())))

    async def operation(self, address, pair, adapter, cable):
        from dbus_next import BusType, Message, MessageType, Variant
        from dbus_next.aio import MessageBus
        self.Message, self.MessageType, self.Variant = Message, MessageType, Variant
        self.abort = None
        self.cable = cable
        self.bus = await MessageBus(bus_address=self.bus_address, bus_type=BusType.SYSTEM).connect()
        registered = False
        loop = asyncio.get_running_loop()
        try:
            reply = await asyncio.wait_for(self.bus.call(Message(destination='org.freedesktop.DBus',
                path='/org/freedesktop/DBus', interface='org.freedesktop.DBus', member='GetNameOwner',
                signature='s', body=['org.bluez'])), self.timeouts['call'])
            if reply.message_type == MessageType.ERROR:
                raise PairingError('The Bluetooth service is unavailable', 'service-unavailable')
            self.owner = reply.body[0]
            objects = (await self.call('/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects'))[0]
            available = [(str(path), props[ADAPTER]) for path, props in objects.items() if ADAPTER in props]
            chosen = next(((path, props) for path, props in available
                if (not adapter or str(props.get('Address', Variant('s', '')).value).upper() == adapter.upper())
                and props.get('Powered', Variant('b', False)).value), None)
            if not chosen:
                raise PairingError('The selected Bluetooth adapter is unavailable or powered off', 'adapter-not-ready')
            self.adapter_path = chosen[0]
            if not cable:
                self.target = self.adapter_path + '/dev_' + address.upper().replace(':', '_')
                if self.target not in objects or DEVICE not in objects[self.target]:
                    raise PairingError('The device is no longer available on this adapter. Scan again', 'NotAvailable')
            self.bus.add_message_handler(self.incoming)
            if self.response_fd is not None:
                loop.add_reader(self.response_fd, self.read_response)
            # A controller has no keyboard or display. Advertising these as
            # available can make legacy pads request a PIN the user cannot enter.
            await self.call('/org/bluez', MANAGER, 'RegisterAgent', 'os', [AGENT, 'NoInputNoOutput'])
            registered = True
            if cable:
                await self.call('/org/bluez', MANAGER, 'RequestDefaultAgent', 'o', [AGENT])
                self.state = 'cable'
                self.event('phase', 'Connect a supported PlayStation controller by USB, then approve cable pairing', phase='cable')
                deadline = loop.time() + self.timeouts['cable']
                while not self.cable_authorized:
                    if self.abort:
                        raise self.abort
                    if loop.time() >= deadline:
                        raise PairingError('Cable pairing timed out. Reconnect the controller USB cable and try again', 'timeout')
                    await asyncio.sleep(.1)
                address = self.target.rsplit('/dev_', 1)[1].replace('_', ':')
            elif pair:
                self.state = 'pair'
                self.event('phase', 'Pairing with the Bluetooth device', phase='pair')
                properties = await self.properties()
                self.target_name = str(properties.get('Name') or properties.get('Alias') or '')
                if not properties.get('Paired'):
                    try:
                        await self.call(self.target, DEVICE, 'Pair', seconds=self.timeouts['pair'])
                    except PairingError as error:
                        if error.code != 'AlreadyExists':
                            raise
                if not (await self.properties()).get('Paired'):
                    raise PairingError('Bluetooth did not confirm the pairing', 'readback-failed')
            if pair or cable:
                self.state = 'trust'
                await self.call(self.target, PROPERTIES, 'Set', 'ssv', [DEVICE, 'Trusted', Variant('b', True)])
            self.state = 'connect'
            if not cable:
                # ServicesResolved is normally false for a disconnected device,
                # even when paired. BlueZ Connect starts SDP/GATT itself; waiting
                # for resolution first would prevent reconnection indefinitely.
                self.event('phase', 'Connecting the Bluetooth device', phase='connect')
                await self.connect()
                if not (await self.properties()).get('ServicesResolved'):
                    self.state = 'services'
                    self.event('phase', 'Discovering the controller input profile', phase='services')
                    await self.wait_for_services()
            self.state = 'ready'
            self.event('phase', 'Waiting for the controller input device' if not cable else
                       'Unplug USB and press PS; waiting for wireless input', phase='ready')
            find = self.find_input or input_probe()
            deadline = loop.time() + (self.timeouts['cable'] if cable else self.timeouts['ready'])
            profile_after = loop.time() + self.timeouts['profile_grace']
            profile_attempted = False
            while True:
                if self.abort:
                    raise self.abort
                properties = await self.properties()
                connection = bool(properties.get('Connected'))
                resolved = bool(properties.get('ServicesResolved'))
                nodes = find(address, require_gamepad=True) if connection and resolved else []
                # Cable-configured devices may be Trusted without Paired until
                # their first wireless authentication; never assert Paired early.
                if nodes and connection and resolved and (not pair or properties.get('Paired')):
                    if (pair or cable) and not properties.get('Trusted'):
                        raise PairingError('Bluetooth did not retain controller trust', 'readback-failed')
                    result = dict(paired=bool(properties.get('Paired')), trusted=bool(properties.get('Trusted')),
                                  connected=True, input_ready=True, cable=cable)
                    self.event('complete', 'Bluetooth controller input is ready', status='PASS', **result)
                    return result
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise PairingError('Bluetooth did not create a working controller input device. Check its controller mode and reconnect', 'input-not-ready')
                # Device1.Connected can describe a link whose input profile was
                # never attached. After a short grace for asynchronous UHID and
                # udev creation, explicitly request its advertised classic HID
                # profile once. HOG/LE uses GATT and must not use ConnectProfile.
                uuids = properties.get('UUIDs', [])
                classic_hid = isinstance(uuids, (list, tuple)) and any(
                    isinstance(uuid, str) and uuid.casefold() == CLASSIC_HID_UUID for uuid in uuids)
                if (not cable and connection and resolved and not profile_attempted and
                        classic_hid and loop.time() >= profile_after):
                    profile_attempted = True
                    self.state = 'input-profile'
                    self.event('phase', 'Connecting the controller input profile', phase='input-profile')
                    try:
                        await self.call(self.target, DEVICE, 'ConnectProfile', 's', [CLASSIC_HID_UUID],
                                        seconds=min(self.timeouts['call'], remaining))
                    except PairingError as error:
                        if error.code not in ('InProgress', 'AlreadyConnected'):
                            raise PairingError('The controller input profile could not connect: ' + str(error),
                                               error.code) from error
                    finally:
                        self.state = 'ready'
                    # Re-read both BlueZ state and application-visible input.
                    # A successful D-Bus method alone never confirms readiness.
                    continue
                await asyncio.sleep(min(.2, remaining))
        finally:
            if self.response_fd is not None:
                loop.remove_reader(self.response_fd)
            if self.target and not cable:
                try:
                    await asyncio.wait_for(self.bus.call(Message(destination=self.owner, path=self.target,
                        interface=DEVICE, member='CancelPairing')), 1)
                except (Exception, asyncio.CancelledError):
                    pass
            if registered:
                try:
                    await asyncio.wait_for(self.bus.call(Message(destination=self.owner, path='/org/bluez',
                        interface=MANAGER, member='UnregisterAgent', signature='o', body=[AGENT])), 1)
                except (Exception, asyncio.CancelledError):
                    pass
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.bus.disconnect()
            await self.bus.wait_for_disconnect()
            # dbus-next 0.2.3 shuts down, but does not close its socket.
            self.bus._sock.close()
            self.responses = b''

    async def run_async(self, address=None, pair=True, adapter=None, cable=False):
        if ((not cable and (not isinstance(address, str) or not ADDRESS.fullmatch(address))) or
                (adapter is not None and not ADDRESS.fullmatch(adapter))):
            raise PairingError('The Bluetooth address is invalid', 'invalid-address')
        try:
            return await asyncio.wait_for(self.operation(address, pair and not cable, adapter, cable), self.timeouts['overall'])
        except asyncio.TimeoutError:
            raise PairingError('Bluetooth pairing timed out', 'timeout')

    def run(self, address=None, pair=True, adapter=None, cable=False):
        return asyncio.run(self.run_async(address, pair, adapter, cable))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--pair', metavar='ADDRESS')
    action.add_argument('--connect', metavar='ADDRESS')
    action.add_argument('--cable', action='store_true')
    parser.add_argument('--adapter', metavar='ADDRESS')
    options = parser.parse_args()
    def canceled(_signum, _frame):
        raise PairingError('Bluetooth pairing was canceled', 'canceled')
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, canceled)
    try:
        address = options.pair or options.connect
        if (not options.cable and not ADDRESS.fullmatch(address)) or (options.adapter and not ADDRESS.fullmatch(options.adapter)):
            raise PairingError('The Bluetooth address is invalid', 'invalid-address')
        json_event(dict(event='phase', message='Preparing the Bluetooth adapter', phase='power'))
        prepared = subprocess.run([SERVICE, '--power-on'] + (['--adapter', options.adapter] if options.adapter else []),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=45, check=False, env=dict(os.environ, LC_ALL='C'))
        if prepared.returncode:
            raise PairingError('The Bluetooth adapter could not be prepared. Check Bluetooth diagnostics', 'adapter-not-ready')
        PairingSession().run(address, pair=bool(options.pair), adapter=options.adapter, cable=options.cable)
        return 0
    except (PairingError, OSError, ImportError, subprocess.TimeoutExpired) as error:
        if not isinstance(error, PairingError):
            error = PairingError('The Bluetooth service could not be contacted', 'service-unavailable')
        try:
            subprocess.run([SERVICE, '--record-error', 'pairing-session-' + error.code[:48]],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        json_event(dict(event='error', message=str(error), code=error.code))
        return 1


if __name__ == '__main__':
    sys.exit(main())
