"""Persistent cache of upstream answers that do not change any more, in var/cache.sqlite.

The in-memory caches of the sources last one run; this one survives between runs, so a
finished round's players, standings, schedule and transfers are downloaded once a week
instead of every 15 minutes (it matters most on the phone's mobile data). Live data --
the current and previous round, league settings, lineups, the injury report -- is never
kept here. How long each kind is kept is decided by rounds.keep_for().

Each row: key, source, fetched_at, expires_at, status, data (gzip JSON). Only good answers
are stored. The cache is safe to delete at any time (it is only a copy):

    python3 -m backend.cache stats
    python3 -m backend.cache clear [--source basketnews]
    FT_NO_CACHE=1 ./publish.sh          (neither read nor write it for one run)
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time

from backend import config, log

LOG = log.get("fetch")
DISABLED = bool(os.environ.get("FT_NO_CACHE"))
_local = threading.local()
_write_lock = threading.Lock()
STATS = {"hits": 0, "stored": 0}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS cache_expires ON cache (expires_at);
"""


def path():
    return config.VAR_DIR / "cache.sqlite"


def _conn():
    db = getattr(_local, "db", None)
    if db is None or getattr(_local, "path", None) != path():
        path().parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path(), timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        _local.db, _local.path = db, path()
    return db


def _key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def get(key):
    """The stored answer for `key`, or None (missing, expired, or the cache is off)."""
    if DISABLED:
        return None
    try:
        row = _conn().execute("SELECT data FROM cache WHERE key = ? AND expires_at > ? AND status = 'ok'",
                              (_key(key), time.time())).fetchone()
    except sqlite3.Error as exc:
        LOG.warning("cache unreadable (%s); going without it", exc)
        return None
    if row is None:
        return None
    STATS["hits"] += 1
    return json.loads(gzip.decompress(row[0]))


def put(key, source, data, keep):
    """Store an answer for `keep` seconds."""
    if DISABLED or not keep:
        return
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), compresslevel=6)
    now = time.time()
    try:
        with _write_lock:
            _conn().execute("INSERT OR REPLACE INTO cache (key, source, fetched_at, expires_at, status, data) "
                            "VALUES (?, ?, ?, ?, 'ok', ?)", (_key(key), source, now, now + keep, blob))
        STATS["stored"] += 1
    except sqlite3.Error as exc:
        LOG.warning("cache not written (%s)", exc)


def prune():
    """Drop expired rows (called once per build)."""
    if DISABLED or not path().exists():
        return 0
    with _write_lock:
        return _conn().execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),)).rowcount


def clear(source=None):
    if not path().exists():
        return 0
    with _write_lock:
        if source:
            return _conn().execute("DELETE FROM cache WHERE source = ?", (source,)).rowcount
        return _conn().execute("DELETE FROM cache").rowcount


def summary():
    if not path().exists():
        return []
    return _conn().execute(
        "SELECT source, COUNT(*), SUM(LENGTH(data)), MIN(expires_at) FROM cache GROUP BY source ORDER BY source"
    ).fetchall()


def main():
    parser = argparse.ArgumentParser(prog="python3 -m backend.cache", description="The persistent upstream cache.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="entries and size per source")
    clear_p = sub.add_parser("clear", help="delete cached answers")
    clear_p.add_argument("--source", help="only this source (e.g. basketnews)")
    args = parser.parse_args()
    if args.cmd == "clear":
        print(f"deleted {clear(args.source)} cached answer(s) from {path()}")
    else:
        rows = summary()
        for source, count, size, first_expiry in rows:
            print(f"{source:16} {count:5} entries  {size / 1e6:6.2f} MB  next expiry "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(first_expiry))}")
        print("empty" if not rows else f"{path()}")


if __name__ == "__main__":
    main()
