import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("usb_plan", ROOT/"installer/prepare-usb.py")
usb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb)


def disk_header(size, table, backup=False):
    sectors = size//512
    raw = bytearray(512)
    raw[:8] = b"EFI PART"
    struct.pack_into("<III", raw, 8, 0x10000, 92, 0)
    struct.pack_into("<QQQQ", raw, 24, sectors-1 if backup else 1,
                     1 if backup else sectors-1, 34, sectors-34)
    raw[56:72] = uuid.UUID("49b7bba7-df14-4cdd-ac28-e32d00bfef46").bytes_le
    struct.pack_into("<QIII", raw, 72, sectors-33 if backup else 2, 128, 128, zlib.crc32(table))
    struct.pack_into("<I", raw, 16, zlib.crc32(raw[:92]))
    return raw


def create_disk(path, size, entries):
    table = bytearray(128*128)
    for number, kind, identity, start, length, name, attributes in entries:
        source = dict(type=kind, guid=identity, sectors=length//512, attributes=attributes)
        table[(number-1)*128:number*128] = usb.make_entry(source, start, name)
    with path.open("xb") as output:
        output.truncate(size)
        mbr = bytearray(512)
        mbr[:15] = b"PRESERVE-OLD-MBR"
        mbr[450] = 0xee
        struct.pack_into("<II", mbr, 454, 1, min(size//512-1, 0xffffffff))
        mbr[510:512] = b"\x55\xaa"
        output.seek(0)
        output.write(mbr)
        output.seek(512)
        output.write(disk_header(size, table))
        output.seek(1024)
        output.write(table)
        output.seek(size-33*512)
        output.write(table)
        output.seek(size-512)
        output.write(disk_header(size, table, backup=True))


class USBPlanTests(unittest.TestCase):
    def setUp(self):
        usb.FIXTURES.mkdir(exist_ok=True, parents=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="gpt-fixture-", dir=usb.FIXTURES)
        self.directory = Path(self.temporary.name)
        self.target = self.directory/"usb-synthetic-256GiB.img"
        self.source = self.directory/"surface-go.img"
        basic = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
        create_disk(self.target, usb.EXPECTED_BYTES, [
            (1, basic, usb.OLD_BOOT_GUID, 2048, (usb.H_START-2048)*512, "UDFVOLUME", 0),
            (2, basic, usb.H_GUID, usb.H_START, usb.H_BYTES, "H files must stay", 0x100000000000001)])
        start = 2048
        source_entries = []
        for number, kind, identity, length, name in [
            (1, usb.ESP_TYPE, "8e212f49-f852-4c2a-904f-cb8de3d1a174", 64*usb.MIB, "EFI"),
            (2, "44479540-f297-41b2-9af7-d131d5f0458a", usb.ROOT_GUID, 2048*usb.MIB, "root"),
            (3, "0fc63daf-8483-4772-8e79-3d69d8477de4", usb.DATA_GUID, 4096*usb.MIB, "data")]:
            source_entries.append((number, kind, identity, start, length, name, 0))
            start += length//512
        source_size = (start+33+2047)//2048*2048*512
        create_disk(self.source, source_size, source_entries)
        self.sentinels = [usb.H_START*512, usb.H_START*512+usb.H_BYTES//2, usb.H_START*512+usb.H_BYTES-4096]
        with self.target.open("r+b") as disk:
            for offset in self.sentinels:
                disk.seek(offset)
                disk.write(b"H-USER-FILES-UNCHANGED"*32)
            disk.seek(2048*512+2*usb.MIB)
            disk.write(b"old Windows bytes must clear")
        with self.source.open("r+b") as disk:
            for number, _, _, start, length, _, _ in source_entries:
                disk.seek(start*512)
                disk.write(("PAYLOAD-"+str(number)).encode())
                disk.seek(start*512+length-512)
                disk.write(("END-"+str(number)).encode())

    def tearDown(self):
        self.temporary.cleanup()

    def read_target(self):
        with self.target.open("rb") as disk:
            return usb.read_gpt(disk.fileno(), usb.EXPECTED_BYTES)

    def test_exact_geometry_crc_h_entry_and_file_sentinels_preserved(self):
        before = self.read_target()
        with self.target.open("rb") as disk:
            sentinels = [os.pread(disk.fileno(), 1024, offset) for offset in self.sentinels]
            original_mbr = os.pread(disk.fileno(), 512, 0)
        writes = []
        original_write = usb.fixture_write

        def record_write(fd, value, offset):
            writes.append((offset, offset+len(value)))
            original_write(fd, value, offset)

        with mock.patch.object(usb, "fixture_write", side_effect=record_write):
            result = usb.apply_fixture(self.target, self.source)
        after = self.read_target()
        self.assertEqual(after["table"][128:256], before["table"][128:256])
        self.assertEqual([part["number"] for part in after["entries"] if part], [1, 2, 3, 4])
        self.assertEqual(after["entries"][2]["guid"], usb.ROOT_GUID)
        self.assertEqual(after["entries"][3]["guid"], usb.DATA_GUID)
        self.assertEqual(after["primary"]["disk_guid"], before["primary"]["disk_guid"])
        h_begin, h_end = usb.H_START*512, usb.H_START*512+usb.H_BYTES
        self.assertFalse(any(begin < h_end and end > h_begin for begin, end in writes))
        with self.target.open("rb") as disk:
            for offset, sentinel in zip(self.sentinels, sentinels):
                self.assertEqual(os.pread(disk.fileno(), 1024, offset), sentinel)
            self.assertEqual(os.pread(disk.fileno(), 512, 0), original_mbr)
            self.assertEqual(os.pread(disk.fileno(), 32, 2048*512+2*usb.MIB), bytes(32))
            for index, payload in enumerate(result["plan"]["copies"], 1):
                self.assertEqual(os.pread(disk.fileno(), 9, payload["target_offset"]), ("PAYLOAD-"+str(index)).encode())
        self.assertLess(self.target.stat().st_blocks*512, 16*usb.MIB)

    def test_failure_rolls_back_both_gpt_copies(self):
        before = self.read_target()
        with self.assertRaises(usb.PlanError):
            usb.apply_fixture(self.target, self.source, inject_failure=True)
        after = self.read_target()
        self.assertEqual(before["table"], after["table"])
        self.assertEqual(before["primary"]["raw"], after["primary"]["raw"])
        self.assertEqual(before["backup"]["raw"], after["backup"]["raw"])

    def test_block_paths_and_symlinks_cannot_enter_writable_fixture_path(self):
        for path in [Path("/dev/sdz"), Path("/dev/null")]:
            with self.assertRaises(usb.PlanError):
                usb.regular_fd(path, write=True)
        link = self.directory/"alias.img"
        link.symlink_to(self.target)
        with self.assertRaises(usb.PlanError):
            usb.regular_fd(link, write=True)

    def test_corrupt_table_rejected_before_writing(self):
        with self.target.open("r+b") as disk:
            disk.seek(1024+128+60)
            disk.write(b"bad")
        with self.assertRaises(usb.PlanError):
            usb.apply_fixture(self.target, self.source)

    def test_changed_h_geometry_rejected(self):
        target = self.read_target()
        target["entries"][1]["start"] += 1
        with self.source.open("rb") as source:
            with self.assertRaises(usb.PlanError):
                usb.build_plan(target, usb.read_gpt(source.fileno(), self.source.stat().st_size))


if __name__ == "__main__":
    unittest.main()
