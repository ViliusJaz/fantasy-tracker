"""/api/league/<id>/standings and /rounds."""
from backend.league import fetch_standings_round, league_meta, schedule, standings
from backend.live import live_matchups, live_round_rows
from backend.projections import matchup_projections
from backend.rounds import is_live


def standings_payload(fid, rnd=None):
    meta = league_meta(fid)
    cur = meta["currentRound"]
    if meta["format"] != "head_to_head" and is_live(meta, cur) and rnd in (None, cur):
        # points leagues: the live round counts right away (head-to-head waits for the results)
        shown, rows, live = cur, live_round_rows(meta, cur), True
    else:
        shown, rows = standings(meta, rnd)
        live = is_live(meta, shown)
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
        rows = live_round_rows(meta, rnd)
        for i, r in enumerate(sorted(rows, key=lambda r: -r["pointsRound"])):
            r["roundPosition"] = i + 1
    rows.sort(key=lambda r: -r["pointsRound"])
    return {"league": meta, "round": rnd, "live": is_live(meta, rnd), "rows": rows}
