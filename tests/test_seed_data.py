"""Private DATA migration fixtures; no real mounts, devices or /data writes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
seed=module('seed_data',PROJECT/'installer/seed-data.py')
profiles=module('seed_profiles',PROJECT/'services/profiles.py')


class DataMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.password=profiles.password_hash('fixture-only-password')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/'live';self.target=self.base/'installed'
        self.source.mkdir();self.target.mkdir()
        self.events=[]

    def file(self,path,data=b'fixture bytes',mode=0o600):
        path=self.source/path;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data);path.chmod(mode);return path

    def data(self):
        self.file('roms/example.gba',b'synthetic ROM fixture\x00'*32)
        self.file('bios/fixture.bin',b'synthetic firmware fixture')
        self.file('library/covers/fixture.sgo',b'synthetic texture')
        self.file('library/catalog.tsv',b'fixture\tFixture\tgba\t/data/roms/example.gba\t/data/library/covers/fixture.sgo\n')
        self.file('profiles/auth.json',json.dumps(dict(version=1,profiles=[dict(name='root',uid=0,gid=0,password=self.password)],attempts={})).encode())
        self.file('profiles/player/home/save.cfg',b'profile preferences')
        self.file('home/wpa_supplicant.conf',b'synthetic private WiFi fixture')
        self.file('saves/fixture.srm',b'synthetic save fixture')
        return seed.source_plan(64*1024**3,self.source)

    def progress(self,event,**fields):self.events.append(dict(event=event,**fields))

    def test_all_games_authentication_and_private_permissions_survive_verified_copy(self):
        plan=self.data();before=(self.source/'profiles/auth.json').read_bytes()
        receipt=seed.copy_tree(self.source,self.target,plan,self.progress)
        self.assertTrue(receipt['verified']);self.assertEqual(receipt['rom_files'],1)
        self.assertTrue(receipt['root_auth_preserved'])
        for entry in plan['entries']:
            original=self.source/entry['path'];copied=self.target/entry['path']
            self.assertEqual(stat.S_IMODE(copied.stat().st_mode),entry['mode'])
            self.assertEqual((copied.stat().st_uid,copied.stat().st_gid),(entry['uid'],entry['gid']))
            if entry['kind']=='file':self.assertEqual(copied.read_bytes(),original.read_bytes())
        auth=self.target/'profiles/auth.json'
        self.assertEqual(auth.read_bytes(),before);self.assertEqual(auth.stat().st_mode&0o777,0o600)
        self.assertTrue(profiles.password_matches('fixture-only-password',json.loads(auth.read_bytes())['profiles'][0]['password']))
        self.assertFalse(profiles.password_matches('wrong-password',json.loads(auth.read_bytes())['profiles'][0]['password']))
        self.assertEqual({x['phase'] for x in self.events},{'preserve-user-data','verify-user-data'})
        self.assertNotIn('fixture-only-password',json.dumps(self.events))
        self.assertFalse(any(p.name.startswith('.surface-seed-stage') for p in self.target.iterdir()))

    def test_only_selected_data_copied_and_cache_session_payload_tailnet_excluded(self):
        self.data()
        for path in ('tailscale/tailscaled.state','SurfaceGoGamingOS/surface-go.img','logs/private.log',
                     'profiles/player/cache/private','home/.cache/private','profiles/.lock','home/SingletonSocket'):
            self.file(path)
        self.file('roms/cache/real-game.gba')
        plan=seed.source_plan(64*1024**3,self.source)
        seed.copy_tree(self.source,self.target,plan,self.progress)
        for path in ('tailscale','SurfaceGoGamingOS','logs','profiles/player/cache','home/.cache','profiles/.lock','home/SingletonSocket'):
            self.assertFalse((self.target/path).exists(),path)
        self.assertTrue((self.target/'roms/cache/real-game.gba').is_file())

    def test_external_links_copied_literal_and_never_followed_but_rom_links_refused(self):
        self.data();outside=self.base/'outside';outside.write_bytes(b'outside remains untouched')
        (self.source/'home/external').symlink_to(outside)
        (self.source/'bios/PPSSPP').symlink_to('/usr/share/surface/system/PPSSPP')
        plan=seed.source_plan(64*1024**3,self.source)
        seed.copy_tree(self.source,self.target,plan,self.progress)
        self.assertEqual(os.readlink(self.target/'home/external'),str(outside))
        self.assertEqual(os.readlink(self.target/'bios/PPSSPP'),'/usr/share/surface/system/PPSSPP')
        self.assertEqual(outside.read_bytes(),b'outside remains untouched')
        (self.source/'roms/external.gba').symlink_to(outside)
        with self.assertRaisesRegex(seed.Refused,'ROM symbolic links'):seed.source_plan(64*1024**3,self.source)

    def test_bad_auth_capacity_external_catalog_and_special_files_refuse_preflight(self):
        self.data();auth=self.source/'profiles/auth.json';auth.chmod(0o644)
        with self.assertRaisesRegex(seed.Refused,'authentication'):seed.source_plan(64*1024**3,self.source)
        auth.chmod(0o600)
        with self.assertRaisesRegex(seed.Refused,'do not fit'):seed.source_plan(seed.RESERVE,self.source)
        catalog=self.source/'library/catalog.tsv';old=catalog.read_bytes()
        catalog.write_bytes(b'fixture\tFixture\tgba\t/run/media/usb/external.gba\t\n')
        with self.assertRaisesRegex(seed.Refused,'outside'):seed.source_plan(64*1024**3,self.source)
        catalog.write_bytes(old);os.mkfifo(self.source/'home/pipe')
        with self.assertRaisesRegex(seed.Refused,'socket, device or FIFO'):seed.source_plan(64*1024**3,self.source)
        self.assertEqual(list(self.target.iterdir()),[])

    def test_existing_destination_data_is_preserved_and_never_overwritten(self):
        plan=self.data();existing=self.target/'keep';existing.write_bytes(b'keep')
        with self.assertRaisesRegex(seed.Refused,'never be overwritten'):seed.copy_tree(self.source,self.target,plan,self.progress)
        self.assertEqual(existing.read_bytes(),b'keep')
        self.assertFalse((self.target/'profiles').exists())

    def test_changed_source_and_readback_corruption_never_publish_authentication(self):
        plan=self.data();self.file('roms/example.gba',b'changed')
        with self.assertRaisesRegex(seed.Refused,'changed after review'):seed.copy_tree(self.source,self.target,plan,self.progress)
        self.assertFalse((self.target/'profiles').exists())
        for child in self.target.iterdir():
            import shutil
            shutil.rmtree(child)
        plan=seed.source_plan(64*1024**3,self.source)
        changed=False
        def corrupt(event,**fields):
            nonlocal changed
            if fields['phase']=='preserve-user-data' and fields['percent']==100 and not changed:
                changed=True
                stage=next(self.target.glob('.surface-seed-stage-*'))
                path=stage/'roms/example.gba';path.write_bytes(b'X'*path.stat().st_size)
        with self.assertRaisesRegex(seed.Refused,'readback'):seed.copy_tree(self.source,self.target,plan,corrupt)
        self.assertFalse((self.target/'profiles').exists())

    def test_new_live_file_during_copy_refuses_final_manifest_and_preserves_source(self):
        plan=self.data();changed=False
        def modify(event,**fields):
            nonlocal changed
            if not changed:
                changed=True;self.file('roms/new-game.gba',b'new fixture')
        with self.assertRaisesRegex(seed.Refused,'changed during verification'):seed.copy_tree(self.source,self.target,plan,modify)
        self.assertTrue((self.source/'roms/new-game.gba').is_file())
        self.assertFalse((self.target/'profiles').exists())

    def test_setid_refused_but_sticky_runtime_directory_preserved(self):
        self.data();runtime=self.source/'windows/runtime';runtime.mkdir(parents=True);runtime.chmod(0o1777)
        plan=seed.source_plan(64*1024**3,self.source);seed.copy_tree(self.source,self.target,plan,self.progress)
        self.assertEqual((self.target/'windows/runtime').stat().st_mode&0o7777,0o1777)
        (self.source/'home/wpa_supplicant.conf').chmod(0o4600)
        with self.assertRaisesRegex(seed.Refused,'Set-user/group-ID'):seed.source_plan(64*1024**3,self.source)

    def test_host_guard_runs_before_namespace_mount_or_target_access(self):
        with patch.object(seed,'guest',side_effect=seed.Refused('host forbidden')), \
             patch.object(seed,'target_snapshot',side_effect=AssertionError('target accessed')), \
             patch.object(seed.ctypes,'CDLL',side_effect=AssertionError('namespace accessed')), \
             patch.object(seed,'tool',side_effect=AssertionError('mount accessed')):
            with self.assertRaisesRegex(seed.Refused,'host forbidden'):seed.apply_seed('/dev/fixture','id','uuid',64*1024**3,{})

    def test_private_mount_cleanup_after_copy_failure_without_running_any_mount_tool(self):
        plan=self.data();mount=self.base/'private/seed-target';mount.parent.mkdir(mode=0o700)
        block=self.base/'fixture-block';block.write_bytes(b'never written')
        snapshot=({'name':'fixture','stable_id':'id'},str(block),123)
        libc=SimpleNamespace(unshare=lambda flags:0)
        real_fstat=os.fstat;calls=[]
        def fake_fstat(fd):
            path=os.readlink(f'/proc/self/fd/{fd}')
            if path==str(block):return SimpleNamespace(st_mode=stat.S_IFBLK|0o600,st_rdev=123)
            if path==str(mount.parent):return SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)
            return real_fstat(fd)
        with patch.object(seed,'guest'),patch.object(seed,'mounted_data'),patch.object(seed,'load_growth',return_value={}), \
             patch.object(seed,'source_plan',return_value=plan),patch.object(seed,'target_snapshot',return_value=snapshot), \
             patch.object(seed.ctypes,'CDLL',return_value=libc),patch.object(seed,'MOUNT',mount), \
             patch.object(seed.os,'fstat',side_effect=fake_fstat),patch.object(seed,'tool',side_effect=lambda argv:calls.append(argv)), \
             patch.object(seed.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, \
             patch.object(seed,'mounted_identity'),patch.object(seed,'copy_tree',side_effect=seed.Refused('fixture copy failure')):
            with self.assertRaisesRegex(seed.Refused,'fixture copy failure'):seed.apply_seed('/dev/fixture','id','uuid',64*1024**3,plan)
        self.assertIn(['umount',str(mount)],calls)
        self.assertFalse(mount.exists());self.assertEqual(block.read_bytes(),b'never written')
        argv=run.call_args.args[0]
        self.assertIn('rw,nosuid,nodev,noexec',argv);self.assertIn('--no-canonicalize',argv)


if __name__=='__main__':unittest.main()
