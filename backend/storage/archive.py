"""A final copy of every finished round, committed under data/archive/ (see docs/DATA.md).

BasketNews can re-send a finished round today, but not forever, so each finished round is
saved once, in the tracker's own format (the adapters' records, not BasketNews fields):

    data/archive/<season>-<competition>/round-NN/players-<scoring>.json.gz
        every player's box score, fantasy points and club in the round, and the games
    data/archive/<season>-<competition>/round-NN/advanced.json.gz
        BasketNews advanced statistics of the round (values)
    data/archive/<season>-<competition>/leagues/<league>/round-NN.json.gz
        the league's standings after the round, its matchups and the transfers of the round
    data/archive/<season>-<competition>/leagues/<league>/draft.json.gz
        the draft picks

NN is the round as the site numbers it (round-01 is the first); inside, "round" is the
0-based index used everywhere in the code. Lineups and injuries are not repeated here:
data/lineups and data/injuries.json already are their history.

A round is written when it has finished. The latest finished round is rewritten when its
numbers change (stat corrections come within a day or two); older rounds are never touched.
Files are gzip with a fixed timestamp, so unchanged data never shows up as a git change.
"""
import gzip
import json

from backend import config, log
from backend.league import fetch_standings_round, raw_transfers, schedule
from backend.players import players
from backend.sources import basketnews as bn
from backend.sources.advanced import advanced_stats, unique_rows

LOG = log.get("storage")
VERSION = 1


def season_dir(meta):
    return config.DATA_DIR / "archive" / f"{meta.get('seasonYear')}-{meta['leagueId']}"


def round_name(rnd):
    return f"round-{rnd + 1:02d}"


def read(path):
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError, EOFError):
        return None


def write(path, doc, may_replace):
    """Write `doc` unless the file holds the same data (or exists and may not be replaced).
    True when the file was written."""
    old = read(path) if path.exists() else None
    if old is not None and (not may_replace or old == doc):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    tmp.replace(path)
    return True


def player_round(meta, rnd):
    """Every player's round: box score, fantasy points (this league's scoring) and club; the games."""
    views = players(meta, rnd, rnd)
    rows, games = [], {}
    for v in views.values():
        club = (v.get("club") or {}).get("abbr")
        rows.append({"id": v["id"], "bnId": v.get("bnId"), "name": v["name"], "position": v.get("position"),
                     "club": club, "fp": v.get("roundPts"), "played": v.get("roundPlayed"), "line": v.get("roundLine")})
        for g in v.get("games") or []:
            if not club:
                continue
            home, away = (club, g["opponent"]) if g["home"] else (g["opponent"], club)
            score = g.get("score")
            games.setdefault((g["at"], home, away), {
                "at": g["at"], "home": home, "away": away,
                "homeScore": (score[0] if g["home"] else score[1]) if score else None,
                "awayScore": (score[1] if g["home"] else score[0]) if score else None,
                "completed": g.get("completed"), "canceled": g.get("canceled")})
    rows.sort(key=lambda r: r["id"])
    return {"version": VERSION, "competition": meta["leagueId"], "season": meta.get("seasonYear"), "round": rnd,
            "pointCalcSystem": meta["pointCalcSystem"], "players": rows,
            "games": [games[k] for k in sorted(games)]}


def advanced_round(meta, rnd):
    rows = [{k: (v.get("value") if isinstance(v, dict) else v) for k, v in r.items()}
            for r in unique_rows(advanced_stats(meta, rnd))]
    rows.sort(key=lambda r: str(r.get("player_id")))
    return {"version": VERSION, "competition": meta["leagueId"], "season": meta.get("seasonYear"), "round": rnd,
            "rows": rows}


def league_round(meta, rnd, transfers):
    return {"version": VERSION, "league": {"id": meta["id"], "title": meta["title"], "format": meta["format"],
                                           "competition": meta["leagueId"], "season": meta.get("seasonYear")},
            "round": rnd, "standings": fetch_standings_round(meta, rnd),
            "matchups": schedule(meta, rnd) if meta["format"] == "head_to_head" else [],
            "transfers": transfers.get(rnd, [])}


def save_finished_rounds(metas):
    """Archive every finished round of the given leagues' competitions. Returns the files written."""
    written = []
    done_competition = set()
    for meta in metas:
        cur, latest_finished = meta["currentRound"], meta["currentRound"] - 1
        base = season_dir(meta)
        transfers = None
        for rnd in range(meta["firstRound"], cur):
            replaceable = rnd == latest_finished
            comp_key = (meta["leagueId"], meta.get("seasonYear"), meta["pointCalcSystem"], rnd)
            if comp_key not in done_competition:
                done_competition.add(comp_key)
                path = base / round_name(rnd) / f"players-{meta['pointCalcSystem']}.json.gz"
                if replaceable or not path.exists():
                    if write(path, player_round(meta, rnd), replaceable):
                        written.append(path)
                path = base / round_name(rnd) / "advanced.json.gz"
                if replaceable or not path.exists():
                    doc = advanced_round(meta, rnd)
                    if doc["rows"] and write(path, doc, replaceable):
                        written.append(path)
            path = base / "leagues" / meta["id"] / f"{round_name(rnd)}.json.gz"
            if replaceable or not path.exists():
                if transfers is None:
                    transfers = dict(raw_transfers(meta))
                if write(path, league_round(meta, rnd, transfers), replaceable):
                    written.append(path)
        picks = bn.fetch_draft_picks(meta["id"])
        if picks:
            path = base / "leagues" / meta["id"] / "draft.json.gz"
            if write(path, {"version": VERSION, "league": meta["id"], "picks": picks}, True):
                written.append(path)
    return written
