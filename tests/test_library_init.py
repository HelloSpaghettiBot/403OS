"""Pristine-DATA init fixtures. No real mounts, host device reads or services."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'external/board/surface-go1/overlay/etc/init.d/S21library'


class LibraryFirstBoot(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.marker=self.base/'image-id';self.marker.write_text('surface-go-fixture\n');self.marker.chmod(0o644)
        self.data=self.base/'data';self.data.mkdir()
        self.mountinfo=self.base/'mountinfo'
        dev=self.data.stat().st_dev
        self.mountinfo.write_text(f'42 23 {os.major(dev)}:{os.minor(dev)} / {self.data} rw - ext4 fixture rw\n')
        self.mounts=self.base/'mounts';self.mounts.write_text('')
        self.calls=self.base/'calls.jsonl';self.binary=self.base/'bin';self.binary.mkdir()
        helper=self.binary/'surface-library-volumes'
        helper.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n'
                          'with Path('+repr(str(self.calls))+').open("a") as out:out.write(json.dumps(sys.argv[1:])+"\\n")\n'
                          'print("{\\"volumes\\": [], \\"status\\": \\"fixture\\"}")\n')
        helper.chmod(0o700)
        mount=self.binary/'mount'
        mount.write_text('#!/usr/bin/env python3\nimport sys\nassert sys.argv[1:3] == ["-t", "tmpfs"]\n')
        mount.chmod(0o700)
        source=SCRIPT.read_text()
        # Only the disposable script copy maps root UID checks to this fixture
        # owner's UID. Production has no environment/path/identity override.
        source=source.replace('os.geteuid() != 0','False').replace('info.st_uid != 0','info.st_uid != os.getuid()')
        for old,new in (
            ('/etc/surface-image-id',str(self.marker)),('/proc/self/mountinfo',str(self.mountinfo)),
            ('/proc/mounts',str(self.mounts)),('/usr/bin/surface-library-volumes',str(helper)),
            ('/sys/bus/virtio/drivers/9pnet_virtio',str(self.base/'no-9p')),
            ('/sys/class/dmi/id/product_name',str(self.base/'no-dmi')),
            ('/run/surface-test-options',str(self.base/'options')),('/dev/ttyS0',str(self.base/'serial')),
            ('/library/roms',str(self.base/'roms')),('/library/index',str(self.base/'index')),
            ('/media/library',str(self.base/'media')),('/data',str(self.data))):
            source=source.replace(old,new)
        self.script=self.base/'S21fixture';self.script.write_text(source)

    def run_script(self):
        environment=dict(os.environ,PATH=str(self.binary)+':'+os.environ.get('PATH','/usr/bin:/bin'))
        return subprocess.run(['/bin/sh',str(self.script),'start'],env=environment,capture_output=True,text=True,timeout=5)

    def test_pristine_data_creates_private_logs_before_running_discovery(self):
        self.assertFalse((self.data/'logs').exists())
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(stat.S_IMODE((self.data/'logs').stat().st_mode),0o700)
        self.assertEqual(json.loads(self.calls.read_text()),['--mount-readonly'])
        self.assertEqual(json.loads((self.data/'logs/library-volumes.json').read_text())['status'],'fixture')

    def test_missing_or_read_only_data_refuses_before_fallback_directories(self):
        original=self.mountinfo.read_text()
        for inventory in ('',original.replace(' rw - ext4 ',' ro - ext4 '),original.replace(' - ext4 ',' - tmpfs ')):
            with self.subTest(inventory=inventory):
                self.mountinfo.write_text(inventory)
                result=self.run_script();self.assertNotEqual(result.returncode,0)
                self.assertFalse((self.data/'logs').exists());self.assertFalse((self.base/'media').exists())
                self.assertFalse(self.calls.exists())

    def test_invalid_or_symlinked_image_marker_refuses_before_log_creation(self):
        self.marker.write_text('other-image\n');self.assertNotEqual(self.run_script().returncode,0)
        self.assertFalse((self.data/'logs').exists())
        self.marker.unlink();self.marker.symlink_to(self.mountinfo)
        self.assertNotEqual(self.run_script().returncode,0);self.assertFalse((self.data/'logs').exists())

    def test_existing_symlink_log_directory_is_not_followed_or_adopted(self):
        elsewhere=self.base/'elsewhere';elsewhere.mkdir();(self.data/'logs').symlink_to(elsewhere)
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertFalse(self.calls.exists());self.assertFalse((elsewhere/'library-volumes.json').exists())


if __name__=='__main__':unittest.main()
