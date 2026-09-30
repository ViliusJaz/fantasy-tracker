"""What the public API does not keep, recorded under data/: every round's lineups and the
injury report turned into per-player episodes.

Fetching never writes here directly: fetchers hand what they saw to observe_lineups() /
observe_injury_report(), and flush() writes it. That way a run can look at the new data
before any of it becomes history (and drop it with discard())."""
import copy
import json
import threading
from datetime import date

from backend import clock, config
from backend.rounds import is_live
from backend.sources.injury_report import is_injury
from backend.util import read_json, write_json


_snapshot_lock = threading.Lock()
_pending_lock = threading.Lock()
_pending_lineups = {}  # league id -> (meta, lineups): the latest fetch not written yet
_pending_injuries = []  # (report key, entries) fetched but not written yet


def observe_lineups(meta, lineup_by_team):
    """Queue a fetch of every team's current lineup for flush()."""
    with _pending_lock:
        _pending_lineups[meta["id"]] = (meta, copy.deepcopy(lineup_by_team))


def observe_injury_report(entries, key=None):
    """Queue a freshly downloaded injury report (`key`: which report) for flush()."""
    with _pending_lock:
        _pending_injuries.append((key, copy.deepcopy(entries)))


def pending():
    """What flush() would write: {"lineups": {leagueId: (meta, lineups)}, "injuries": [(key, entries)]}."""
    with _pending_lock:
        return {"lineups": dict(_pending_lineups), "injuries": list(_pending_injuries)}


def discard(lineups=True, injuries=True):
    """Drop queued data (all of it, or one kind) instead of writing it."""
    with _pending_lock:
        if lineups:
            _pending_lineups.clear()
        if injuries:
            _pending_injuries.clear()


def flush():
    """Write everything queued. Returns how many files changed: {"lineups": n, "injuries": n}."""
    with _pending_lock:
        lineups, reports = list(_pending_lineups.values()), list(_pending_injuries)
        _pending_lineups.clear()
        _pending_injuries.clear()
    changed = {"lineups": 0, "injuries": 0}
    for meta, lineup_by_team in lineups:
        changed["lineups"] += save_lineup_snapshot(meta, lineup_by_team)
    for _, entries in reports:
        changed["injuries"] += update_injury_log(entries)
    return changed


def lineup_rounds(fid):
    """Saved lineups of a league: {"<round>": {savedAt, locked, teams: {teamId: {formation, players}}}}."""
    return read_json(config.LINEUPS_DIR / f"{fid}.json", {"rounds": {}})["rounds"]


def injury_log():
    """Recorded injury episodes: {bnPlayerId: {name, club, episodes: [...]}}."""
    return read_json(config.INJURY_LOG_FILE, {"players": {}})["players"]


def save_lineup_snapshot(meta, lineup_by_team):
    """Store each round's lineups; a round locks once it starts. True when the file changed."""
    by_round = {}
    for team_id, lu in lineup_by_team.items():
        by_round.setdefault(lu["round"], {})[team_id] = {"formation": lu["formation"], "players": lu["players"]}
    path = config.LINEUPS_DIR / f"{meta['id']}.json"
    with _snapshot_lock:
        store = read_json(path, {"rounds": {}})
        changed = False
        for rnd, teams in by_round.items():
            key = str(rnd)
            locked = rnd < meta["currentRound"] or is_live(meta, rnd)
            old = store["rounds"].get(key)
            if old and old.get("locked") and not locked:
                continue
            if old and old["teams"] == teams and old.get("locked") == locked:
                continue
            store["rounds"][key] = {"savedAt": clock.now().isoformat(timespec="seconds"),
                                    "locked": locked, "teams": teams}
            changed = True
        if changed:
            write_json(path, store)
    return changed


def lineup_snapshot(meta, rnd, team_id):
    snap = lineup_rounds(meta["id"]).get(str(rnd))
    if not snap or team_id not in snap["teams"]:
        return None
    return {**snap["teams"][team_id], "savedAt": snap["savedAt"], "locked": snap.get("locked", False),
            "source": snap.get("source")}


REMOVED_NOTE = "Išbrauktas iš traumų sąrašo"  # stored in the log when a player leaves the report

_log_lock = threading.Lock()


def update_injury_log(entries):
    """Turn daily report snapshots into per-player episodes (start, end, status changes).
    True when the log changed."""
    today = clock.today().isoformat()
    now = clock.now().astimezone().isoformat(timespec="minutes")  # when the change was first seen
    with _log_lock:
        log = read_json(config.INJURY_LOG_FILE, {"players": {}})
        before = json.dumps(log["players"], sort_keys=True)
        known = log["players"]
        seen = set()
        for e in entries:
            if not e["bnId"]:
                continue
            seen.add(e["bnId"])
            rec = known.setdefault(e["bnId"], {"episodes": []})
            rec["name"], rec["club"] = e["name"], e["club"]
            episodes = rec["episodes"]
            open_ep = episodes[-1] if episodes and episodes[-1]["end"] is None else None
            update = {"date": today, "at": now, "status": e["status"], "return": e["return"], "comment": e["comment"]}
            if e["status"] == "ready":
                if open_ep:
                    open_ep["end"] = today
                    _add_update(open_ep, update)
                elif not episodes and is_injury(e["comment"]):
                    # First sighting of someone already recovered: keep the finished injury.
                    episodes.append({"start": today, "end": today, "lastSeen": today, "updates": [update]})
                continue
            if open_ep:
                open_ep["lastSeen"] = today
                _add_update(open_ep, update)
            else:
                episodes.append({"start": today, "end": None, "lastSeen": today, "updates": [update]})
        for bn_id, rec in known.items():
            episodes = rec["episodes"]
            if bn_id in seen or not episodes or episodes[-1]["end"] is not None:
                continue
            ep = episodes[-1]
            gap = (date.fromisoformat(today) - date.fromisoformat(ep["lastSeen"])).days
            ep["end"] = today if gap <= 1 else ep["lastSeen"]
            _add_update(ep, {"date": ep["end"], "at": now if gap <= 1 else None, "status": "ready", "return": "",
                             "comment": REMOVED_NOTE})
        # Only touch the file when something changed, so the GitHub copy is not re-committed every run.
        if json.dumps(known, sort_keys=True) != before or not config.INJURY_LOG_FILE.exists():
            log["updatedAt"] = clock.now().isoformat(timespec="seconds")
            write_json(config.INJURY_LOG_FILE, log)
            return True
    return False


def _add_update(episode, update):
    last = episode["updates"][-1] if episode["updates"] else {}
    if any(last.get(k) != update[k] for k in ("status", "return", "comment")):
        episode["updates"].append(update)
