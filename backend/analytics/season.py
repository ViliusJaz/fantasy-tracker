"""Per-round breakdowns and the season metrics built on them: efficiency, schedule strength,
streaks."""
from backend import history
from backend.analytics import dataset, metrics
from backend.league import fetch_standings_round, schedule
from backend.players import players_by_ids
from backend.scoring import score_lineup
from backend.util import pool_map


def manager_efficiency(breakdowns, team_names):
    """Real points / best possible points per team over the rounds with saved lineups."""
    eff = metrics.efficiency(dataset.from_breakdowns(None, breakdowns, team_names))
    rows = [{"team": {"id": tid, "title": team_names.get(tid, "?")}, **row} for tid, row in eff.items()]
    rows.sort(key=lambda r: -(r["efficiency"] or 0))
    return rows


def strength_of_schedule(meta, breakdowns, team_names):
    """H2H: how strong each team's opponents have been and will be (their average points per round)."""
    if meta["format"] != "head_to_head" or not breakdowns:
        return []
    start = meta["currentRound"] + (1 if meta["roundStarted"] else 0)
    future = {r: [{"team1": (m["team1"] or {}).get("id"), "team2": (m["team2"] or {}).get("id")} for m in games]
              for r, games in pool_map(lambda r: (r, schedule(meta, r)), list(range(start, meta["totalRounds"])))}
    strength = metrics.schedule_strength(dataset.from_breakdowns(meta, breakdowns, team_names, future))
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    rows = [{"team": {"id": tid, "title": team_names.get(tid, "?")}, "faced": mean(st["faced"]),
             "against": mean(st["against"]), "next5": mean(st["next5"]), "rest": mean(st["rest"])}
            for tid, st in strength.items()]
    for key in ("faced", "next5", "rest"):  # 1 = hardest
        ordered = sorted((r for r in rows if r[key] is not None), key=lambda r: -r[key])
        for i, r in enumerate(ordered):
            r[key + "Rank"] = i + 1
    rows.sort(key=lambda r: r.get("facedRank", 99))
    return rows


def round_breakdown(meta, rnd):
    """Scores, matchups and (when a lineup snapshot exists) scored lineups of one finished round."""
    table = fetch_standings_round(meta, rnd)
    teams = {r["team"]["id"]: r["team"] for r in table}
    scores = {r["team"]["id"]: r["pointsRound"] for r in table}
    games = []
    if meta["format"] == "head_to_head":
        for m in schedule(meta, rnd):
            if not (m["team1"] and m["team2"]):
                continue
            a, b = (m["team1"], m["score1"]), (m["team2"], m["score2"])
            (win, ws), (lose, ls) = (a, b) if a[1] >= b[1] else (b, a)
            games.append({"winner": win, "loser": lose, "ws": ws, "ls": ls, "margin": round(ws - ls, 2),
                          "tie": ws == ls})
    lineups_by_team = {}
    snap = history.lineup_rounds(meta["id"]).get(str(rnd))
    if snap:
        ids = [p["id"] for lu in snap["teams"].values() for p in lu["players"]]
        pmap = players_by_ids(meta, ids, rnd, rnd)
        for tid, lu in snap["teams"].items():
            plist = [{**pmap[p["id"]], "card": p["card"], "slot": p["slot"], "captain": p["captain"]}
                     for p in lu["players"] if p["id"] in pmap]
            lineups_by_team[tid] = {"players": plist, **score_lineup(plist)}
    return {"round": rnd, "teams": teams, "scores": scores, "games": games, "lineups": lineups_by_team}
