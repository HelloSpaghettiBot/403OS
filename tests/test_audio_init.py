"""Fresh/restored guest mixer setup; all commands are isolated file fixtures."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'external/board/surface-go1/overlay/etc/init.d/S22persistent'


class AudioInitialization(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.data=self.base/'data';self.data.mkdir()
        self.bin=self.base/'bin';self.bin.mkdir();self.calls=self.base/'calls.jsonl'
        self.mounts=self.base/'mounts';self.mounts.write_text(f'fixture {self.data} ext4 rw 0 0\n')
        self.environment=dict(os.environ,PATH=str(self.bin)+':'+os.environ.get('PATH','/usr/bin:/bin'),
                              AUDIO_FIXTURE_BASE=str(self.base),AUDIO_FIXTURE_FAIL_INIT='0')
        stub='''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
base=Path(os.environ['AUDIO_FIXTURE_BASE'])
name=Path(sys.argv[0]).name
with (base/'calls.jsonl').open('a') as log:log.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='timeout':
 assert sys.argv[1] in ('3','10') and sys.argv[2]=='alsactl'
 os.execv(str(base/'bin/alsactl'),['alsactl',*sys.argv[3:]])
elif name=='alsactl':
 if sys.argv[1:]==['init']:sys.exit(int(os.environ['AUDIO_FIXTURE_FAIL_INIT']))
 assert sys.argv[1:2]==['-f'] and Path(sys.argv[2])==base/'data/home/asound.state'
 assert sys.argv[3:] in (['restore'],['store'])
 if sys.argv[3]=='store':Path(sys.argv[2]).write_text('fixture saved mixer state\\n')
elif name not in ('mount','chown'):raise AssertionError('Unexpected fixture command')
'''
        for name in ('timeout','alsactl','mount','chown'):
            path=self.bin/name;path.write_text(stub);path.chmod(0o700)
        source=SCRIPT.read_text()
        for old,new in (('/proc/mounts',str(self.mounts)),('/var/lib/bluetooth',str(self.base/'bluetooth')),
                        ('/run/surface-games',str(self.base/'games')),('/usr/share/surface/system/PPSSPP',str(self.base/'ppsspp')),
                        ('/sys/class/backlight',str(self.base/'backlight')),('/data',str(self.data))):
            source=source.replace(old,new)
        self.script=self.base/'S22fixture';self.script.write_text(source)

    def run_script(self):
        result=subprocess.run(['/bin/sh',str(self.script),'start'],env=self.environment,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_fresh_mixer_initializes_once_and_persists_private_state(self):
        calls=self.run_script();state=self.data/'home/asound.state'
        self.assertIn(['timeout','10','alsactl','init'],calls)
        self.assertIn(['alsactl','-f',str(state),'store'],calls)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode),0o600)
        self.calls.unlink();calls=self.run_script()
        self.assertIn(['timeout','10','alsactl','-f',str(state),'restore'],calls)
        self.assertNotIn(['alsactl','init'],calls)

    def test_existing_owner_mixer_state_is_restored_without_reset_or_rewrite(self):
        state=self.data/'home/asound.state';state.parent.mkdir();state.write_text('OWNER LEVELS\n')
        calls=self.run_script()
        self.assertIn(['alsactl','-f',str(state),'restore'],calls)
        self.assertNotIn(['alsactl','init'],calls);self.assertEqual(state.read_text(),'OWNER LEVELS\n')

    def test_failed_initialization_does_not_persist_failure_as_success(self):
        self.environment['AUDIO_FIXTURE_FAIL_INIT']='1';calls=self.run_script()
        self.assertIn(['alsactl','init'],calls)
        self.assertFalse(any(call[-1]=='store' for call in calls))
        self.assertFalse((self.data/'home/asound.state').exists())

    def test_missing_guest_data_mount_makes_no_mixer_or_mount_calls(self):
        self.mounts.write_text('');self.assertEqual(self.run_script(),[])
        self.assertFalse((self.data/'home').exists())


if __name__=='__main__':unittest.main()
