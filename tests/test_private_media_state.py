"""Private USB refresh must retain login/save state without unsafe tar extraction."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('private_media', ROOT/'scripts/private-usb-media.py')
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


class PrivateMediaStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root/'stage'
        self.stage.mkdir()
        self.archive = self.root/'state.tar'

    def archive_with(self, name='profiles/auth.json', mode=0o600, uid=0, kind=tarfile.REGTYPE):
        with tarfile.open(self.archive, 'w') as output:
            item = tarfile.TarInfo(name)
            item.mode, item.uid, item.type = mode, uid, kind
            data = b'{"fixture":"password-hash"}'
            item.size = len(data) if kind == tarfile.REGTYPE else 0
            item.linkname = '/etc/shadow' if kind == tarfile.SYMTYPE else ''
            output.addfile(item, io.BytesIO(data) if item.size else None)

    def test_authentication_data_and_private_mode_survive_refresh(self):
        self.archive_with()
        state = media.restore_state(self.archive, self.stage)
        path = self.stage/'profiles/auth.json'
        self.assertIn('password-hash', path.read_text())
        self.assertEqual(state[Path('profiles/auth.json')], dict(uid=0, gid=0, mode=0o600))
        with patch.object(media.os, 'chown') as chown:
            media.state_mode(path, self.stage, state)
        chown.assert_called_once_with(path, 0, 0, follow_symlinks=False)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unsafe_archives_are_refused_before_staging_changes(self):
        cases = [('../escape', 0o600, 0, tarfile.REGTYPE),
                 ('/home/escape', 0o600, 0, tarfile.REGTYPE),
                 ('roms/replace.gba', 0o600, 0, tarfile.REGTYPE),
                 ('profiles/auth.json', 0o666, 0, tarfile.REGTYPE),
                 ('profiles/auth.json', 0o4600, 0, tarfile.REGTYPE),
                 ('home/escape', 0o600, 0, tarfile.SYMTYPE)]
        for name, mode, uid, kind in cases:
            with self.subTest(name=name, mode=mode, kind=kind):
                self.archive_with(name, mode, uid, kind)
                with self.assertRaises(ValueError):
                    media.restore_state(self.archive, self.stage)
                self.assertEqual(list(self.stage.iterdir()), [])

    def test_existing_staging_symlink_is_not_followed(self):
        (self.stage/'profiles').symlink_to(self.root, target_is_directory=True)
        self.archive_with()
        with self.assertRaisesRegex(ValueError, 'symlink'):
            media.restore_state(self.archive, self.stage)
        self.assertFalse((self.root/'auth.json').exists())

    def test_secondary_user_owner_is_preserved(self):
        self.archive_with('home/user/save', uid=2000)
        state = media.restore_state(self.archive, self.stage)
        self.assertEqual(state[Path('home/user/save')]['uid'], 2000)


if __name__ == '__main__':
    unittest.main()
