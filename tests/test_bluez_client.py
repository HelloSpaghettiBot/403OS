"""Actual image bluetoothctl against private D-Bus fixtures, never host radios.

Requires an existing x86_64 target build plus host dbus-python/PyGObject.
The ordinary source suite skips these integration checks when unavailable.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
except ImportError:
    dbus = None

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'output/target'
CLIENT = TARGET / 'usr/bin/bluetoothctl'
LOADER = TARGET / 'lib64/ld-linux-x86-64.so.2'
ADDRESS = '00:11:22:33:44:66'
ADAPTER_PATH = '/org/bluez/hci0'
DEVICE_PATH = ADAPTER_PATH + '/dev_' + ADDRESS.replace(':', '_')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


@unittest.skipUnless(dbus is not None and CLIENT.is_file() and LOADER.is_file()
                     and shutil.which('dbus-daemon'),
                     'requires built image client, dbus-python, PyGObject and dbus-daemon')
class BluezClientIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        cls.loop = GLib.MainLoop()
        cls.thread = threading.Thread(target=cls.loop.run, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.loop.quit()
        cls.thread.join(3)

    def setUp(self):
        self.daemon = subprocess.Popen(
            ['dbus-daemon', '--session', '--nofork', '--print-address=1'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_bus)
        address = self.daemon.stdout.readline().strip()
        self.assertTrue(address.startswith('unix:'))
        self.environment = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address,
                                LC_ALL='C', TERM='dumb')
        self.bus = dbus.bus.BusConnection(address)
        self.name = dbus.service.BusName('org.bluez', bus=self.bus)
        self.adapter_properties = {
            'Address': dbus.String('00:11:22:33:44:55'),
            'AddressType': dbus.String('public'), 'Alias': dbus.String('Fixture adapter'),
            'Name': dbus.String('Fixture adapter'), 'Powered': dbus.Boolean(True),
            'Pairable': dbus.Boolean(True), 'Discovering': dbus.Boolean(False)}
        self.device_properties = {
            'Address': dbus.String(ADDRESS), 'AddressType': dbus.String('public'),
            'Name': dbus.String('Fixture controller'), 'Alias': dbus.String('Fixture controller'),
            'Adapter': dbus.ObjectPath(ADAPTER_PATH), 'Paired': dbus.Boolean(False),
            'Trusted': dbus.Boolean(False), 'Connected': dbus.Boolean(False)}
        self.discovery_callback = None
        self.pair_requires_agent = False
        self.agents = {}
        self.agent_events = []
        self.live_agent_checked = False
        self.authentication_method = None
        self.authentication_value = None
        self.authentication_replies = []
        self.operation_events = []
        fixture = self
        objects = {dbus.ObjectPath(ADAPTER_PATH): {'org.bluez.Adapter1': self.adapter_properties},
                   dbus.ObjectPath(DEVICE_PATH): {'org.bluez.Device1': self.device_properties},
                   dbus.ObjectPath('/org/bluez'): {'org.bluez.AgentManager1': {}}}

        class Properties(dbus.service.Object):
            def __init__(self, bus, path, interface, properties):
                super().__init__(bus, path)
                self.interface, self.properties = interface, properties

            @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s',
                                 out_signature='a{sv}')
            def GetAll(self, interface):
                return self.properties if str(interface) == self.interface else {}

            @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ssv',
                                 out_signature='')
            def Set(self, interface, name, value):
                if str(interface) != self.interface:
                    raise dbus.exceptions.DBusException('Wrong interface',
                                name='org.freedesktop.DBus.Error.InvalidArgs')
                self.properties[str(name)] = value
                if str(interface) == 'org.bluez.Device1' and str(name) == 'Trusted':
                    fixture.operation_events.append('trust')
                self.PropertiesChanged(self.interface, {str(name): value}, [])

            @dbus.service.signal('org.freedesktop.DBus.Properties', signature='sa{sv}as')
            def PropertiesChanged(self, interface, changed, invalidated):
                pass

        class Adapter(Properties):
            @dbus.service.method('org.bluez.Adapter1', in_signature='', out_signature='')
            def StartDiscovery(self):
                self.properties['Discovering'] = dbus.Boolean(True)
                if fixture.discovery_callback is not None:
                    fixture.discovery_callback()

            @dbus.service.method('org.bluez.Adapter1', in_signature='', out_signature='')
            def StopDiscovery(self):
                self.properties['Discovering'] = dbus.Boolean(False)

            @dbus.service.method('org.bluez.Adapter1', in_signature='a{sv}', out_signature='')
            def SetDiscoveryFilter(self, properties):
                pass

            @dbus.service.method('org.bluez.Adapter1', in_signature='o', out_signature='')
            def RemoveDevice(self, path):
                del objects[path]

        class Device(Properties):
            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='',
                                 sender_keyword='sender', async_callbacks=('reply', 'error'))
            def Pair(self, reply, error, sender=None):
                fixture.operation_events.append('pair')
                def paired(*values):
                    if fixture.authentication_method == 'RequestPinCode':
                        if tuple(str(value) for value in values) != (fixture.authentication_value,):
                            error(dbus.exceptions.DBusException('Incorrect fixture PIN',
                                                                name='org.bluez.Error.AuthenticationFailed'))
                            return
                    if fixture.authentication_method == 'RequestPasskey':
                        if tuple(int(value) for value in values) != (fixture.authentication_value,):
                            error(dbus.exceptions.DBusException('Incorrect fixture passkey',
                                                                name='org.bluez.Error.AuthenticationFailed'))
                            return
                    fixture.authentication_replies.append(tuple(values))
                    self.properties['Paired'] = dbus.Boolean(True)
                    self.PropertiesChanged('org.bluez.Device1', {'Paired': dbus.Boolean(True)}, [])
                    reply()

                if not fixture.pair_requires_agent:
                    paired()
                    return
                fixture.agent_events.append(('pair', str(sender)))
                registered = fixture.agents.get(str(sender))
                if registered is None:
                    error(dbus.exceptions.DBusException('Pair requires an agent registered by its caller',
                                                        name='org.bluez.Error.AuthenticationFailed'))
                    return
                path, _ = registered

                def agent_ready(xml):
                    if 'org.bluez.Agent1' not in str(xml):
                        error(dbus.exceptions.DBusException('Registered agent is not exported',
                                                            name='org.bluez.Error.AuthenticationFailed'))
                        return
                    fixture.live_agent_checked = True
                    fixture.agent_events.append(('agent-live', str(sender)))
                    if fixture.authentication_method is None:
                        paired()
                        return
                    arguments = [dbus.ObjectPath(DEVICE_PATH)]
                    if fixture.authentication_method == 'RequestConfirmation':
                        arguments.append(dbus.UInt32(fixture.authentication_value))
                    method = getattr(agent, fixture.authentication_method)
                    method(*arguments, dbus_interface='org.bluez.Agent1',
                           reply_handler=paired, error_handler=error, timeout=5)

                # Introspection proves that the registered object remains alive
                # throughout Pair without accepting or inventing an auth reply.
                agent = fixture.bus.get_object(sender, path, introspect=False)
                agent.Introspect(dbus_interface='org.freedesktop.DBus.Introspectable',
                                 reply_handler=agent_ready, error_handler=error, timeout=1)

            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='')
            def Connect(self):
                fixture.operation_events.append('connect')
                self.properties['Connected'] = dbus.Boolean(True)
                self.PropertiesChanged('org.bluez.Device1', {'Connected': dbus.Boolean(True)}, [])

            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='')
            def Disconnect(self):
                self.properties['Connected'] = dbus.Boolean(False)
                self.PropertiesChanged('org.bluez.Device1', {'Connected': dbus.Boolean(False)}, [])

        class Manager(dbus.service.Object):
            @dbus.service.method('org.freedesktop.DBus.ObjectManager', in_signature='',
                                 out_signature='a{oa{sa{sv}}}')
            def GetManagedObjects(self):
                return objects

        class AgentManager(Properties):
            @dbus.service.method('org.bluez.AgentManager1', in_signature='os', out_signature='',
                                 sender_keyword='sender')
            def RegisterAgent(self, path, capability, sender=None):
                fixture.agents[str(sender)] = (str(path), str(capability))
                fixture.agent_events.append(('registered', str(sender)))

            @dbus.service.method('org.bluez.AgentManager1', in_signature='o', out_signature='',
                                 sender_keyword='sender')
            def UnregisterAgent(self, path, sender=None):
                fixture.agents.pop(str(sender), None)

            @dbus.service.method('org.bluez.AgentManager1', in_signature='o', out_signature='')
            def RequestDefaultAgent(self, path):
                pass

        # BlueZ's ObjectManager root is '/', while adapter paths start /org/bluez.
        self.manager = Manager(self.bus, '/')
        self.agent_manager = AgentManager(self.bus, '/org/bluez', 'org.bluez.AgentManager1', {})
        self.adapter = Adapter(self.bus, ADAPTER_PATH, 'org.bluez.Adapter1', self.adapter_properties)
        self.device = Device(self.bus, DEVICE_PATH, 'org.bluez.Device1', self.device_properties)

    def stop_bus(self):
        for attribute in ('manager', 'agent_manager', 'adapter', 'device'):
            if hasattr(self, attribute):
                getattr(self, attribute).remove_from_connection()
        if hasattr(self, 'name'):
            self.name = None
        if hasattr(self, 'bus'):
            self.bus.close()
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self.daemon.stdout.close()
        self.daemon.stderr.close()

    def command(self, *arguments):
        result = subprocess.run(self.client_argv() + list(arguments), env=self.environment,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        return ANSI.sub('', result.stdout)

    @staticmethod
    def client_argv():
        return [str(LOADER), '--library-path',
                str(TARGET / 'lib') + ':' + str(TARGET / 'usr/lib'), str(CLIENT)]

    def pairing_session(self, responses):
        # Import the helper's core directly so its real-image CLI guard is
        # retained in production while tests use only this private bus.
        spec = importlib.util.spec_from_file_location(
            'surface_bluetooth_pair_integration', ROOT / 'services/bluetooth-pair.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        events = []

        def receive(event):
            events.append(event)
            if event.get('event') == 'prompt':
                value = responses[event['kind']]
                os.write(writer, (json.dumps({'id': event['id'], 'value': value}) + '\n').encode())

        session = module.PairingSession(client_argv=self.client_argv(), env=self.environment,
                                        emit=receive, response_fd=reader)
        return module, session, events

    def test_noninteractive_list_and_show_report_actual_fixture_adapter(self):
        self.assertIn('Controller 00:11:22:33:44:55', self.command('list'))
        information = self.command('show')
        self.assertIn('Controller 00:11:22:33:44:55', information)
        self.assertRegex(information, r'Powered:\s+yes')

    def test_noninteractive_queries_without_agent_option_do_not_register_agent(self):
        self.assertIn('Controller 00:11:22:33:44:55', self.command('list'))
        self.assertIn('Device ' + ADDRESS + ' Fixture controller', self.command('devices'))
        self.assertEqual(self.agent_events, [])
        self.assertEqual(self.agents, {})

    def test_one_shot_agent_option_does_not_register_an_interactive_agent(self):
        # Document the actual BlueZ 5.86 behavior that requires our persistent
        # interactive pairing session. --agent alone does not register an
        # Agent1 when bluetoothctl also receives a positional command.
        self.pair_requires_agent = True
        output = self.command('--agent', 'NoInputNoOutput', '--timeout', '2', 'pair', ADDRESS)
        self.assertIn('Failed to pair: org.bluez.Error.AuthenticationFailed', output)
        self.assertNotIn('Agent registered', output)
        self.assertFalse(self.device_properties['Paired'])
        self.assertFalse(self.live_agent_checked)
        self.assertEqual([event[0] for event in self.agent_events], ['pair'])
        self.assertEqual(self.agents, {})

    def test_persistent_agent_answers_legacy_pin_then_trusts_and_connects(self):
        self.pair_requires_agent = True
        self.authentication_method = 'RequestPinCode'
        self.authentication_value = '0000'
        _, session, events = self.pairing_session({'pin': '0000'})
        result = session.run(ADDRESS, pair=True)
        self.assertEqual(result, {'paired': True, 'trusted': True, 'connected': True})
        self.assertEqual(self.operation_events, ['pair', 'trust', 'connect'])
        self.assertTrue(self.live_agent_checked)
        self.assertEqual(self.authentication_replies, [('0000',)])
        self.assertEqual([event['kind'] for event in events if event.get('event') == 'prompt'], ['pin'])
        self.assertEqual([event[0] for event in self.agent_events],
                         ['registered', 'pair', 'agent-live'])
        sender = self.agent_events[0][1]
        self.assertTrue(all(event[1] == sender for event in self.agent_events))
        self.assertEqual(self.agents[sender], ('/org/bluez/agent', 'KeyboardDisplay'))
        self.assertTrue(self.device_properties['Paired'])
        self.assertTrue(self.device_properties['Trusted'])
        self.assertTrue(self.device_properties['Connected'])

    def test_persistent_agent_requests_confirmation_before_connecting(self):
        self.pair_requires_agent = True
        self.authentication_method = 'RequestConfirmation'
        self.authentication_value = 123456
        _, session, events = self.pairing_session({'confirm': 'yes'})
        result = session.run(ADDRESS, pair=True)
        self.assertEqual(result, {'paired': True, 'trusted': True, 'connected': True})
        prompts = [event for event in events if event.get('event') == 'prompt']
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]['kind'], 'confirm')
        self.assertIn('123456', str(prompts[0]))
        self.assertEqual(self.authentication_replies, [()])
        self.assertEqual(self.operation_events, ['pair', 'trust', 'connect'])

    def test_persistent_agent_answers_numeric_passkey(self):
        self.pair_requires_agent = True
        self.authentication_method = 'RequestPasskey'
        self.authentication_value = 123456
        _, session, events = self.pairing_session({'passkey': '123456'})
        result = session.run(ADDRESS, pair=True)
        self.assertEqual(result, {'paired': True, 'trusted': True, 'connected': True})
        self.assertEqual([event['kind'] for event in events if event.get('event') == 'prompt'],
                         ['passkey'])
        self.assertEqual(self.authentication_replies, [(123456,)])
        self.assertEqual(self.operation_events, ['pair', 'trust', 'connect'])

    def test_persistent_agent_authorizes_pairing_before_connecting(self):
        self.pair_requires_agent = True
        self.authentication_method = 'RequestAuthorization'
        _, session, events = self.pairing_session({'confirm': 'yes'})
        result = session.run(ADDRESS, pair=True)
        self.assertEqual(result, {'paired': True, 'trusted': True, 'connected': True})
        prompts = [event for event in events if event.get('event') == 'prompt']
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]['kind'], 'confirm')
        self.assertEqual(self.authentication_replies, [()])
        self.assertEqual(self.operation_events, ['pair', 'trust', 'connect'])

    def test_rejected_confirmation_does_not_trust_or_connect(self):
        self.pair_requires_agent = True
        self.authentication_method = 'RequestConfirmation'
        self.authentication_value = 123456
        module, session, _ = self.pairing_session({'confirm': 'no'})
        with self.assertRaises(module.PairingError):
            session.run(ADDRESS, pair=True)
        self.assertEqual(self.operation_events, ['pair'])
        self.assertFalse(self.device_properties['Paired'])
        self.assertFalse(self.device_properties['Trusted'])
        self.assertFalse(self.device_properties['Connected'])

    def test_noninteractive_power_confirmation_and_readback(self):
        self.assertIn('Changing power off succeeded', self.command('power', 'off'))
        self.assertFalse(self.adapter_properties['Powered'])
        self.assertRegex(self.command('show'), r'Powered:\s+no')
        self.assertIn('Changing power on succeeded', self.command('power', 'on'))
        self.assertTrue(self.adapter_properties['Powered'])
        self.assertRegex(self.command('show'), r'Powered:\s+yes')

    def test_noninteractive_device_discovery_and_pair_connect_receipts(self):
        self.assertIn('Discovery started', self.command('--timeout', '1', 'scan', 'on'))
        self.assertTrue(self.adapter_properties['Discovering'])
        self.assertIn('Device ' + ADDRESS + ' Fixture controller', self.command('devices'))
        self.assertIn('Pairing successful', self.command('pair', ADDRESS))
        self.assertTrue(self.device_properties['Paired'])
        self.assertIn('trust succeeded', self.command('trust', ADDRESS))
        self.assertTrue(self.device_properties['Trusted'])
        self.assertIn('Connection successful', self.command('connect', ADDRESS))
        self.assertTrue(self.device_properties['Connected'])
        self.assertRegex(self.command('info', ADDRESS), r'Connected:\s+yes')
        self.assertIn('Disconnection successful', self.command('disconnect', ADDRESS))
        self.assertFalse(self.device_properties['Connected'])
        self.assertIn('Device has been removed', self.command('remove', ADDRESS))
        self.assertNotIn('Fixture controller', self.command('devices'))

    def test_unnamed_device_exposes_address_alias_without_remote_name(self):
        # BlueZ's Device1 Alias falls back to a dash-separated address when
        # the remote device has not supplied its optional Name property.
        self.device_properties.pop('Name')
        self.device_properties['Alias'] = dbus.String(ADDRESS.replace(':', '-'))
        self.assertIn('Device ' + ADDRESS + ' ' + ADDRESS.replace(':', '-'),
                      self.command('devices'))
        information = self.command('info', ADDRESS)
        self.assertNotRegex(information, r'(?m)^\s*Name:')
        self.assertRegex(information, r'Alias:\s+' + ADDRESS.replace(':', '-'))

    def test_discovery_reports_late_remote_name_and_alias_changes(self):
        self.device_properties.pop('Name')
        self.device_properties['Alias'] = dbus.String(ADDRESS.replace(':', '-'))
        initial = self.command('devices')
        self.assertIn(ADDRESS.replace(':', '-'), initial)
        resolved_name = 'iPega Fixture Controller'
        resolved = threading.Event()

        def resolve_name():
            changed = {'Name': dbus.String(resolved_name),
                       'Alias': dbus.String(resolved_name)}
            self.device_properties.update(changed)
            self.device.PropertiesChanged('org.bluez.Device1', changed, [])
            resolved.set()
            return GLib.SOURCE_REMOVE

        def start_resolution():
            # Send properties after the real target client has entered
            # discovery, rather than pre-populating its startup snapshot.
            GLib.timeout_add(150, resolve_name)

        self.discovery_callback = start_resolution
        output = self.command('--timeout', '1', 'scan', 'on')
        self.assertTrue(resolved.is_set(), output)
        self.assertIn('[CHG] Device ' + ADDRESS + ' Name: ' + resolved_name, output)
        self.assertIn('[CHG] Device ' + ADDRESS + ' Alias: ' + resolved_name, output)
        self.assertIn('Device ' + ADDRESS + ' ' + resolved_name, self.command('devices'))


if __name__ == '__main__':
    unittest.main()
