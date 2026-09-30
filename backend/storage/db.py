"""The SQLite index of the history: var/tracker.sqlite.

The history itself lives as text in data/ (committed, merged between devices by git); this
database is built from it on each device and answers questions like "who owned this player
in round 7" or "what did each manager score per round" with plain SQL. It can be deleted or
rebuilt at any time without losing anything.

Schema changes are numbered files in migrations/ (001_init.sql, 002_....sql). PRAGMA
user_version records the last one applied; connect() applies the missing ones in order,
each in a transaction. A migration that cannot be written as SQL can instead bump the
version and ask for a rebuild (the data is re-read from data/).
"""
import re
import sqlite3
from pathlib import Path

from backend import config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def path():
    return config.VAR_DIR / "tracker.sqlite"


def migrations():
    """[(version, sql)] in order."""
    out = []
    for file in sorted(MIGRATIONS.glob("*.sql")):
        m = re.match(r"(\d+)_", file.name)
        if m:
            out.append((int(m.group(1)), file.read_text(encoding="utf-8")))
    return out


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn):
    """Apply every migration newer than the database. Returns the versions applied."""
    applied = []
    for version, sql in migrations():
        if version <= schema_version(conn):
            continue
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        applied.append(version)
    return applied


def connect(db_path=None):
    db_path = db_path or path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def remove():
    for suffix in ("", "-wal", "-shm"):
        Path(str(path()) + suffix).unlink(missing_ok=True)
