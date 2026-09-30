"""Storage: the round archive, the SQLite index with its migrations, and live snapshots."""
import gzip
import json
import sqlite3

import pytest

from conftest import CLASSIC, HLA, run_replay

SEASON = "2026-6a7ae0128b647e038c999860"


def archive_dir(out):
    return out / "state" / "data" / "archive" / SEASON


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


# ---------------------------------------------------------------- archive

def test_finished_round_is_archived(replayed):
    out, _ = replayed
    base = archive_dir(out)
    players = read(base / "round-01" / "players-modern.json.gz")
    assert players["round"] == 0 and players["pointCalcSystem"] == "modern"
    assert len(players["players"]) > 250 and len(players["games"]) == 10
    played = [p for p in players["players"] if p["played"]]
    assert played and all(p["line"] and p["fp"] is not None for p in played)
    advanced = read(base / "round-01" / "advanced.json.gz")
    assert len(advanced["rows"]) > 150 and all(not isinstance(v, dict) for v in advanced["rows"][0].values())
    for fid in (HLA, CLASSIC):
        league = read(base / "leagues" / fid / "round-01.json.gz")
        assert league["league"]["id"] == fid and len(league["standings"]) == 8
        assert read(base / "leagues" / fid / "draft.json.gz")["picks"]
    assert len(read(base / "leagues" / HLA / "round-01.json.gz")["matchups"]) == 4
    assert not (base / "round-02").exists()  # the live round is not archived yet


def test_archive_writes_only_real_changes(ft, tmp_path):
    from backend.storage import archive
    path = tmp_path / "a.json.gz"
    assert archive.write(path, {"x": 1}, may_replace=True)
    first = path.read_bytes()
    assert not archive.write(path, {"x": 1}, may_replace=True)          # same data: untouched
    assert path.read_bytes() == first
    assert not archive.write(path, {"x": 2}, may_replace=False)         # settled round: never rewritten
    assert archive.write(path, {"x": 2}, may_replace=True)              # latest round: corrections land
    assert archive.read(path) == {"x": 2}
    assert gzip.compress(json.dumps({"x": 2}, separators=(",", ":"), sort_keys=True).encode(), 9, mtime=0) == \
        path.read_bytes()                                                # deterministic bytes


# ---------------------------------------------------------------- SQLite index

@pytest.fixture
def index(replayed, monkeypatch):
    from backend import config
    from backend.storage import db
    out, _ = replayed
    monkeypatch.setattr(config, "DATA_DIR", out / "state" / "data")
    monkeypatch.setattr(config, "VAR_DIR", out / "index-test")
    db.remove()
    conn = db.connect()
    yield conn
    conn.close()


def test_migrations_apply_once(index, tmp_path, monkeypatch):
    from backend.storage import db
    assert db.schema_version(index) == max(v for v, _ in db.migrations())
    assert db.migrate(index) == []                                       # nothing left to apply
    extra = tmp_path / "migrations"
    extra.mkdir()
    for version, sql in db.migrations():
        (extra / f"{version:03d}_x.sql").write_text(sql)
    (extra / "999_note.sql").write_text("ALTER TABLE players ADD COLUMN note TEXT;")
    monkeypatch.setattr(db, "MIGRATIONS", extra)
    assert db.migrate(index) == [999] and db.migrate(index) == []
    index.execute("SELECT note FROM players LIMIT 1")


def test_index_answers_history_questions(index):
    from backend.storage import ingest
    loaded = ingest.refresh(index)
    assert loaded["files"] == 9
    assert ingest.refresh(index) == {"files": 0, "rows": 0}             # nothing changed: nothing reloaded
    rounds = index.execute("SELECT round, COUNT(*) FROM manager_rounds WHERE league_id = ? GROUP BY round",
                           (HLA,)).fetchall()
    assert [tuple(r) for r in rounds] == [(0, 8)]
    owners = index.execute("SELECT COUNT(DISTINCT team_id) FROM ownership WHERE league_id = ? AND round = 1",
                           (HLA,)).fetchone()[0]
    assert owners == 8
    best = index.execute("SELECT player_id, fp FROM player_rounds WHERE round = 0 ORDER BY fp DESC LIMIT 1").fetchone()
    assert best["fp"] > 20
    assert index.execute("SELECT COUNT(*) FROM draft_picks WHERE league_id = ?", (CLASSIC,)).fetchone()[0] > 50
    assert index.execute("SELECT COUNT(*) FROM injury_episodes WHERE end IS NULL").fetchone()[0] > 10


def test_index_follows_changed_and_deleted_files(index, replayed):
    from backend.storage import ingest
    out, _ = replayed
    ingest.refresh(index)
    lineups = out / "state" / "data" / "lineups" / f"{HLA}.json"
    original = lineups.read_text()
    try:
        doc = json.loads(original)
        doc["rounds"].pop("0")
        lineups.write_text(json.dumps(doc))
        assert ingest.refresh(index)["files"] == 1
        assert index.execute("SELECT COUNT(*) FROM lineups WHERE league_id = ? AND round = 0", (HLA,)).fetchone()[0] == 0
        lineups.unlink()
        ingest.refresh(index)
        assert index.execute("SELECT COUNT(*) FROM lineups WHERE league_id = ?", (HLA,)).fetchone()[0] == 0
    finally:
        lineups.write_text(original)


def test_rebuild_from_scratch_gives_the_same_rows(index):
    from backend.storage import db, ingest
    ingest.refresh(index)
    before = {t: index.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ingest.SOURCE_TABLES}
    index.close()
    db.remove()
    conn = db.connect()
    ingest.refresh(conn, force=True)
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ingest.SOURCE_TABLES}
    conn.close()
    assert before == after


# ---------------------------------------------------------------- live snapshots

def test_a_live_snapshot_rebuilds_the_same_site(tmp_path, replayed):
    first = tmp_path / "first"
    run_replay(first, env={"FT_SNAPSHOTS": "1"})
    snaps = list((first / "var" / "snapshots" / "live").glob("*.json.gz"))
    assert len(snaps) == 1
    again = tmp_path / "again"
    report = run_replay(again, recording=snaps[0])
    assert report["exitCode"] == 0 and report["missingCount"] == 0
    original, _ = replayed
    for path in (original / "site" / "api").rglob("*.json"):
        if path.name != "health.json":
            rel = path.relative_to(original)
            assert (again / rel).read_bytes() == path.read_bytes(), rel


def test_snapshots_are_pruned(ft, tmp_path, monkeypatch):
    import os
    import time
    from backend.storage import snapshots
    folder = tmp_path / "live"
    folder.mkdir()
    now = time.time()
    for i in range(20):
        path = folder / f"2026093{i:02d}.json.gz"
        path.write_bytes(b"x")
        age = 3600 * (100 if i < 3 else 0)
        os.utime(path, (now - age, now - age))
    snapshots.prune(folder)
    left = sorted(p.name for p in folder.iterdir())
    assert len(left) == snapshots.KEEP and left[0] == "2026093" + "08.json.gz"


def test_snapshots_are_off_on_the_phone(monkeypatch):
    from backend.storage import snapshots
    monkeypatch.delenv("FT_SNAPSHOTS", raising=False)
    monkeypatch.setenv("FT_DEVICE", "phone")
    assert not snapshots.enabled()
    monkeypatch.setenv("FT_DEVICE", "mac")
    assert snapshots.enabled()
    monkeypatch.setenv("FT_SNAPSHOTS", "0")
    assert not snapshots.enabled()


def test_index_is_optional(ft, monkeypatch, caplog):
    import export
    from backend.storage import db

    def broken():
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(db, "connect", broken)
    monkeypatch.setattr(export.archive, "save_finished_rounds", lambda metas: [])
    export.store_history([])  # logs, does not raise
    assert "SQLite index not updated" in caplog.text
