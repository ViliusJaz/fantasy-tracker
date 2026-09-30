"""Per-round breakdowns and the season metrics built on them: efficiency, schedule strength,
streaks, the round recap."""
from backend import history
from backend.analytics import dataset, metrics
from backend.i18n import L
from backend.league import fetch_standings_round, moved_players, raw_transfers, schedule
from backend.players import players, players_by_ids
from backend.scoring import score_lineup
from backend.util import num, pool_map


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


def positions_before(meta, rnd):
    """{teamId: standings position} after the round before `rnd` (empty for the first round)."""
    if rnd <= meta["firstRound"]:
        return {}
    return {r["team"]["id"]: r["position"] for r in fetch_standings_round(meta, rnd - 1)}


def round_recap(meta, bd, team_names, positions_before):
    """Automatic round summary: the lines a league chat would want."""
    lines = []
    name = lambda tid: team_names.get(tid, "?")  # noqa: E731
    rnd, scores = bd["round"], bd["scores"]
    if scores:
        hi, lo = max(scores, key=scores.get), min(scores, key=scores.get)
        lines.append({"icon": "🔥", "text": L(f"Turo lyderis: {name(hi)} surinko {num(scores[hi])} tšk.",
                                              f"Top score: {name(hi)} with {num(scores[hi])} points.")})
    decided = [g for g in bd["games"] if not g["tie"]]
    upsets = [(positions_before.get(g["winner"]["id"], 0) - positions_before.get(g["loser"]["id"], 0), g)
              for g in decided if positions_before]
    upsets = [u for u in upsets if u[0] > 0]
    if upsets:
        gap, g = max(upsets, key=lambda u: u[0])
        lines.append({"icon": "😱", "text": L(
            f"Staigmena: {g['winner']['title']} ({positions_before[g['winner']['id']]} vieta) įveikė "
            f"{g['loser']['title']} ({positions_before[g['loser']['id']]} vieta) {num(g['ws'])}:{num(g['ls'])}.",
            f"Upset: {g['winner']['title']} (#{positions_before[g['winner']['id']]}) beat "
            f"{g['loser']['title']} (#{positions_before[g['loser']['id']]}) {num(g['ws'])}:{num(g['ls'])}.")})
    if decided:
        close = min(decided, key=lambda g: g["margin"])
        lines.append({"icon": "⚔️", "text": L(
            f"Arčiausia kova: {close['winner']['title']} {num(close['ws'])}:{num(close['ls'])} {close['loser']['title']}.",
            f"Closest game: {close['winner']['title']} {num(close['ws'])}:{num(close['ls'])} {close['loser']['title']}.")})
    lus = bd["lineups"]
    if lus:
        active = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["slot"] != "inactive"]
        if active:
            tid, p = max(active, key=lambda x: x[1].get("roundPts") or -999)
            lines.append({"icon": "⭐", "text": L(f"MVP: {p['name']} ({name(tid)}) {num(p.get('roundPts'))} FP.",
                                                  f"MVP: {p['name']} ({name(tid)}) {num(p.get('roundPts'))} FP.")})
        caps = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["captain"]]
        if caps:
            tid, p = min(caps, key=lambda x: x[1]["contrib"])
            lines.append({"icon": "🙈", "text": L(
                f"Kapitono nesėkmė: {name(tid)} kapitonas {p['name']} atnešė tik {num(p['contrib'])} tšk.",
                f"Captain flop: {name(tid)}'s captain {p['name']} brought only {num(p['contrib'])} points.")})
        tid = max(lus, key=lambda t: lus[t]["lost"])
        if lus[tid]["lost"] > 0:
            lines.append({"icon": "🪑", "text": L(
                f"Suolo katastrofa: {name(tid)} dėl sudėties prarado {num(lus[tid]['lost'])} tšk. "
                f"(optimali sudėtis būtų surinkusi {num(lus[tid]['optimal'])}).",
                f"Bench disaster: {name(tid)} lost {num(lus[tid]['lost'])} points to lineup choices "
                f"(best lineup: {num(lus[tid]['optimal'])}).")})
    # waiver steal: best first round from a player picked up right before this round
    signed = []
    for r, ts in raw_transfers(meta):
        if r != rnd:
            continue
        for t in ts:
            tid = (t["offer"]["team"] or {}).get("id")
            if t["kind"] != "trade" and tid:
                signed += [(tid, pid) for pid in moved_players(t["request"])]
    if signed:
        pts_now = {v["id"]: (v.get("roundPts"), v["name"]) for v in players(meta, rnd, rnd).values()}
        scored = [(pts_now.get(pid, (None, "?")), tid) for tid, pid in signed if pts_now.get(pid, (None,))[0] is not None]
        if scored:
            (fp, pname), tid = max(scored, key=lambda x: x[0][0])
            lines.append({"icon": "💎", "text": L(f"Perėjimų radinys: {pname} ({name(tid)}) debiutavo su {num(fp)} FP.",
                                                  f"Waiver steal: {pname} ({name(tid)}) debuted with {num(fp)} FP.")})
    if scores and len(scores) > 1:
        lines.append({"icon": "🧊", "text": L(f"Sunkiausias turas: {name(lo)} tik {num(scores[lo])} tšk.",
                                              f"Rough round: {name(lo)} with only {num(scores[lo])} points.")})
    return lines


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
