"""Live fantasy points. While a round is on, BasketNews has no stats for it yet (they come after
the games), so players whose game has started get their line and fantasy points from the
EuroLeague live feed (backend/sources/euroleague_live.py), worked out with BasketNews' own
formula (backend/scoring.fantasy_points). Such players are marked "roundLive", and BasketNews'
official numbers replace them as soon as they exist. The lineups' totals, the matchup scores
and the round table of a live round are counted from the same numbers.

Everything here is read when a build runs (every 15 minutes); nothing live is ever archived:
the history only keeps finished rounds, scored by BasketNews.
"""
import threading
import time
from datetime import datetime

from backend import clock, log
from backend.league import lineups
from backend.rounds import is_live
from backend.scoring import fantasy_points, score_lineup
from backend.sources import euroleague, euroleague_live
from backend.util import ascii_slug, pool_map

LOG = log.get("fetch")
TTL = 120
_memo = {}
_lock = threading.Lock()


def _local(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def _records(raw):
    return ((raw or {}).get("playersSearchRecordsFromClient") or {}).get("records") or []


def fixtures(raw):
    """The round's games from the raw player list: ({(home, away, at): game}, {abbr: club names},
    {abbr: BasketNews has stats for some player of the club})."""
    games, clubs, scored = {}, {}, {}
    for rec in _records(raw):
        team = ((rec.get("team") or {}).get("team")) or {}
        abbr = team.get("abbreviation")
        if not abbr:
            continue
        clubs[abbr] = {"nameEn": (team.get("en") or {}).get("name"), "fullName": (team.get("translation") or {}).get("name")}
        scored[abbr] = scored.get(abbr, False) or bool((rec.get("roundStats") or {}).get("s_gp"))
        for g in team.get("games") or []:
            home, away = g["team1"]["team"]["abbreviation"], g["team2"]["team"]["abbreviation"]
            games.setdefault((home, away, g["originalGameAt"]), g)
    return games, clubs, scored


def _whole(x):
    return int(x) if float(x).is_integer() else x


def boxes(meta, raw):
    """{(home, away, at): live game (euroleague_live.parse) + "codes": {abbr: EuroLeague code}} for
    the games of the live round that have started. The same object for a couple of minutes."""
    from backend.predictions import club_codes  # predictions imports the model; keep this module light
    key = (meta["leagueId"], meta["currentRound"])
    with _lock:
        hit = _memo.get(key)
        if hit and hit[0] > time.time() and hit[1] is raw:
            return hit[2]
    games, clubs, scored = fixtures(raw)
    now = clock.now()
    # games that have started, except finished ones BasketNews has already scored (its numbers win)
    started = [k for k, g in games.items()
               if not g.get("canceled") and (g.get("live") or g.get("completed") or _local(k[2]) <= now)
               and not (g.get("completed") and scored.get(k[0]) and scored.get(k[1]))]
    out = {}
    if started:
        season = f"E{meta.get('seasonYear')}"
        try:
            schedule = euroleague.season_games(season)
        except Exception as exc:  # noqa: BLE001 - live points are extra: never fail a build over them
            LOG.warning("EuroLeague schedule unavailable, no live box scores: %s", exc)
            schedule = []
        codes = club_codes(clubs, schedule)

        def one(k):
            home, away, at = k
            when = _local(at)
            match = next((g for g in schedule if g["home"] == codes.get(home) and g["away"] == codes.get(away)
                          and abs((_local(g["date"]) - when).total_seconds()) < 36 * 3600), None)
            if not match:
                return k, None
            state = euroleague_live.game(season, int(str(match["id"]).split("_")[-1]))
            return k, ({**state, "codes": {home: codes[home], away: codes[away]}} if state else None)
        out = {k: v for k, v in pool_map(one, started) if v}
    with _lock:
        _memo[key] = (time.time() + TTL, raw, out)
    return out


def _tokens(name):
    return set(ascii_slug((name or "").replace(",", " ")).split("-")) - {""}


def match_rows(rows, roster):
    """{id(view): live row} for one club. The live feed writes "MALEDON, THEO" with a shirt number;
    BasketNews "Theo Maledon" (its numbers and first names can be out of date). Pairs are made
    one to one, in passes: shirt number + a shared name part, the same name, then the surname."""
    pairs, free_rows, free_views = {}, list(rows), list(roster)

    def surname(row):
        return _tokens(row["name"].split(",")[0])

    tests = [
        lambda r, v: bool(r["number"]) and r["number"] == str(v.get("number") or "").strip()
        and bool(_tokens(r["name"]) & _tokens(v["name"])),
        lambda r, v: _tokens(r["name"]) == _tokens(v["name"]),
        lambda r, v: bool(surname(r)) and surname(r) <= _tokens(v["name"]),
    ]
    for test in tests:
        for r in list(free_rows):
            hits = [v for v in free_views if test(r, v)]
            if len(hits) == 1 and sum(1 for r2 in free_rows if test(r2, hits[0])) == 1:
                pairs[id(hits[0])] = r
                free_rows.remove(r)
                free_views.remove(hits[0])
    return pairs


LINE_SUM = ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "eff", "p2m", "p2a", "p3m", "p3a",
            "ftm", "fta", "sec", "ba", "fd", "pm")


def overlay(meta, views, live_games):
    """Live scores, quarter and clock on every game of the round; live lines and fantasy points
    for players BasketNews has not scored yet. Changes `views` in place."""
    if not live_games:
        return
    rosters = {}
    for v in views.values():
        rosters.setdefault((v.get("club") or {}).get("abbr"), []).append(v)
    matched = {}  # (game, club) -> {id(view): live row}
    for key, state in live_games.items():
        for club in key[:2]:
            matched[(key, club)] = match_rows(state["players"].get(state["codes"][club]) or [], rosters.get(club, []))
    for v in views.values():
        club = (v.get("club") or {}).get("abbr")
        if not club:
            continue
        lines, points = [], []
        for g in v.get("games") or []:
            home, away = (club, g["opponent"]) if g["home"] else (g["opponent"], club)
            state = live_games.get((home, away, g["at"]))
            if not state:
                continue
            me, opp = state["score"].get(state["codes"][club], 0), state["score"].get(state["codes"][g["opponent"]], 0)
            g["score"] = [me, opp]
            g["live"] = state["live"]
            g["completed"] = g["completed"] or state["final"]
            if state["live"]:
                g["period"] = {"quarter": state["quarter"], "clock": state["clock"]}
            row = matched.get(((home, away, g["at"]), club), {}).get(id(v))
            if row:
                won = (me > opp) if (state["final"] or me != opp) else None
                lines.append(row["line"])
                points.append(fantasy_points(row["line"], won, meta.get("pointCalcSystem")))
        if v.get("roundLine") or not lines or any(p is None for p in points):
            continue  # BasketNews' own numbers win; unknown scoring systems get no live points
        line = {k: sum(x.get(k) or 0 for x in lines) for k in LINE_SUM}
        line["min"] = round(line["sec"] / 60, 1)
        v.update(roundLine=line, roundPts=round(sum(points), 2), roundPlayed=True, roundLive=True)


def team_totals(meta, rnd):
    """{teamId: live total} for a live round, from each team's lineup for it; {} otherwise."""
    if not is_live(meta, rnd):
        return {}
    from backend.players import players_by_ids  # players imports this module
    current = {tid: lu for tid, lu in lineups(meta).items() if lu["round"] == rnd}
    if not current:
        return {}
    pmap = players_by_ids(meta, [p["id"] for lu in current.values() for p in lu["players"]], rnd, rnd)
    out = {}
    for tid, lu in current.items():
        plist = [{**pmap[p["id"]], "card": p["card"], "slot": p["slot"], "captain": p["captain"]}
                 for p in lu["players"] if p["id"] in pmap]
        out[tid] = _whole(score_lineup(plist)["total"])
    return out


def live_matchups(meta, rnd, matchups):
    """Head-to-head scores of a live round counted from the lineups."""
    totals = team_totals(meta, rnd)
    if not totals:
        return matchups
    out = []
    for m in matchups:
        a, b = totals.get((m["team1"] or {}).get("id")), totals.get((m["team2"] or {}).get("id"))
        out.append({**m, "score1": m["score1"] if a is None else a, "score2": m["score2"] if b is None else b})
    return out


def live_round_rows(meta, rnd):
    """A points league's table for live round `rnd`: the previous table plus the live points.
    BasketNews' rows for the live round are used as the base when they already carry totals."""
    from backend.league import fetch_standings_round, not_scored
    before = fetch_standings_round(meta, rnd - 1) if rnd > meta["firstRound"] else []
    rows = fetch_standings_round(meta, rnd)
    if (not rows or not_scored(rows)) and before:
        rows = [{**r, "pointsRound": 0, "roundPosition": None} for r in before]
    return live_table(meta, rnd, rows, before)


def live_table(meta, rnd, rows, before=None):
    """A points league's table during a live round: this round's live points added to the total
    before it, re-ranked; positionGained (new place minus old) against `before`, the table after
    the previous round. Rows without a live total keep their numbers."""
    totals = team_totals(meta, rnd)
    if not totals:
        return rows
    old = {r["team"]["id"]: r["position"] for r in before or []}
    out = []
    for r in rows:
        pts = totals.get(r["team"]["id"])
        if pts is None:
            out.append(dict(r))
            continue
        out.append({**r, "pointsRound": pts,
                    "pointsTotal": _whole(round(r["pointsTotal"] - (r.get("pointsRound") or 0) + pts, 2))})
    out.sort(key=lambda r: -r["pointsTotal"])
    for i, r in enumerate(out):
        r["position"] = i + 1
        if r["team"]["id"] in old:
            r["positionGained"] = r["position"] - old[r["team"]["id"]]
    return out
