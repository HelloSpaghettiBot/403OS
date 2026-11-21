"""Image audio inventory contract using disposable output directories only."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'external/board/surface-go1/post-build.sh'
REQUIRED=('usr/bin/amixer','usr/bin/aplay','usr/sbin/alsactl')


class AudioImageInventory(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.target=self.base/'target';self.target.mkdir()
        (self.target/'etc').mkdir();(self.target/'etc/fstab').write_text('# SURFACE_DATA fixture\n')
        self.images=self.base/'images';self.images.mkdir()
        build=self.base/'build';kernel=build/'linux-6.12.118';kernel.mkdir(parents=True)
        spec=importlib.util.spec_from_file_location('hardware_check',ROOT/'scripts/check-hardware-config.py')
        hardware=importlib.util.module_from_spec(spec);spec.loader.exec_module(hardware)
        (kernel/'.config').write_text(''.join('CONFIG_'+name+'=y\n' for group in hardware.REQUIRED.values() for name in group)
                                     + ''.join('CONFIG_'+name+'=m\n' for name in hardware.MODULES))
        (self.base/'.config').write_text('BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HID=y\nBR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_HOG=y\n')
        bluez=build/'bluez5_utils-5.86';bluez.mkdir()
        (bluez/'config.log').write_text('  $ ./configure --enable-hid --enable-hog\n')
        for name in hardware.FIRMWARE:
            path=self.target/'lib/firmware'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        module_dir=self.target/'lib/modules/fixture'
        btusb=module_dir/'kernel/drivers/bluetooth/btusb.ko'
        btusb.parent.mkdir(parents=True);btusb.write_bytes(b'\x7fELFfixture')
        (module_dir/'modules.dep').write_text('kernel/drivers/bluetooth/btusb.ko:\n')
        (module_dir/'modules.alias').write_text('alias usb:v*p*d*dcE0dsc01dp01ic*isc*ip*in* btusb\n')
        load=self.target/'etc/modules-load.d/bluetooth.conf';load.parent.mkdir(parents=True);load.write_text('btusb\n')
        init=self.target/'etc/init.d/S11modules';init.parent.mkdir(parents=True);init.write_text('# fixture\n');init.chmod(0o755)
        module=self.target/'usr/lib/weston/surface-rotation.so';module.parent.mkdir(parents=True);module.write_bytes(b'fixture')
        self.environment=dict(os.environ,TARGET_DIR=str(self.target),BINARIES_DIR=str(self.images),BUILD_DIR=str(build))
        for name in REQUIRED:
            binary=self.target/name;binary.parent.mkdir(parents=True,exist_ok=True)
            binary.write_text('# fixture executable, never executed\n');binary.chmod(0o755)

    def run_script(self):
        return subprocess.run(['/bin/sh',str(SCRIPT)],env=self.environment,capture_output=True,text=True,timeout=5)

    def test_complete_audio_inventory_allows_image_preparation(self):
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.images/'efi-part/EFI/BOOT/grub.cfg').is_file())
        self.assertTrue((self.target/'usr/share/surface-tests/input.nes').is_file())

    def test_missing_or_nonexecutable_audio_tool_refuses_before_image_writes(self):
        for name in REQUIRED:
            binary=self.target/name
            for mode in (0o644,None):
                with self.subTest(binary=name,mode=mode):
                    if mode is None:binary.unlink()
                    else:binary.chmod(mode)
                    result=self.run_script();self.assertNotEqual(result.returncode,0)
                    self.assertIn('/'+name,result.stderr);self.assertIn('alsa-utils-reinstall',result.stderr)
                    self.assertFalse((self.images/'efi-part').exists())
                    if mode is None:binary.write_text('# fixture\n')
                    binary.chmod(0o755)


if __name__=='__main__':unittest.main()
