"""Actual image bluetoothctl against private D-Bus fixtures, never host radios.

Requires an existing x86_64 target build plus host dbus-python/PyGObject.
The ordinary source suite skips these integration checks when unavailable.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
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
        fixture = self
        objects = {dbus.ObjectPath(ADAPTER_PATH): {'org.bluez.Adapter1': self.adapter_properties},
                   dbus.ObjectPath(DEVICE_PATH): {'org.bluez.Device1': self.device_properties}}

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
            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='')
            def Pair(self):
                self.properties['Paired'] = dbus.Boolean(True)

            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='')
            def Connect(self):
                self.properties['Connected'] = dbus.Boolean(True)

            @dbus.service.method('org.bluez.Device1', in_signature='', out_signature='')
            def Disconnect(self):
                self.properties['Connected'] = dbus.Boolean(False)

        class Manager(dbus.service.Object):
            @dbus.service.method('org.freedesktop.DBus.ObjectManager', in_signature='',
                                 out_signature='a{oa{sa{sv}}}')
            def GetManagedObjects(self):
                return objects

        # BlueZ's ObjectManager root is '/', while adapter paths start /org/bluez.
        self.manager = Manager(self.bus, '/')
        self.adapter = Adapter(self.bus, ADAPTER_PATH, 'org.bluez.Adapter1', self.adapter_properties)
        self.device = Device(self.bus, DEVICE_PATH, 'org.bluez.Device1', self.device_properties)

    def stop_bus(self):
        for attribute in ('manager', 'adapter', 'device'):
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
        prefix = [str(LOADER), '--library-path', str(TARGET / 'lib') + ':' + str(TARGET / 'usr/lib'),
                  str(CLIENT)]
        result = subprocess.run(prefix + list(arguments), env=self.environment,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        return ANSI.sub('', result.stdout)

    def test_noninteractive_list_and_show_report_actual_fixture_adapter(self):
        self.assertIn('Controller 00:11:22:33:44:55', self.command('list'))
        information = self.command('show')
        self.assertIn('Controller 00:11:22:33:44:55', information)
        self.assertRegex(information, r'Powered:\s+yes')

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
