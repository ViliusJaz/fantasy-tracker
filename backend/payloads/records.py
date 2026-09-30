"""/api/league/<id>/records: season records, awards and analytics."""
from backend.analytics import metrics
from backend.analytics.awards import draft_awards, round_awards, season_oscars, season_records
from backend.analytics.season import (
    manager_efficiency, round_breakdown, strength_of_schedule,
)
from backend.league import league_meta, standings
from backend.util import pool_map


def records_payload(fid, rnd=None):
    meta = league_meta(fid)
    finished = list(range(meta["firstRound"], meta["currentRound"]))
    _, table = standings(meta)
    team_names = {r["team"]["id"]: r["team"]["title"] for r in table}
    base = {"league": meta, "finished": finished, "round": None, "roundAwards": [], "oscars": [],
            "records": [], "form": [], "missingLineups": []}
    if not finished:
        return base
    rnd = finished[-1] if rnd not in finished else rnd
    breakdowns = pool_map(lambda r: round_breakdown(meta, r), finished)
    h2h = meta["format"] == "head_to_head"

    results, totals, per_round = {}, {}, {}
    for bd in breakdowns:
        for tid, pts in bd["scores"].items():
            totals[tid] = totals.get(tid, 0) + pts
            per_round.setdefault(tid, []).append({"round": bd["round"], "points": pts})
        for g in bd["games"]:
            for team, res, opp, mine, theirs in (
                    (g["winner"], "T" if g["tie"] else "W", g["loser"], g["ws"], g["ls"]),
                    (g["loser"], "T" if g["tie"] else "L", g["winner"], g["ls"], g["ws"])):
                results.setdefault(team["id"], []).append(
                    {"round": bd["round"], "result": res, "opponent": opp["title"], "points": mine, "against": theirs})
    streaks = {t: metrics.streaks([x["result"] for x in rs]) for t, rs in results.items()}

    form = []
    for row in table:
        tid = row["team"]["id"]
        rounds = per_round.get(tid, [])
        pts = [x["points"] for x in rounds]
        entry = {"team": row["team"], "position": row["position"],
                 "avg": round(sum(pts) / len(pts), 2) if pts else None,
                 "best": max(pts) if pts else None, "worst": min(pts) if pts else None}
        if h2h:
            longest_w, longest_l, (kind, run) = streaks.get(tid, (0, 0, (None, 0)))
            entry.update(last=results.get(tid, [])[-5:], streak={"kind": kind, "length": run},
                         longestWin=longest_w, longestLoss=longest_l)
        else:
            entry["last"] = rounds[-5:]
        form.append(entry)

    selected = next(bd for bd in breakdowns if bd["round"] == rnd)
    return {
        **base,
        "round": rnd,
        "roundAwards": round_awards(meta, selected),
        "oscars": season_oscars(meta, breakdowns, team_names),
        "draftAwards": draft_awards(meta, finished, team_names),
        "efficiency": manager_efficiency(breakdowns, team_names),
        "schedule": strength_of_schedule(meta, breakdowns, team_names),
        "records": season_records(meta, breakdowns, team_names, streaks, totals),
        "form": form,
        "missingLineups": [bd["round"] for bd in breakdowns if not bd["lineups"]],
        "partialLineups": [{"round": bd["round"],
                            "teams": sorted(team_names.get(t, t) for t in bd["scores"] if t not in bd["lineups"])}
                           for bd in breakdowns if bd["lineups"] and set(bd["scores"]) - set(bd["lineups"])],
    }
