"""Recent live snapshots: every upstream answer a build used, kept for a while on this device.

Each build on the Mac saves var/snapshots/live/<time>.json.gz: the state of data/ before the
build plus every answer it used (downloaded or taken from the cache). It is the same format
as the test recordings, so any recent build can be rebuilt offline, byte for byte:

    python3 tools/replay_export.py replay var/snapshots/live/<time>.json.gz /tmp/rebuilt

Only the newest KEEP builds younger than MAX_AGE hours are kept (about 1.5 MB each). Finished
rounds are preserved for good by archive.py instead. Off on the phone (mobile storage);
FT_SNAPSHOTS=1 / 0 turns it on or off anywhere.
"""
import base64
import gzip
import hashlib
import json
import os
import threading
import time

from backend import clock, config, health, log, net

LOG = log.get("storage")
KEEP = 12
MAX_AGE = 48  # hours
VERSION = 1


def request_key(method, url, data):
    """Same key as tools/replay.py, so snapshots and test recordings are interchangeable."""
    digest = hashlib.sha1(data).hexdigest()[:20] if data else "-"
    return f"{method} {url} {digest}"


def _text(body):
    try:
        return {"text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(body).decode()}


class Recorder:
    def __init__(self, files):
        self.files = files
        self.responses = {}
        self.started = clock.now().astimezone().isoformat(timespec="seconds")
        self._lock = threading.Lock()

    def remember(self, method, url, data, body, status=200, content_type=None):
        entry = {"status": status, "headers": {"Content-Type": content_type}, "url": url, **_text(body)}
        if data:
            entry["request"] = data.decode("utf-8", errors="replace")
        with self._lock:
            self.responses.setdefault(request_key(method, url, data), entry)


_active = None


def enabled():
    flag = os.environ.get("FT_SNAPSHOTS")
    return flag == "1" if flag in ("0", "1") else health.device() == "mac"


def start():
    """Begin recording this build (when enabled): net.fetch and cache hits report to it."""
    global _active
    if not enabled():
        return None
    files = {}
    for path in [config.LEAGUES_FILE, *sorted(config.DATA_DIR.rglob("*.json"))]:
        if path.is_file() and "archive" not in path.parts:
            rel = "leagues.json" if path == config.LEAGUES_FILE else f"data/{path.relative_to(config.DATA_DIR)}"
            files[rel] = path.read_text(encoding="utf-8")
    _active = Recorder(files)
    net.RECORDER = _active.remember
    return _active


def remember(method, url, data, body):
    """Report an answer served from the cache, so the snapshot still has it."""
    if _active is not None:
        _active.remember(method, url, data, body)


def save(recorder):
    """Write the snapshot and prune old ones. Returns the path, or None."""
    global _active
    net.RECORDER = None
    _active = None
    if recorder is None:
        return None
    folder = config.VAR_DIR / "snapshots" / "live"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.json.gz"
    doc = {"version": VERSION, "recordedAt": recorder.started, "files": recorder.files,
           "responses": recorder.responses}
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(doc, fh, ensure_ascii=False, sort_keys=True)
    prune(folder)
    return path


def prune(folder):
    snaps = sorted(folder.glob("*.json.gz"), reverse=True)
    cutoff = time.time() - MAX_AGE * 3600
    for i, path in enumerate(snaps):
        if i >= KEEP or path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
