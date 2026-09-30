"""Git merge driver for the history files in data/, so two devices' changes combine instead
of conflicting.

publish.sh registers it (git config merge.ft-history.driver) and .gitattributes assigns it
to data/**/*.json. Git calls it with the common ancestor, our version and their version;
the merged result is written over ours. Nothing recorded on either side is dropped:

  data/lineups/<league>.json  every round of both; for a round both saved, the locked one,
                              then the later save
  data/injuries.json          every player of both; for a player both changed, the record with
                              the latest status change (it contains the older ones)
  data/proballers.json        every lookup of both; a found link beats "not found", then the
                              later check

    python3 tools/merge_history.py BASE OURS THEIRS PATH      (exit 0: merged into OURS)
"""
import json
import sys
from pathlib import Path


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def merge_lineups(ours, theirs):
    out = {**ours, "rounds": dict(ours.get("rounds") or {})}
    for rnd, snap in (theirs.get("rounds") or {}).items():
        mine = out["rounds"].get(rnd)
        if mine is None:
            out["rounds"][rnd] = snap
        elif (bool(snap.get("locked")), snap.get("savedAt") or "") > (bool(mine.get("locked")), mine.get("savedAt") or ""):
            out["rounds"][rnd] = snap
    return out


def _last_change(rec):
    stamps = [(u.get("date") or "", u.get("at") or "") for ep in rec.get("episodes") or [] for u in ep.get("updates") or []]
    stamps += [(ep.get("lastSeen") or "", "") for ep in rec.get("episodes") or []]
    return max(stamps, default=("", ""))


def merge_injuries(ours, theirs):
    players = dict(ours.get("players") or {})
    for bn_id, rec in (theirs.get("players") or {}).items():
        mine = players.get(bn_id)
        if mine is None or _last_change(rec) > _last_change(mine):
            players[bn_id] = rec
    out = {**ours, "players": players}
    stamps = [d.get("updatedAt") for d in (ours, theirs) if d.get("updatedAt")]
    if stamps:
        out["updatedAt"] = max(stamps)
    return out


def merge_proballers(ours, theirs):
    out = dict(ours)
    for key, rec in theirs.items():
        mine = out.get(key)
        if mine is None or (bool(rec.get("url")), rec.get("checked") or "") > (bool(mine.get("url")), mine.get("checked") or ""):
            out[key] = rec
    return out


def merge(path, ours, theirs):
    name = Path(path).name
    if "lineups" in Path(path).parts:
        return merge_lineups(ours, theirs)
    if name == "injuries.json":
        return merge_injuries(ours, theirs)
    if name == "proballers.json":
        return merge_proballers(ours, theirs)
    return None


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    _, ours_path, theirs_path, path = sys.argv[1:]
    ours, theirs = load(ours_path), load(theirs_path)
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        sys.exit(1)  # not JSON we know: leave the conflict to git
    merged = merge(path, ours, theirs)
    if merged is None:
        sys.exit(1)
    Path(ours_path).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
