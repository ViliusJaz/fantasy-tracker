"""Season metrics: every formula once, as a pure function of a season dataset (dataset.py).

The records page (payloads/records.py), the transfers page and api/.../analytics.json all
take their numbers from here, so a metric cannot be worked out one way on one page and
another way on the next. Nothing here fetches, stores or translates anything; rounds
without the needed data are left out and reported, never guessed.

A dataset: {"league", "teams": {id: team}, "rounds": [round, ...] (finished rounds, oldest
first), "future": {round: [matchup]}, "transfers": [...], "draft": [...], "fp": {round:
{playerId: points}}}; a round: {"round", "scores": {teamId: points}, "matchups": [{"team1",
"team2", "score1", "score2"}], "lineups": {teamId: [slot]}} with a slot {"id", "card",
"captain", "fp", "positions"}.
"""
from backend import scoring


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _slot_players(slots):
    """Lineup slots in the shape scoring.py works with."""
    return [{"id": s["id"], "card": s["card"], "slot": scoring.slot_of(s["card"]), "captain": bool(s.get("captain")),
             "roundPts": s.get("fp"), "positions": s.get("positions") or []} for s in slots]


# ---------------------------------------------------------------- results

def results(ds):
    """Head-to-head results per team, oldest first: {teamId: [{round, result W/L/T, opponent, for, against}]}."""
    out = {}
    for rd in ds["rounds"]:
        for m in rd.get("matchups") or []:
            a, b = m.get("team1"), m.get("team2")
            if not a or not b:
                continue
            for me, opp, mine, theirs in ((a, b, m["score1"], m["score2"]), (b, a, m["score2"], m["score1"])):
                res = "W" if mine > theirs else "L" if mine < theirs else "T"
                out.setdefault(me, []).append({"round": rd["round"], "result": res, "opponent": opp,
                                               "for": mine, "against": theirs})
    return out


def streaks(results_list):
    """Longest win and loss streaks and the current streak from ['W', 'L', 'W', ...]:
    (longest W, longest L, (kind, length))."""
    best = {"W": 0, "L": 0}
    run_kind, run = None, 0
    for res in results_list:
        if res == run_kind:
            run += 1
        else:
            run_kind, run = res, 1
        if res in best:
            best[res] = max(best[res], run)
    return best["W"], best["L"], (run_kind, run)


def all_play(ds):
    """Record against every other team each round (as if everyone played everyone):
    {teamId: {"wins", "losses", "ties", "pct"}}."""
    out = {tid: {"wins": 0, "losses": 0, "ties": 0} for tid in ds["teams"]}
    for rd in ds["rounds"]:
        scores = rd["scores"]
        for tid, mine in scores.items():
            rec = out.setdefault(tid, {"wins": 0, "losses": 0, "ties": 0})
            for other, theirs in scores.items():
                if other != tid:
                    rec["wins" if mine > theirs else "losses" if mine < theirs else "ties"] += 1
    for rec in out.values():
        games = rec["wins"] + rec["losses"] + rec["ties"]
        rec["pct"] = round((rec["wins"] + rec["ties"] / 2) / games, 3) if games else None
    return out


def expected_wins(ds):
    """Wins a team 'deserved': each round, the share of the other teams it outscored (ties
    half), summed over the rounds it played a head-to-head game. {teamId: float}."""
    played = {tid: {x["round"] for x in rs} for tid, rs in results(ds).items()}
    out = {tid: 0.0 for tid in ds["teams"]}
    for rd in ds["rounds"]:
        scores = rd["scores"]
        others = len(scores) - 1
        if others < 1:
            continue
        for tid, mine in scores.items():
            if rd["round"] not in played.get(tid, set()):
                continue
            beaten = sum(1 for o, s in scores.items() if o != tid and mine > s)
            tied = sum(1 for o, s in scores.items() if o != tid and mine == s)
            out[tid] = out.get(tid, 0.0) + (beaten + tied / 2) / others
    return {tid: round(v, 2) for tid, v in out.items()}


def luck(ds):
    """Actual head-to-head wins (ties half) minus expected wins: > 0 lucky with the schedule,
    < 0 unlucky. {teamId: {"wins", "expected", "luck"}}."""
    exp = expected_wins(ds)
    out = {}
    for tid in ds["teams"]:
        rs = results(ds).get(tid, [])
        wins = sum(1 for x in rs if x["result"] == "W") + sum(0.5 for x in rs if x["result"] == "T")
        out[tid] = {"wins": wins, "expected": exp.get(tid, 0.0), "luck": round(wins - exp.get(tid, 0.0), 2)}
    return out


# ---------------------------------------------------------------- lineups

def lineup_scores(ds):
    """Per team and round with a saved lineup: real points, the best possible with the same
    players (scoring.optimal_score) and the captain's part. [(round, teamId, info)]."""
    out = []
    for rd in ds["rounds"]:
        for tid, slots in (rd.get("lineups") or {}).items():
            plist = _slot_players(slots)
            totals = scoring.score_lineup(plist)
            court = [p for p in plist if p["slot"] == "starter"]
            best_captain = max((p["roundPts"] or 0 for p in court), default=0)
            captain = next((p for p in plist if p["captain"]), None)
            captain_fp = (captain["roundPts"] or 0) if captain and captain["slot"] == "starter" else 0
            out.append((rd["round"], tid, {**totals, "players": plist, "captainFp": captain_fp,
                                           "bestCaptainFp": best_captain}))
    return out


def efficiency(ds):
    """Real points / best possible points over the rounds with saved lineups, and the points
    lost to lineup choices: {teamId: {"points", "optimal", "lost", "efficiency", "rounds"}}."""
    tot, opt, rounds = {}, {}, {}
    for _, tid, lu in lineup_scores(ds):
        tot[tid] = tot.get(tid, 0) + lu["total"]
        opt[tid] = opt.get(tid, 0) + lu["optimal"]
        rounds[tid] = rounds.get(tid, 0) + 1
    return {tid: {"points": round(tot[tid], 2), "optimal": round(opt[tid], 2),
                  "lost": round(max(opt[tid] - tot[tid], 0), 2),
                  "efficiency": round(100 * tot[tid] / opt[tid], 1) if opt[tid] else None, "rounds": rounds[tid]}
            for tid in tot}


def captain_loss(ds):
    """Points the captain choice cost: the best starter's points minus the captain's (the
    captain counts twice, so that is exactly the bonus missed). {teamId: {"lost", "rounds",
    "worst": {"round", "lost"}}}."""
    out = {}
    for rnd, tid, lu in lineup_scores(ds):
        lost = round(max(lu["bestCaptainFp"] - lu["captainFp"], 0), 2)
        rec = out.setdefault(tid, {"lost": 0.0, "rounds": 0, "worst": None})
        rec["lost"] = round(rec["lost"] + lost, 2)
        rec["rounds"] += 1
        if lost and (rec["worst"] is None or lost > rec["worst"]["lost"]):
            rec["worst"] = {"round": rnd, "lost": lost}
    return out


# ---------------------------------------------------------------- schedule

def schedule_strength(ds):
    """How strong each team's opponents were (their average points per round) and what they
    scored against it; ahead: the same for the future schedule. {teamId: {"faced", "against",
    "next5", "rest"}} (means over the lists, None when empty)."""
    pts = {}
    for rd in ds["rounds"]:
        for tid, sc in rd["scores"].items():
            pts.setdefault(tid, []).append(sc)
    avg = {tid: sum(v) / len(v) for tid, v in pts.items()}
    faced, against = {}, {}
    for tid, rs in results(ds).items():
        faced[tid] = [avg.get(x["opponent"], 0) for x in rs]
        against[tid] = [x["against"] for x in rs]
    ahead = {}
    for rnd in sorted(ds.get("future") or {}):
        for m in ds["future"][rnd]:
            if m.get("team1") and m.get("team2"):
                ahead.setdefault(m["team1"], []).append((rnd, avg.get(m["team2"], 0)))
                ahead.setdefault(m["team2"], []).append((rnd, avg.get(m["team1"], 0)))
    out = {}
    for tid in avg:
        nxt = [x for _, x in sorted(ahead.get(tid, []))]
        out[tid] = {"faced": faced.get(tid, []), "against": against.get(tid, []), "next5": nxt[:5], "rest": nxt}
    return out


def rivalries(ds):
    """Head-to-head history of every pair: {(teamA, teamB): {"games", "wins": {a: n, b: n}, "ties",
    "points": {a: x, b: y}}} with teamA < teamB."""
    out = {}
    for rd in ds["rounds"]:
        for m in rd.get("matchups") or []:
            a, b = m.get("team1"), m.get("team2")
            if not a or not b:
                continue
            (x, sx), (y, sy) = sorted(((a, m["score1"]), (b, m["score2"])))
            rec = out.setdefault((x, y), {"games": 0, "wins": {x: 0, y: 0}, "ties": 0, "points": {x: 0.0, y: 0.0}})
            rec["games"] += 1
            rec["points"][x] = round(rec["points"][x] + sx, 2)
            rec["points"][y] = round(rec["points"][y] + sy, 2)
            if sx == sy:
                rec["ties"] += 1
            else:
                rec["wins"][x if sx > sy else y] += 1
    return out


# ---------------------------------------------------------------- moves

def transfer_net(transfers, fp, rounds, rosters=None):
    """What each move gave each team involved, over `rounds` (the rounds from the move on):
    points of the players it received while still on its roster (a round without a saved
    roster counts them as kept), minus what the players it gave away scored anywhere.
    transfers: [{"id", "round", "kind", "offer": {"team": id, "players": [ids]}, "request": {...}}]
    fp: {round: {playerId: points}}; rosters: {round: {teamId: {playerIds}}}.
    Returns {transferId: {teamId: {"inFp", "outFp", "net", "rounds"}}}."""
    rosters = rosters or {}

    def kept(tid, pid, r):
        team = (rosters.get(r) or {}).get(tid)
        return True if team is None else pid in team

    out = {}
    for t in transfers:
        after = [r for r in rounds if r >= t["round"]]
        sides = [(t["offer"], t["request"])] + ([(t["request"], t["offer"])] if t["kind"] == "trade" else [])
        out[t["id"]] = {}
        for own, other in sides:
            tid = own.get("team")
            if not tid:
                continue
            got = sum((fp.get(r) or {}).get(p, 0) or 0 for r in after for p in other["players"] if kept(tid, p, r))
            gave = sum((fp.get(r) or {}).get(p, 0) or 0 for r in after for p in own["players"])
            out[t["id"]][tid] = {"inFp": round(got, 2), "outFp": round(gave, 2), "net": round(got - gave, 2),
                                 "rounds": len(after)}
    return out


def draft_value(ds):
    """How each pick turned out: the drafted players ranked by season points (finished rounds);
    value = pick number - that rank (> 0: better than where it was taken).
    {"picks": [{"pick", "team", "player", "points", "rank", "value"}], "teams": {teamId: {"value", "best", "worst"}}}."""
    totals = {}
    for r, pts in (ds.get("fp") or {}).items():
        for pid, v in pts.items():
            totals[pid] = totals.get(pid, 0) + (v or 0)
    picks = [p for p in ds.get("draft") or [] if p.get("player")]
    ranked = sorted(picks, key=lambda p: (-totals.get(p["player"], 0), p["pick"]))
    rank = {p["pick"]: i + 1 for i, p in enumerate(ranked)}
    rows = [{"pick": p["pick"], "team": p["team"], "player": p["player"], "points": round(totals.get(p["player"], 0), 2),
             "rank": rank[p["pick"]], "value": p["pick"] - rank[p["pick"]]} for p in picks]
    teams = {}
    for row in rows:
        rec = teams.setdefault(row["team"], {"value": 0, "best": None, "worst": None})
        rec["value"] += row["value"]
        if rec["best"] is None or row["value"] > rec["best"]["value"]:
            rec["best"] = row
        if rec["worst"] is None or row["value"] < rec["worst"]["value"]:
            rec["worst"] = row
    return {"picks": rows, "teams": teams}


# ---------------------------------------------------------------- records

def records(ds):
    """Season records as numbers: highest / lowest round score, biggest / closest win, most
    points in a loss, fewest in a win. Each {"team", "round", "points", ...} or None."""
    rows = [(sc, tid, rd["round"]) for rd in ds["rounds"] for tid, sc in rd["scores"].items()]
    games = [(x, tid) for tid, rs in results(ds).items() for x in rs if x["result"] == "W"]
    losses = [(x, tid) for tid, rs in results(ds).items() for x in rs if x["result"] == "L"]

    def score(row):
        return {"team": row[1], "round": row[2], "points": row[0]} if row else None

    def game(pair):
        if not pair:
            return None
        x, tid = pair
        return {"team": tid, "opponent": x["opponent"], "round": x["round"], "for": x["for"], "against": x["against"],
                "margin": round(x["for"] - x["against"], 2)}
    return {
        "highestScore": score(max(rows, default=None)),
        "lowestScore": score(min(rows, default=None)),
        "biggestWin": game(max(games, key=lambda g: g[0]["for"] - g[0]["against"], default=None)),
        "closestWin": game(min(games, key=lambda g: g[0]["for"] - g[0]["against"], default=None)),
        "mostPointsInLoss": game(max(losses, key=lambda g: g[0]["for"], default=None)),
        "fewestPointsInWin": game(min(games, key=lambda g: g[0]["for"], default=None)),
    }
