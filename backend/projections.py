"""Projected fantasy points for the current round and head-to-head win probabilities."""
import math

from backend.injuries import injury_report, injury_view
from backend.league import lineups
from backend.players import players, players_by_ids
from backend.scoring import score_lineup
from backend.util import pool_map


# Share of a normal game a player is expected to play, by injury-report status.
AVAILABILITY = {"out": 0.0, "doubtful": 0.25, "questionable": 0.6, "uncertain": 0.6, "game-time": 0.85,
                "expected": 0.95}


def recent_points(meta, n=5):
    """{playerId: [fantasy points in each of the last n finished rounds the player played]}"""
    rounds = list(range(max(meta["firstRound"], meta["currentRound"] - n), meta["currentRound"]))
    out = {}
    for _, pm in pool_map(lambda r: (r, players(meta, r, r)), rounds):
        for v in pm.values():
            if v["roundPlayed"] and v.get("roundPts") is not None:
                out.setdefault(v["id"], []).append(v["roundPts"])
    return out


def player_projection(v, recent, report):
    """Expected fantasy points in the player's games of the round (before lineup multipliers)
    and the variance of that guess. Season average blended with the last five rounds, times
    the number of games, times the chance to play from the injury report."""
    rec = recent.get(v["id"]) or []
    avg = v.get("avgPts")
    if avg is None:
        base = sum(rec) / len(rec) if rec else 0.0
    elif len(rec) >= 2:
        base = 0.6 * avg + 0.4 * sum(rec) / len(rec)
    else:
        base = avg
    games = [g for g in v.get("games") or [] if not g.get("canceled")]
    inj = injury_view(report.get(v.get("bnId")), v.get("health"))
    avail = AVAILABILITY.get((inj or {}).get("status"), 1.0)
    if len(rec) >= 3:
        mean_r = sum(rec) / len(rec)
        sd = max(4.0, math.sqrt(sum((x - mean_r) ** 2 for x in rec) / (len(rec) - 1)))
    else:
        sd = max(4.0, 0.45 * abs(base))
    # still to come: 1 per game not started, half of a game in progress
    todo = sum(0 if g.get("completed") else 0.5 if g.get("live") else 1 for g in games)
    done = (v.get("roundPts") or 0) if len(games) > todo else 0
    mean = done + base * todo * avail
    var = sd * sd * todo * max(avail, 0.25)
    return round(mean, 2), var


def lineup_projection(plist, recent, report):
    """Projected total of a scored lineup (players carry mult) and its variance; adds proj / projSd."""
    mean = var = 0.0
    for p in plist:
        pm, pv = player_projection(p, recent, report)
        p["proj"], p["projSd"] = round(pm, 1), round(math.sqrt(pv), 1)
        mean += p["mult"] * pm
        var += p["mult"] ** 2 * pv
    return {"mean": round(mean, 1), "sd": round(math.sqrt(var), 1)}


def win_probability(a, b):
    """P(team a outscores team b) with normally distributed totals."""
    sd = math.sqrt(a["sd"] ** 2 + b["sd"] ** 2) or 1.0
    return round(0.5 * (1 + math.erf((a["mean"] - b["mean"]) / (sd * math.sqrt(2)))), 3)


def team_projections(meta, rnd):
    """{teamId: projection} for the current round from today's lineups (only that round)."""
    if rnd != meta["currentRound"]:
        return {}
    current = lineups(meta)
    ids = [p["id"] for lu in current.values() for p in lu["players"]]
    pmap = players_by_ids(meta, ids, rnd, rnd)
    recent, report = recent_points(meta), injury_report(meta)
    out = {}
    for tid, lu in current.items():
        if lu["round"] != rnd:
            continue
        plist = [{**pmap[p["id"]], "card": p["card"], "slot": p["slot"], "captain": p["captain"]}
                 for p in lu["players"] if p["id"] in pmap]
        score_lineup(plist)
        out[tid] = lineup_projection(plist, recent, report)
    return out


def matchup_projections(meta, rnd, matchups):
    """Adds projected finals and win probabilities to a round's H2H matchups (current round only)."""
    proj = team_projections(meta, rnd)
    for m in matchups:
        a, b = proj.get((m["team1"] or {}).get("id")), proj.get((m["team2"] or {}).get("id"))
        if a and b:
            m["projection"] = {"team1": a, "team2": b, "win1": win_probability(a, b)}
    return matchups
