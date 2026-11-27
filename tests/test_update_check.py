"""Release discovery tests use synthetic releases and temporary RAM-state paths."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_check", ROOT / "services/update-check.py")
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


def release(tag="v0.2.0"):
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "published_at": "2026-11-26T00:00:00Z", "html_url": updates.RELEASES + "/tag/" + tag,
            "assets": [{"name": updates.ASSET, "state": "uploaded", "size": 1000,
                        "digest": "sha256:" + "a" * 64,
                        "browser_download_url": updates.RELEASES + "/download/" + tag + "/" + updates.ASSET}]}


class ReleaseTests(unittest.TestCase):
    def test_newer_matching_image_is_available(self):
        result = updates.release_status("0.1.0-dev", release())
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["release_url"], updates.RELEASES + "/tag/v0.2.0")

    def test_stable_supersedes_development_build(self):
        self.assertTrue(updates.release_status("0.1.0-dev", release("v0.1.0"))["update_available"])

    def test_numeric_version_order_and_no_downgrade(self):
        self.assertGreater(updates.version_key("v0.10.0"), updates.version_key("v0.9.9"))
        for current in ("0.2.0", "0.3.0-dev", "1.0.0"):
            result = updates.release_status(current, release())
            self.assertEqual(result["status"], "up_to_date")
            self.assertFalse(result["update_available"])

    def test_unpublished_prerelease_and_foreign_links_rejected(self):
        for key, value in (("draft", True), ("prerelease", True), ("published_at", ""),
                           ("html_url", "https://example.invalid/releases/tag/v0.2.0"),
                           ("tag_name", "v0.2.0-beta"), ("tag_name", "main"), ("assets", None)):
            with self.subTest(key=key, value=value):
                candidate = release()
                candidate[key] = value
                with self.assertRaises(ValueError):
                    updates.release_status("0.1.0-dev", candidate)

    def test_source_only_and_other_architectures_not_installable(self):
        candidate = release()
        candidate["assets"] = []
        self.assertEqual(updates.release_status("0.1.0-dev", candidate)["status"], "no_image")
        candidate["assets"] = release()["assets"]
        candidate["assets"][0]["name"] = "403os-aarch64.img.xz"
        self.assertFalse(updates.release_status("0.1.0-dev", candidate)["update_available"])

    def test_bad_or_unfinished_assets_not_advertised(self):
        for key, value in (("size", 0), ("size", True), ("size", 2 * 1024 ** 3 + 1),
                           ("state", "new"), ("digest", None), ("digest", "sha256:no"),
                           ("browser_download_url", "http://github.com/insecure")):
            with self.subTest(key=key, value=value):
                candidate = release()
                candidate["assets"][0][key] = value
                self.assertEqual(updates.release_status("0.1.0-dev", candidate)["status"], "no_image")
        candidate = release()
        candidate["assets"].append(copy.deepcopy(candidate["assets"][0]))
        self.assertFalse(updates.release_status("0.1.0-dev", candidate)["update_available"])

    def test_invalid_versions(self):
        for value in (None, 2, "", "v01.2.0", "1.2", "1.2.3.4", "1.2.3-beta", "1.2.3\n", "1.2.3;reboot"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                updates.version_key(value)


class FetchTests(unittest.TestCase):
    def test_fixed_https_request_has_deadline_and_size_limit(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(release()).encode() + b"\n200", b"")
        with patch.object(updates.subprocess, "run", return_value=completed) as run:
            self.assertEqual(updates.fetch(), (200, release()))
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/bin/curl", "-q"])
        self.assertEqual(args[-1], updates.API)
        self.assertNotIn("--location", args)
        self.assertNotIn("--insecure", args)
        self.assertIn("--max-time", args)
        self.assertIn("--max-filesize", args)
        self.assertEqual(run.call_args.kwargs["timeout"], 23)

    def test_missing_release_rate_limit_server_and_redirect(self):
        for code in (404, 403, 429, 503, 301):
            with patch.object(updates.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"{}\n" + str(code).encode(), b"")):
                self.assertEqual(updates.fetch(), (code, None))

    def test_transport_tls_and_oversized_responses(self):
        for code in (6, 28, 60, 63):
            with patch.object(updates.subprocess, "run", return_value=subprocess.CompletedProcess([], code, b"", b"private diagnostic")):
                with self.assertRaises(ValueError if code == 63 else OSError):
                    updates.fetch()
        for body in (b"garbage", b"{}\nbad", b"[bad]\n200", b"x" * (updates.MAX_RESPONSE + 1) + b"\n200"):
            with patch.object(updates.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, body, b"")):
                with self.assertRaises(ValueError):
                    updates.fetch()


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / "updates"
        self.path_patch = patch.object(updates, "RUNTIME", self.runtime)
        self.path_patch.start()
        # Test fake root-owned RAM files without elevating the host process.
        original_lstat = Path.lstat
        def synthetic_root(path):
            info = original_lstat(path)
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        self.stat_patch = patch.object(Path, "lstat", synthetic_root)
        self.stat_patch.start()

    def tearDown(self):
        self.stat_patch.stop()
        self.path_patch.stop()
        self.temporary.cleanup()

    def test_first_check_and_atomic_cache_permissions(self):
        with patch.object(updates, "fetch", return_value=(200, release())) as fetch:
            result = updates.check("0.1.0-dev")
            self.assertTrue(result["update_available"])
            self.assertEqual(updates.cached("0.1.0-dev"), result)
            self.assertEqual((self.runtime / "status.json").stat().st_mode & 0o777, 0o644)
            self.assertEqual(updates.check("0.1.0-dev"), result)
            fetch.assert_called_once()
        self.assertEqual(sorted(item.name for item in self.runtime.iterdir()), ["check.lock", "status.json"])

    def test_no_release_message_is_honest(self):
        with patch.object(updates, "fetch", return_value=(404, None)):
            result = updates.check("0.1.0-dev")
        self.assertEqual(result["status"], "no_release")
        self.assertFalse(result["update_available"])
        self.assertIn("No downloadable OS release", result["message"])

    def test_offline_keeps_known_release_without_installing(self):
        with patch.object(updates, "fetch", return_value=(200, release())):
            result = updates.check("0.1.0-dev")
        with patch.object(updates.time, "monotonic", return_value=result["checked_monotonic"] + 61):
            with patch.object(updates, "fetch", side_effect=OSError("offline")):
                offline = updates.check("0.1.0-dev")
        self.assertEqual(offline["status"], "offline")
        self.assertTrue(offline["update_available"])
        self.assertEqual(offline["release_url"], result["release_url"])

    def test_auto_daily_and_offline_retry(self):
        with patch.object(updates, "fetch", return_value=(404, None)) as fetch:
            result = updates.check("0.1.0-dev", automatic=True)
            with patch.object(updates.time, "monotonic", return_value=result["checked_monotonic"] + updates.DAY - 1):
                updates.check("0.1.0-dev", automatic=True)
                self.assertEqual(fetch.call_count, 1)
            with patch.object(updates.time, "monotonic", return_value=result["checked_monotonic"] + updates.DAY):
                updates.check("0.1.0-dev", automatic=True)
                self.assertEqual(fetch.call_count, 2)

    def test_failure_states_and_timeout(self):
        for expected, reply in (("offline", subprocess.TimeoutExpired("curl", 23)),
                                ("error", ValueError("bad")), ("error", (503, None)),
                                ("rate_limited", (403, None)), ("rate_limited", (429, None))):
            with self.subTest(expected=expected, reply=reply):
                (self.runtime / "status.json").unlink(missing_ok=True)
                options = {"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply}
                with patch.object(updates, "fetch", **options):
                    self.assertEqual(updates.check("0.1.0-dev")["status"], expected)

    def test_automatic_offline_retry_and_rate_limit_backoff(self):
        for code, interval in ((503, updates.RETRY), (429, 3600)):
            (self.runtime / "status.json").unlink(missing_ok=True)
            with patch.object(updates, "fetch", return_value=(code, None)) as fetch:
                result = updates.check("0.1.0-dev", automatic=True)
                with patch.object(updates.time, "monotonic", return_value=result["checked_monotonic"] + interval - 1):
                    updates.check("0.1.0-dev", automatic=True)
                    self.assertEqual(fetch.call_count, 1)
                with patch.object(updates.time, "monotonic", return_value=result["checked_monotonic"] + interval):
                    updates.check("0.1.0-dev", automatic=True)
                    self.assertEqual(fetch.call_count, 2)

    def test_changed_version_or_bad_cache_is_rechecked(self):
        self.runtime.mkdir()
        for value in ([], {}, {"schema": "old"}):
            (self.runtime / "status.json").write_text(json.dumps(value))
            self.assertEqual(updates.cached("0.1.0-dev")["status"], "unchecked")
        with patch.object(updates, "fetch", return_value=(404, None)) as fetch:
            updates.check("0.1.0-dev")
            updates.check("0.2.0-dev")
            self.assertEqual(fetch.call_count, 2)

    def test_status_symlink_and_writable_runtime_rejected(self):
        self.runtime.mkdir()
        target = self.runtime / "other"
        target.write_text(json.dumps(updates.initial("0.1.0-dev")))
        (self.runtime / "status.json").symlink_to(target)
        self.assertEqual(updates.cached("0.1.0-dev")["status"], "unchecked")
        self.runtime.chmod(0o777)
        with patch.object(updates, "fetch") as fetch, self.assertRaises(ValueError):
            updates.check("0.1.0-dev")
        fetch.assert_not_called()

    def test_concurrent_checker_does_not_duplicate_requests(self):
        self.runtime.mkdir()
        with (self.runtime / "check.lock").open("w") as lock:
            updates.fcntl.flock(lock, updates.fcntl.LOCK_EX | updates.fcntl.LOCK_NB)
            with patch.object(updates, "fetch") as fetch:
                self.assertEqual(updates.check("0.1.0-dev")["status"], "unchecked")
                fetch.assert_not_called()

    def test_build_identity_and_invalid_identity(self):
        with patch.object(updates, "VERSION", ROOT / "config/os-version.json"):
            self.assertEqual(updates.identity(), "0.1.0-dev")
        metadata = Path(self.temporary.name) / "identity.json"
        for value in ([], {}, {"schema": "403os-version-v1", "target": "other"}):
            metadata.write_text(json.dumps(value))
            with patch.object(updates, "VERSION", metadata), self.assertRaises(ValueError):
                updates.identity()


if __name__ == "__main__":
    unittest.main()
