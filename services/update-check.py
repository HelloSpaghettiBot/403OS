#!/usr/bin/env python3
"""Read-only GitHub release discovery. Never downloads or installs system images."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

API = "https://api.github.com/repos/HelloSpaghettiBot/403OS/releases/latest"
RELEASES = "https://github.com/HelloSpaghettiBot/403OS/releases"
TARGET = "surface-go1-x86_64"
ASSET = "403os-" + TARGET + ".img.xz"
VERSION = Path("/usr/share/surface/os-version.json")
RUNTIME = Path("/run/surface-updates")
MAX_RESPONSE = 512 * 1024
DAY = 24 * 60 * 60
RETRY = 15 * 60
STATES = {"unchecked", "available", "up_to_date", "no_release", "no_image",
          "offline", "rate_limited", "error"}


def version_key(value, development=False):
    if not isinstance(value, str):
        raise ValueError("Invalid OS version")
    match = re.fullmatch(r"v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\."
                         r"(0|[1-9][0-9]{0,5})(-dev)?", value)
    if not match or (match[4] and not development):
        raise ValueError("Expected a stable vMAJOR.MINOR.PATCH release")
    return tuple(int(match[i]) for i in (1, 2, 3)) + (0 if match[4] else 1,)


def identity():
    value = json.loads(VERSION.read_text())
    if (not isinstance(value, dict) or value.get("schema") != "403os-version-v1" or value.get("target") != TARGET
            or value.get("channel") != "stable"):
        raise ValueError("Unsupported OS update identity")
    version_key(value.get("version"), development=True)
    return value["version"]


def initial(current):
    return {"event": "update-status", "schema": "403os-update-status-v1",
            "current_version": current, "target": TARGET, "channel": "stable",
            "status": "unchecked", "update_available": False,
            "latest_version": "", "release_url": RELEASES,
            "message": "Updates have not been checked yet", "checked_at": "",
            "checked_monotonic": 0}


def cached(current):
    fallback = initial(current)
    try:
        path = RUNTIME / "status.json"
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > 16384):
            return fallback
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or value.get("event") != fallback["event"] or value.get("schema") != fallback["schema"]
                or value.get("current_version") != current or value.get("target") != TARGET
                or value.get("channel") != "stable" or value.get("status") not in STATES
                or type(value.get("update_available")) is not bool
                or type(value.get("checked_monotonic")) not in (int, float)
                or not 0 <= value["checked_monotonic"] <= time.monotonic()):
            return fallback
        return value
    except (OSError, ValueError, TypeError):
        return fallback


def fetch():
    # curl's TLS verification, complete-transfer deadline and response size limit
    # apply even to chunked replies. No redirects, curlrc, tokens or shell.
    result = subprocess.run([
        "/usr/bin/curl", "-q", "--proto", "=https", "--silent", "--show-error",
        "--connect-timeout", "6", "--max-time", "18", "--max-filesize", str(MAX_RESPONSE),
        "--header", "Accept: application/vnd.github+json",
        "--header", "X-GitHub-Api-Version: 2022-11-28",
        "--user-agent", "403OS-update-check/1", "--write-out", "\n%{http_code}", API,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=23, check=False)
    if result.returncode:
        if result.returncode == 63:
            raise ValueError("Release response exceeded the size limit")
        raise OSError("GitHub could not be reached securely")
    body, separator, code = result.stdout.rpartition(b"\n")
    if not separator or len(body) > MAX_RESPONSE or not re.fullmatch(rb"[0-9]{3}", code):
        raise ValueError("Invalid GitHub response")
    status = int(code)
    if status != 200:
        return status, None
    return status, json.loads(body)


def release_status(current, release):
    if not isinstance(release, dict):
        raise ValueError("Invalid release metadata")
    tag = release.get("tag_name")
    latest = version_key(tag)
    if (release.get("draft") is not False or release.get("prerelease") is not False
            or not isinstance(release.get("published_at"), str) or not release["published_at"]
            or release.get("html_url") != RELEASES + "/tag/" + tag):
        raise ValueError("Expected a published stable 403 OS release")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Missing release assets")
    matching = [item for item in assets if isinstance(item, dict) and item.get("name") == ASSET]
    ready = False
    if len(matching) == 1:
        asset = matching[0]
        digest = asset.get("digest")
        ready = (asset.get("state") == "uploaded" and type(asset.get("size")) is int
                 and 0 < asset["size"] <= 2 * 1024 ** 3
                 and asset.get("browser_download_url") == RELEASES + "/download/" + tag + "/" + ASSET
                 and isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None)
    newer = latest > version_key(current, development=True)
    state = "available" if newer and ready else "no_image" if newer else "up_to_date"
    message = {"available": "403 OS " + tag.removeprefix("v") + " is available. Open release details to get the update USB image",
               "no_image": "A newer release is published, but its Surface Go image is not ready",
               "up_to_date": "You are running the latest available stable version"}[state]
    return {"status": state, "latest_version": tag.removeprefix("v"),
            "release_url": release["html_url"], "update_available": state == "available", "message": message}


def prepare_runtime():
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Update status directory is not trusted")


def check(current, automatic=False):
    prepare_runtime()
    descriptor = os.open(RUNTIME / "check.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return cached(current)
        old = cached(current)
        age = time.monotonic() - old["checked_monotonic"]
        interval = (DAY if old["status"] in {"available", "up_to_date", "no_release", "no_image"}
                    else 60 * 60 if old["status"] == "rate_limited" else RETRY) if automatic else 60
        if old["status"] == "rate_limited":
            interval = max(interval, 60 * 60)
        if old["status"] != "unchecked" and 0 <= age < interval:
            return old
        result = dict(old)
        try:
            code, release = fetch()
            if code == 404:
                result = initial(current)
                result.update(status="no_release", message="No downloadable OS release has been published yet")
            elif code in {403, 429}:
                result.update(status="rate_limited", message="GitHub temporarily limited update checks; trying again later")
            elif code == 200:
                result.update(release_status(current, release))
            else:
                result.update(status="error", message="GitHub is temporarily unavailable; trying again later")
        except (OSError, subprocess.TimeoutExpired):
            result.update(status="offline", message="Unable to reach GitHub. Connect to the internet and check again")
        except (ValueError, TypeError, KeyError):
            result.update(status="error", message="The release information could not be validated; trying again later")
        result["checked_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        result["checked_monotonic"] = time.monotonic()
        with tempfile.NamedTemporaryFile(mode="w", dir=RUNTIME, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(result, stream, allow_nan=False)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.replace(temporary, RUNTIME / "status.json")
            finally:
                temporary.unlink(missing_ok=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    try:
        current = identity()
        if args.daemon:
            time.sleep(60)  # Network association and HOME startup take priority.
            while True:
                check(current, automatic=True)
                time.sleep(30)
        else:
            result = cached(current) if args.status else check(current)
            print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"event": "error", "message": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
