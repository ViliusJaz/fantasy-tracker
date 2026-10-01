"""/api/league/<id>/standings and /rounds."""
from backend.league import fetch_standings_round, league_meta, schedule, standings
from backend.live import live_matchups, live_table
from backend.projections import matchup_projections
from backend.rounds import is_live


def standings_payload(fid, rnd=None):
    meta = league_meta(fid)
    shown, rows = standings(meta, rnd)
    live = is_live(meta, shown)
    if live and meta["format"] != "head_to_head":  # points leagues: the live round counts right away
        rows = live_table(meta, shown, rows, fetch_standings_round(meta, shown - 1) if shown > meta["firstRound"] else [])
    return {
        "league": meta,
        "round": shown,
        "live": live,
        "rows": rows,
        "hasTies": any(r.get("ties") for r in rows),
    }


def rounds_payload(fid, rnd=None):
    meta = league_meta(fid)
    if meta["format"] == "head_to_head":
        rnd = meta["currentRound"] if rnd is None else rnd
        matchups = schedule(meta, rnd)
        if rnd == meta["currentRound"]:
            matchups = matchup_projections(meta, rnd, live_matchups(meta, rnd, matchups))
        return {"league": meta, "round": rnd, "live": is_live(meta, rnd), "matchups": matchups}
    rnd = meta["latestRound"] if rnd is None else rnd
    rows = fetch_standings_round(meta, rnd)
    if is_live(meta, rnd):
        rows = live_table(meta, rnd, rows)
        for i, r in enumerate(sorted(rows, key=lambda r: -r["pointsRound"])):
            r["roundPosition"] = i + 1
    rows.sort(key=lambda r: -r["pointsRound"])
    return {"league": meta, "round": rnd, "live": is_live(meta, rnd), "rows": rows}
