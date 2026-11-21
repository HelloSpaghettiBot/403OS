"""Service startup fixtures with a fake daemon; no host networking/services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'external/package/surface-tailscale/S71tailscaled'


class TailscaleServiceStart(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.data=self.base/'data';self.data.mkdir()
        self.bin=self.base/'bin';self.bin.mkdir();self.calls=self.base/'calls.jsonl'
        self.mounts=self.base/'mounts';self.mounts.write_text(f'fixture {self.data} ext4 rw 0 0\n')
        self.cmdline=self.base/'cmdline';self.cmdline.write_text('fixture guest\n')
        self.tun=self.base/'tun';self.tun.touch()
        self.daemon=self.bin/'tailscaled';self.daemon.write_text('# fixture, never executed\n');self.daemon.chmod(0o755)
        self.environment=dict(os.environ,PATH=str(self.bin)+':'+os.environ.get('PATH','/usr/bin:/bin'),
                              TAILSCALE_FIXTURE_BASE=str(self.base),TAILSCALE_FIXTURE_START_ERROR='0')
        stub=self.bin/'start-stop-daemon'
        stub.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
base=Path(os.environ['TAILSCALE_FIXTURE_BASE']);args=sys.argv[1:]
with (base/'calls.jsonl').open('a') as log:log.write(json.dumps(args)+'\\n')
assert '-S' in args and '-x' in args and '-p' in args
assert args[args.index('-x')+1]==str(base/'bin/tailscaled')
pidfile=Path(args[args.index('-p')+1]);assert pidfile==base/'run/tailscale/tailscaled.pid'
assert '--state='+str(base/'data/tailscale/tailscaled.state') in args
if os.environ['TAILSCALE_FIXTURE_START_ERROR']=='1':sys.exit(2)
if pidfile.exists():sys.exit(0 if '-o' in args else 1)
pidfile.write_text('fixture running before socket exists\\n')
with (base/'spawns').open('a') as output:output.write('spawn\\n')
''');stub.chmod(0o700)
        source=SCRIPT.read_text()
        # Only this disposable script changes the character-device predicate.
        # Production always requires the real kernel TUN character device.
        source=source.replace('[ ! -c /dev/net/tun ]','[ ! -f '+str(self.tun)+' ]')
        for old,new in (('/usr/sbin/tailscaled',str(self.daemon)),('/proc/cmdline',str(self.cmdline)),
                        ('/proc/mounts',str(self.mounts)),('/run/tailscale',str(self.base/'run/tailscale')),
                        ('/data',str(self.data))):source=source.replace(old,new)
        self.script=self.base/'S71fixture';self.script.write_text(source)

    def start(self):
        return subprocess.run(['/bin/sh',str(self.script),'start'],env=self.environment,capture_output=True,text=True,timeout=5)

    def test_start_is_idempotent_before_local_api_socket_exists(self):
        self.assertEqual(self.start().returncode,0)
        self.assertFalse((self.base/'run/tailscale/tailscaled.sock').exists())
        result=self.start();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.base/'spawns').read_text(),'spawn\n')
        self.assertEqual(len(self.calls.read_text().splitlines()),2)

    def test_real_start_error_is_propagated(self):
        self.environment['TAILSCALE_FIXTURE_START_ERROR']='1'
        self.assertEqual(self.start().returncode,2)
        self.assertFalse((self.base/'spawns').exists())

    def test_missing_persistent_mount_or_tun_refuses_without_daemon_start(self):
        self.mounts.write_text('');self.assertEqual(self.start().returncode,1)
        self.assertFalse(self.calls.exists())
        self.mounts.write_text(f'fixture {self.data} ext4 rw 0 0\n');self.tun.unlink()
        self.assertEqual(self.start().returncode,1);self.assertFalse(self.calls.exists())

    def test_installer_never_starts_the_daemon(self):
        self.cmdline.write_text('fixture surface.vm-install=1\n')
        self.assertEqual(self.start().returncode,0);self.assertFalse(self.calls.exists())


if __name__=='__main__':unittest.main()
