"""Load the history files of data/ into the SQLite index (db.py).

Each file's rows carry its path (source_file); a file whose content hash changed since the
last load is replaced as a whole, a deleted file's rows are removed, and unchanged files are
skipped, so refreshing after every build takes a moment. Reading only -- data/ is never
written here.
"""
import gzip
import hashlib
import json

from backend import clock, config

SOURCE_TABLES = ("player_rounds", "games", "advanced_rounds", "standings", "matchups", "transfers",
                 "transfer_players", "draft_picks", "lineups", "lineup_slots", "injury_episodes", "injury_updates")
LINE_KEYS = ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "eff", "p2m", "p2a", "p3m",
             "p3a", "ftm", "fta", "usg")


def history_files():
    """{relative path: Path} of every file the index is built from."""
    data = config.DATA_DIR
    files = {}
    for path in sorted((data / "lineups").glob("*.json")):
        files[f"lineups/{path.name}"] = path
    if (data / "injuries.json").exists():
        files["injuries.json"] = data / "injuries.json"
    for path in sorted((data / "archive").rglob("*.json.gz")):
        files[str(path.relative_to(data))] = path
    return files


def _load(path):
    raw = path.read_bytes()
    text = gzip.decompress(raw) if path.suffix == ".gz" else raw
    return hashlib.sha256(raw).hexdigest(), json.loads(text)


def _lineups(conn, rel, doc):
    fid = rel.split("/")[-1][:-len(".json")]
    n = 0
    for rnd, snap in (doc.get("rounds") or {}).items():
        for tid, team in (snap.get("teams") or {}).items():
            conn.execute("INSERT OR REPLACE INTO lineups VALUES (?,?,?,?,?,?,?,?)",
                         (fid, int(rnd), tid, snap.get("savedAt"), int(bool(snap.get("locked"))), snap.get("source"),
                          team.get("formation"), rel))
            for p in team.get("players") or []:
                conn.execute("INSERT INTO lineup_slots VALUES (?,?,?,?,?,?,?,?)",
                             (fid, int(rnd), tid, p["id"], p.get("card"), p.get("slot"), int(bool(p.get("captain"))), rel))
                n += 1
    return n


def _injuries(conn, rel, doc):
    n = 0
    for bn_id, rec in (doc.get("players") or {}).items():
        for i, ep in enumerate(rec.get("episodes") or []):
            conn.execute("INSERT OR REPLACE INTO injury_episodes VALUES (?,?,?,?,?,?,?,?)",
                         (bn_id, i, rec.get("name"), rec.get("club"), ep.get("start"), ep.get("end"), ep.get("lastSeen"), rel))
            for j, u in enumerate(ep.get("updates") or []):
                conn.execute("INSERT OR REPLACE INTO injury_updates VALUES (?,?,?,?,?,?,?,?,?)",
                             (bn_id, i, j, u.get("date"), u.get("at"), u.get("status"), u.get("return"),
                              u.get("comment"), rel))
                n += 1
    return n


def _player_round(conn, rel, doc):
    season, comp, rnd, pcs = doc["season"], doc["competition"], doc["round"], doc["pointCalcSystem"]
    n = 0
    for p in doc.get("players") or []:
        line = p.get("line") or {}
        conn.execute("INSERT OR REPLACE INTO players VALUES (?,?,?,?)", (p["id"], p.get("bnId"), p.get("name"),
                                                                         p.get("position")))
        conn.execute(f"INSERT OR REPLACE INTO player_rounds VALUES (?,?,?,?,?,?,?,?,{','.join('?' * len(LINE_KEYS))},?)",
                     (season, comp, rnd, pcs, p["id"], p.get("club"), p.get("fp"), int(bool(p.get("played"))),
                      *(line.get(k) for k in LINE_KEYS), rel))
        n += 1
    for g in doc.get("games") or []:
        conn.execute("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (season, comp, rnd, g["at"], g["home"], g["away"], g.get("homeScore"), g.get("awayScore"),
                      int(bool(g.get("completed"))), int(bool(g.get("canceled"))), rel))
        n += 1
    return n


def _advanced(conn, rel, doc):
    n = 0
    for row in doc.get("rows") or []:
        conn.execute("INSERT OR REPLACE INTO advanced_rounds VALUES (?,?,?,?,?,?)",
                     (doc["season"], doc["competition"], doc["round"], str(row.get("player_id")),
                      json.dumps(row, sort_keys=True), rel))
        n += 1
    return n


def _league_round(conn, rel, doc):
    lg, rnd = doc["league"], doc["round"]
    fid = lg["id"]
    conn.execute("INSERT OR REPLACE INTO leagues VALUES (?,?,?,?,?,?)",
                 (fid, lg.get("title"), lg.get("format"), lg.get("competition"), lg.get("season"), rel))
    n = 0
    for r in doc.get("standings") or []:
        team = r["team"]
        conn.execute("INSERT OR REPLACE INTO teams VALUES (?,?,?,?)", (fid, team["id"], team.get("title"),
                                                                       team.get("owner")))
        conn.execute("INSERT OR REPLACE INTO standings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (fid, rnd, team["id"], r.get("position"), r.get("pointsRound"), r.get("pointsTotal"),
                      r.get("wins"), r.get("losses"), r.get("ties"), r.get("roundPosition"), rel))
        n += 1
    for m in doc.get("matchups") or []:
        conn.execute("INSERT OR REPLACE INTO matchups VALUES (?,?,?,?,?,?,?,?)",
                     (fid, rnd, m["id"], (m.get("team1") or {}).get("id"), (m.get("team2") or {}).get("id"),
                      m.get("score1"), m.get("score2"), rel))
        n += 1
    for t in doc.get("transfers") or []:
        offer, request = t["offer"], t["request"]
        conn.execute("INSERT OR REPLACE INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (fid, t["id"], rnd, t["kind"], t.get("at"), (offer.get("team") or {}).get("id"),
                      (request.get("team") or {}).get("id"), offer.get("credits"), request.get("credits"), rel))
        for side_name, side in (("offer", offer), ("request", request)):
            for p in side.get("players") or []:
                conn.execute("INSERT INTO transfer_players VALUES (?,?,?,?,?,?)",
                             (fid, t["id"], side_name, p["id"], p.get("name"), rel))
        n += 1
    return n


def _draft(conn, rel, doc):
    n = 0
    for i, pick in enumerate(doc.get("picks") or []):
        conn.execute("INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?)",
                     (doc["league"], i + 1, pick.get("teamId"), pick.get("playerId"), rel))
        n += 1
    return n


def _handler(rel):
    name = rel.split("/")[-1]
    if rel.startswith("lineups/"):
        return _lineups
    if rel == "injuries.json":
        return _injuries
    if "/leagues/" in rel:
        return _draft if name == "draft.json.gz" else _league_round
    if name.startswith("players-"):
        return _player_round
    if name == "advanced.json.gz":
        return _advanced
    return None


def _forget(conn, rel):
    for table in SOURCE_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE source_file = ?", (rel,))
    conn.execute("DELETE FROM ingested WHERE path = ?", (rel,))


def refresh(conn, force=False):
    """Bring the index up to date with data/. Returns {"files": changed files, "rows": rows loaded}."""
    known = {r["path"]: r["sha"] for r in conn.execute("SELECT path, sha FROM ingested")}
    files = history_files()
    changed = rows = 0
    with conn:
        for rel in set(known) - set(files):
            _forget(conn, rel)
            changed += 1
        for rel, path in files.items():
            handler = _handler(rel)
            if handler is None:
                continue
            sha, doc = _load(path)
            if not force and known.get(rel) == sha:
                continue
            _forget(conn, rel)
            n = handler(conn, rel, doc)
            conn.execute("INSERT INTO ingested VALUES (?,?,?,?)",
                         (rel, sha, n, clock.now().isoformat(timespec="seconds")))
            changed += 1
            rows += n
    return {"files": changed, "rows": rows}
