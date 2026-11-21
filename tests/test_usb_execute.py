import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


writer = module("usb_execute", ROOT/"installer/write-usb.py")
fixtures = module("usb_test_fixtures", ROOT/"tests/test_usb_plan.py")


class RegularFixtureIO(writer.DeviceIO):
    """Test-only backend. Production DeviceIO never accepts these descriptors."""
    def __init__(self, path, target, plan):
        self.fd = writer.usb.regular_fd(path, write=True)
        self.target, self.plan = target, plan
        self.writes = []

    def _pwrite(self, value, offset):
        self.writes.append((offset, offset+len(value)))
        writer.usb.fixture_write(self.fd, value, offset)
        return len(value)

    def drop_cache(self, _offset, _length):
        pass


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.USBPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.source = self.fixture.source.open("rb")
        self.addCleanup(self.source.close)
        self.target = self.fixture.read_target()
        source_gpt = writer.usb.read_gpt(self.source.fileno(), self.fixture.source.stat().st_size)
        self.plan = writer.usb.build_plan(self.target, source_gpt)
        # Exercise the exact transaction with tiny fixture payloads; the real
        # plan builder separately enforces the full reviewed partition sizes.
        for part in self.plan["copies"]:
            part["bytes"] = 4096
            value = os.pread(self.source.fileno(), 4096, part["source_offset"])
            part["sha256"] = hashlib.sha256(value).hexdigest()
        self.io = RegularFixtureIO(self.fixture.target, self.target, self.plan)
        self.addCleanup(os.close, self.io.fd)
        self.snapshots = writer.gpt_snapshots(self.io.fd, writer.usb.EXPECTED_BYTES)
        self.review = {"h_sentinels": writer.h_sentinels(self.io.read)}
        self.stamp = writer.source_stamp(self.source.fileno())

    def transaction(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return writer.run_transaction(self.source.fileno(), self.io, self.review,
                                          self.snapshots, self.stamp, **kwargs)

    def test_dense_copy_clears_old_bytes_and_preserves_h_and_gpt_entry(self):
        for part in self.plan["copies"]:
            self.io.write(b"W"*4096, part["target_offset"], "payload")
        result = self.transaction()
        self.assertEqual(result["status"], "PASS")
        for part in self.plan["copies"]:
            self.assertEqual(self.io.read(4096, part["target_offset"]),
                             os.pread(self.source.fileno(), 4096, part["source_offset"]))
        after = self.io.verify_gpt()
        self.assertEqual(after["table"][128:256], self.target["table"][128:256])
        self.assertEqual(writer.h_sentinels(self.io.read), self.review["h_sentinels"])
        h_first = writer.usb.H_START*512
        h_last = h_first+writer.usb.H_BYTES
        self.assertFalse(any(first < h_last and last > h_first for first, last in self.io.writes))

    def test_failure_restores_exact_gpt_snapshots_and_reports_partial_payload(self):
        result = self.transaction(inject_failure=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["gpt_rollback"], "PASS")
        self.assertTrue(result["payloads_may_be_partial"])
        self.assertEqual(result["h_sentinels"], "PASS")
        for offset, value in self.snapshots:
            self.assertEqual(self.io.read(len(value), offset), value)

    def test_readback_mismatch_rolls_back_partition_metadata(self):
        real_read = self.io.read
        first_payload = self.plan["copies"][0]["target_offset"]

        def changed(length, offset):
            value = real_read(length, offset)
            return bytes([value[0]^1])+value[1:] if offset == first_payload else value

        with mock.patch.object(self.io, "read", side_effect=changed):
            result = self.transaction()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("readback SHA-256", result["error"])
        self.assertEqual(result["gpt_rollback"], "PASS")

    def test_h_and_unreviewed_ranges_are_rejected_before_write(self):
        for offset, phase in [(writer.usb.H_START*512, "payload"), (0, "gpt"),
                              (64*1024**2, "payload"), (1, "unknown")]:
            with self.assertRaises(writer.ExecuteError):
                self.io.write(b"forbidden", offset, phase)
        self.assertEqual(self.io.writes, [])

    def test_production_backend_rejects_regular_fixture_descriptor(self):
        with self.assertRaisesRegex(writer.ExecuteError, "block descriptor"):
            writer.DeviceIO(self.io.fd, self.target, self.plan)

    def test_source_hash_covers_whole_file_and_partition_boundaries(self):
        path = self.fixture.directory/"small-hash-source"
        value = b"prefix"+bytes(8192)+b"suffix"
        path.write_bytes(value)
        with path.open("rb") as source:
            whole, parts = writer.hash_source(source.fileno(), len(value),
                [{"source_offset": 6, "bytes": 8192}, {"source_offset": 8198, "bytes": 6}])
        self.assertEqual(whole, hashlib.sha256(value).hexdigest())
        self.assertEqual(parts, [hashlib.sha256(bytes(8192)).hexdigest(), hashlib.sha256(b"suffix").hexdigest()])

    def test_changed_source_or_target_digest_blocks_before_execution(self):
        args = SimpleNamespace(expected_serial=writer.usb.EXPECTED_SERIAL,
            expected_model=writer.usb.EXPECTED_MODEL, expected_bytes=writer.usb.EXPECTED_BYTES,
            expected_h_uuid=writer.usb.H_FS_UUID, expected_h_partuuid=writer.usb.H_GUID,
            expected_h_start=writer.usb.H_START, expected_source_sha256="a"*64,
            expected_target_gpt_sha256=writer.snapshots_sha(self.snapshots))
        with mock.patch.object(writer, "unmounted"), \
             mock.patch.object(writer, "hash_source", return_value=("b"*64, ["c"*64]*3)):
            with self.assertRaisesRegex(writer.ExecuteError, "full SHA-256"):
                writer.build_review(args, self.source.fileno(), self.io.fd, {})
        args.expected_target_gpt_sha256 = "d"*64
        with mock.patch.object(writer, "unmounted"), mock.patch.object(writer, "hash_source") as hashing:
            with self.assertRaisesRegex(writer.ExecuteError, "GPT snapshot SHA"):
                writer.build_review(args, self.source.fileno(), self.io.fd, {})
        hashing.assert_not_called()


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        context = mock.patch.object(writer, "ROOT", self.root)
        context.start()
        self.addCleanup(context.stop)
        self.sha = "a"*64
        self.manifest = {"schema": "surface-usb-readiness-v1", "image_sha256": self.sha,
                         "installed_cold_boots": []}
        for index, mode in enumerate(["live", "test", "test"]):
            directory = self.root/"artifacts/vm-tests"/str(index)
            directory.mkdir(parents=True)
            checks = {name: "PASS" for name in writer.REQUIRED_CHECKS}
            checks["hardware"] = "UNTESTED"
            checks["tailscale_daemon_persistence"] = "NOT_VERIFIED" if mode == "live" else "PASS"
            result = dict(mode=mode, image_sha256=self.sha, status="PARTIAL", checks=checks,
                          installer_attached=mode == "live", direct_kernel_boot=False,
                          data_boot_count=index, disk=str(self.root/"installed.qcow2"),
                          tailscale_state_sha256="c"*64)
            command = ["qemu-system-x86_64", "-drive", "file=installed.qcow2,format=qcow2"]
            result_raw, command_raw = writer.canonical(result), writer.canonical(command)
            (directory/"result.json").write_bytes(result_raw)
            (directory/"command.json").write_bytes(command_raw)
            record = dict(status="PASS", cold_boot=True, result=str(directory/"result.json"),
                          result_sha256=writer.digest(result_raw), command_sha256=writer.digest(command_raw))
            if mode == "live":
                self.manifest["live_boot"] = record
            else:
                self.manifest["installed_cold_boots"].append(record)
        self.path = self.root/"readiness.json"

    def save(self):
        self.path.write_bytes(writer.canonical(self.manifest))

    def test_matching_image_and_distinct_passing_cold_boots_are_required(self):
        self.save()
        result = writer.validate_readiness(self.path, self.sha)
        self.assertEqual(result["data_boot_counts"], [1, 2])
        with self.assertRaises(writer.ExecuteError):
            writer.validate_readiness(self.path, "b"*64)
        self.manifest["installed_cold_boots"][1] = copy.deepcopy(self.manifest["installed_cold_boots"][0])
        self.save()
        with self.assertRaisesRegex(writer.ExecuteError, "distinct"):
            writer.validate_readiness(self.path, self.sha)

    def test_app_or_audio_failure_blocks_readiness_even_when_manifest_says_pass(self):
        record = self.manifest["installed_cold_boots"][1]
        result_path = Path(record["result"])
        result = json.loads(result_path.read_text())
        result["checks"]["audio_playback"] = "NOT_RUN"
        raw = writer.canonical(result)
        result_path.write_bytes(raw)
        record["result_sha256"] = writer.digest(raw)
        self.save()
        with self.assertRaisesRegex(writer.ExecuteError, "audio"):
            writer.validate_readiness(self.path, self.sha)

    def test_changed_evidence_digest_blocks_readiness(self):
        self.save()
        Path(self.manifest["installed_cold_boots"][1]["result"]).write_text("{}")
        with self.assertRaisesRegex(writer.ExecuteError, "digest changed"):
            writer.validate_readiness(self.path, self.sha)

    def test_changed_tailscale_state_blocks_installed_readiness(self):
        record = self.manifest["installed_cold_boots"][1]
        result_path = Path(record["result"])
        result = json.loads(result_path.read_text())
        result["tailscale_state_sha256"] = "d"*64
        raw = writer.canonical(result)
        result_path.write_bytes(raw)
        record["result_sha256"] = writer.digest(raw)
        self.save()
        with self.assertRaisesRegex(writer.ExecuteError, "Tailscale state SHA-256 changed"):
            writer.validate_readiness(self.path, self.sha)

    def test_mounted_partition_blocks_before_descriptor_open(self):
        disk = {"path": "/dev/sdz", "mountpoints": [], "children": [
            {"path": "/dev/sdz2", "mountpoints": ["/media/example-usb"]}]}
        with mock.patch("os.open") as opening:
            with self.assertRaisesRegex(writer.ExecuteError, "already be unmounted"):
                writer.unmounted(disk)
        opening.assert_not_called()

    def test_identity_pin_mismatch_is_rejected(self):
        args = SimpleNamespace(expected_serial="WRONG", expected_model=writer.usb.EXPECTED_MODEL,
            expected_bytes=writer.usb.EXPECTED_BYTES, expected_h_uuid=writer.usb.H_FS_UUID,
            expected_h_partuuid=writer.usb.H_GUID, expected_h_start=writer.usb.H_START)
        with self.assertRaisesRegex(writer.ExecuteError, "identity arguments"):
            writer.validate_expected(args)

    def test_execute_without_exact_confirmation_never_opens_writable_device(self):
        argv = ["write-usb.py", "--execute", "--device", "/dev/sdz", "--image", "/unused",
                "--readiness", "/unused", "--expected-serial", writer.usb.EXPECTED_SERIAL,
                "--expected-model", writer.usb.EXPECTED_MODEL, "--expected-bytes", str(writer.usb.EXPECTED_BYTES),
                "--expected-h-uuid", writer.usb.H_FS_UUID, "--expected-h-partuuid", writer.usb.H_GUID,
                "--expected-h-start", str(writer.usb.H_START), "--expected-source-sha256", self.sha,
                "--expected-target-gpt-sha256", "b"*64]
        with mock.patch("sys.argv", argv), mock.patch.object(writer, "open_exclusive") as opening, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(writer.main(), 1)
        opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
