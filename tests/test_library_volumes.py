import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("library_volumes", ROOT/"services/library-volumes.py")
volumes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(volumes)


class LibraryVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sys = self.root/"sys"
        self.blocks = self.sys/"class/block"
        self.blocks.mkdir(parents=True)
        self.devices = self.root/"dev"
        self.devices.mkdir()
        self.marker = self.root/"surface-image-id"
        self.marker.write_text("surface-go-unit-fixture\n")
        self.mountinfo = self.root/"mountinfo"
        self.mountinfo.write_text("24 0 8:2 / / ro,relatime - ext4 /dev/sda2 ro\n")
        self.swaps = self.root/"swaps"
        self.swaps.write_text("Filename Type Size Used Priority\n")
        self.filesystems = self.root/"filesystems"
        self.filesystems.write_text("nodev\ttmpfs\n\text4\n\tntfs3\n\tvfat\n\texfat\n")
        self.library = self.root/"media/library"
        for name, value in [("GUEST_MARKER", self.marker), ("SYS_ROOT", self.sys),
                            ("SYS_BLOCK", self.blocks), ("DEV_ROOT", self.devices),
                            ("MOUNTINFO", self.mountinfo), ("SWAPS", self.swaps),
                            ("FILESYSTEMS", self.filesystems), ("LIBRARY_ROOT", self.library)]:
            context = mock.patch.object(volumes, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = mock.patch.object(volumes.os, "geteuid", return_value=0)
        context.start()
        self.addCleanup(context.stop)
        self.probes = {}
        self.disk("sda", "8:0", usb=True, partitions=[("sda1", "8:1"), ("sda2", "8:2"), ("sda3", "8:3")])
        self.disk("sdb", "8:16", usb=True, partitions=[("sdb1", "8:17")])
        self.probes[str(self.devices/"sdb1")] = {"UUID": "b4c027e0-985a-47aa-ab3f-478915e864ea", "TYPE": "ext4", "LABEL": "Pokémon Games"}

    def disk(self, name, dev, usb=False, partitions=(), removable=False, sd_type=None):
        actual = self.sys/("devices/pci/usb1/1-1/block" if usb else "devices/platform/block")/name
        actual.mkdir(parents=True)
        (actual/"dev").write_text(dev)
        (actual/"size").write_text("4096")
        (actual/"removable").write_text("1" if removable else "0")
        (actual/"device").mkdir()
        (actual/"device/serial").write_text("SERIAL-"+name)
        (actual/"device/model").write_text("MODEL-"+name)
        if sd_type:
            (actual/"device/type").write_text(sd_type)
        (self.blocks/name).symlink_to(actual, target_is_directory=True)
        (self.devices/name).write_bytes(b"synthetic device fixture")
        for number, (partition, partdev) in enumerate(partitions, 1):
            path = actual/partition
            path.mkdir()
            (path/"dev").write_text(partdev)
            (path/"size").write_text("2048")
            (path/"partition").write_text(str(number))
            (self.blocks/partition).symlink_to(path, target_is_directory=True)
            (self.devices/partition).write_bytes(b"synthetic partition fixture")

    def inventory(self):
        with mock.patch.object(volumes, "verify_device_node"), \
             mock.patch.object(volumes, "probe", side_effect=lambda _tool, path, **_kw: self.probes.get(str(path), {})):
            return volumes.inventory("/fixture/blkid")

    def test_root_disk_is_excluded_even_when_it_is_usb(self):
        found, errors = self.inventory()
        self.assertEqual(errors, [])
        self.assertEqual([Path(value["device"]).name for value in found], ["sdb1"])
        self.assertTrue(found[0]["eligible"])

    def test_removable_sd_whole_usb_and_supported_filesystems_are_detected(self):
        self.disk("sdc", "8:32", usb=True)
        self.disk("mmcblk0", "179:0", removable=True, sd_type="SD", partitions=[("mmcblk0p1", "179:1")])
        self.disk("mmcblk1", "179:8", removable=False, sd_type="MMC", partitions=[("mmcblk1p1", "179:9")])
        self.disk("sdd", "8:48", partitions=[("sdd1", "8:49")])
        self.probes[str(self.devices/"sdc")] = {"UUID": "1234-ABCD", "TYPE": "vfat"}
        self.probes[str(self.devices/"mmcblk0p1")] = {"UUID": "1234567890ABCDEF", "TYPE": "ntfs"}
        found, _ = self.inventory()
        by_name = {Path(value["device"]).name: value for value in found}
        self.assertEqual(set(by_name), {"sdb1", "sdc", "mmcblk0p1"})
        self.assertEqual(by_name["mmcblk0p1"]["driver"], "ntfs3")
        self.assertEqual(by_name["mmcblk0p1"]["transport"], "removable_sd")
        self.assertEqual(by_name["sdc"]["transport"], "usb")

    def test_active_swap_excludes_the_entire_external_disk(self):
        self.swaps.write_text("Filename Type Size Used Priority\n"+str(self.devices/"sdb1")+" partition 1024 0 -2\n")
        found, errors = self.inventory()
        self.assertEqual((found, errors), ([], []))

    def test_device_mapper_root_excludes_underlying_usb_disk(self):
        actual = self.sys/"devices/virtual/block/dm-0"
        actual.mkdir(parents=True)
        (actual/"dev").write_text("253:0")
        (actual/"size").write_text("2048")
        (actual/"slaves").mkdir()
        (actual/"slaves/sda2").symlink_to(self.blocks/"sda2")
        (self.blocks/"dm-0").symlink_to(actual, target_is_directory=True)
        self.mountinfo.write_text("24 0 253:0 / / ro - ext4 /dev/dm-0 ro\n")
        found, _ = self.inventory()
        self.assertEqual([Path(value["device"]).name for value in found], ["sdb1"])

    def test_duplicate_or_unsafe_filesystem_uuid_cannot_mount(self):
        self.disk("sdc", "8:32", usb=True)
        self.probes[str(self.devices/"sdc")] = self.probes[str(self.devices/"sdb1")].copy()
        found, _ = self.inventory()
        self.assertTrue(all(value["status"] == "duplicate_filesystem_uuid" and not value["eligible"] for value in found))
        self.probes[str(self.devices/"sdc")] = {"UUID": "../escape", "TYPE": "exfat"}
        found, _ = self.inventory()
        unsafe = next(value for value in found if value["filesystem"] == "exfat")
        self.assertEqual(unsafe["status"], "missing_or_unsafe_uuid")
        self.assertFalse(unsafe["eligible"])

    def test_existing_readonly_mount_path_is_returned_unchanged(self):
        with self.mountinfo.open("a") as output:
            output.write("25 24 8:17 / /external/Game\\040Drive ro,nosuid,nodev,noexec - ext4 /dev/sdb1 ro\n")
        found, errors = self.inventory()
        result = volumes.result_json(found, errors)
        self.assertEqual(result["mount_paths"], ["/external/Game Drive"])
        self.assertFalse(found[0]["eligible"])
        self.assertEqual(found[0]["status"], "already_mounted_readonly")

    def test_existing_readwrite_mount_is_left_untouched_and_not_a_readonly_source(self):
        with self.mountinfo.open("a") as output:
            output.write("25 24 8:17 / /external rw - ext4 /dev/sdb1 rw\n")
        found, errors = self.inventory()
        self.assertEqual(found[0]["status"], "already_mounted_readwrite")
        self.assertFalse(found[0]["eligible"])
        self.assertEqual(volumes.result_json(found, errors)["mount_paths"], [])

    def test_guest_guard_precedes_tools_discovery_and_directories(self):
        self.marker.unlink()
        with mock.patch("sys.argv", ["library-volumes.py", "--mount-readonly"]), \
             mock.patch.object(volumes, "util_linux") as tools, \
             mock.patch.object(volumes, "inventory") as inventory, \
             mock.patch.object(volumes, "secure_directory") as directories, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(volumes.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "ERROR")
        tools.assert_not_called()
        inventory.assert_not_called()
        directories.assert_not_called()

    def test_mount_uses_held_fds_and_ext4_noload_then_verifies_actual_mount(self):
        found, _ = self.inventory()
        chosen = found[0]
        target = self.library/chosen["uuid"]
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            with self.mountinfo.open("a") as output:
                output.write(f"25 24 8:17 / {target} ro,nosuid,nodev,noexec - ext4 /dev/sdb1 ro,noload\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def probe(_tool, _path, **_kwargs):
            return self.probes[chosen["device"]]

        with mock.patch.object(volumes, "inventory", return_value=(found, [])), \
             mock.patch.object(volumes, "open_block", side_effect=lambda _volume: os.open(chosen["device"], os.O_RDONLY)), \
             mock.patch.object(volumes, "probe", side_effect=probe), \
             mock.patch.object(volumes, "run_tool", side_effect=run):
            volumes.mount_one(chosen, "/fixture/blkid", "/fixture/mount", time.monotonic()+30)
        self.assertEqual(chosen["mount_path"], str(target))
        self.assertEqual(chosen["status"], "mounted_readonly")
        command, kwargs = calls[0]
        self.assertEqual(command[:7], ["/fixture/mount", "-n", "-i", "-c", "-t", "ext4", "-o"])
        self.assertEqual(set(command[7].split(",")), {"ro", "nosuid", "nodev", "noexec", "noload"})
        self.assertTrue(all(path.startswith("/proc/self/fd/") for path in command[-2:]))
        self.assertEqual(len(kwargs["pass_fds"]), 2)
        self.assertLessEqual(kwargs["timeout"], volumes.MOUNT_TIMEOUT)

    def test_identity_change_prevents_mount_and_directory_creation(self):
        found, _ = self.inventory()
        changed = [dict(found[0], uuid="1234-ABCD")]
        with mock.patch.object(volumes, "inventory", return_value=(changed, [])), \
             mock.patch.object(volumes, "open_block") as opening, \
             mock.patch.object(volumes, "secure_directory") as directories:
            with self.assertRaisesRegex(volumes.VolumeError, "changed before mounting"):
                volumes.mount_one(found[0], "/fixture/blkid", "/fixture/mount", time.monotonic()+30)
        opening.assert_not_called()
        directories.assert_not_called()

    def test_symlink_mountpoint_components_are_rejected(self):
        outside = self.root/"outside"
        outside.mkdir()
        self.library.parent.mkdir()
        self.library.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            volumes.secure_directory(self.library/"1234-ABCD")
        self.assertFalse((outside/"1234-ABCD").exists())

    def test_private_umask_does_not_block_new_game_mountpoint_traversal(self):
        before = stat.S_IMODE(self.root.stat().st_mode)
        mask = os.umask(0o077)
        try:
            descriptor = volumes.secure_directory(self.library/"1234-ABCD")
            os.close(descriptor)
        finally:
            os.umask(mask)
        for path in (self.root/"media", self.library, self.library/"1234-ABCD"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), before)
        self.library.chmod(0o700)
        descriptor = volumes.secure_directory(self.library/"1234-ABCD")
        os.close(descriptor)
        self.assertEqual(stat.S_IMODE(self.library.stat().st_mode), 0o700)

    def test_regular_device_nodes_are_rejected_by_production_open(self):
        found, _ = self.inventory()
        with self.assertRaisesRegex(volumes.VolumeError, "block descriptor"):
            volumes.open_block(found[0])

    def test_utf8_export_is_decoded_and_subprocesses_are_bounded(self):
        export = "UUID=1234-ABCD\nTYPE=vfat\nLABEL=Pok\\xc3\\xa9mon\\x20Games\n"
        with mock.patch.object(volumes, "run_tool", return_value=SimpleNamespace(returncode=0, stdout=export)):
            fields = volumes.probe("/fixture/blkid", "/fixture/device")
        self.assertEqual(fields["LABEL"], "Pokémon Games")
        with mock.patch.object(volumes.subprocess, "run") as run:
            volumes.run_tool(["/fixture/tool"])
        self.assertEqual(run.call_args.kwargs["timeout"], volumes.PROBE_TIMEOUT)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        encoded = json.dumps({"path": "/media/library", "label": fields["LABEL"]}, ensure_ascii=False)
        self.assertIn("Pokémon", encoded)

    def test_busybox_probe_tool_is_rejected(self):
        with mock.patch.object(volumes.shutil, "which", return_value="/sbin/blkid"), \
             mock.patch.object(volumes, "run_tool", return_value=SimpleNamespace(returncode=0, stdout="BusyBox v1.37")):
            with self.assertRaisesRegex(volumes.VolumeError, "BusyBox"):
                volumes.util_linux("blkid")

    def test_missing_root_identity_refuses_before_any_probe(self):
        self.mountinfo.write_text("24 0 0:32 / / rw - overlay overlay rw\n")
        with mock.patch.object(volumes, "probe") as probing:
            with self.assertRaisesRegex(volumes.VolumeError, "mounted root block device"):
                volumes.inventory("/fixture/blkid")
        probing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
