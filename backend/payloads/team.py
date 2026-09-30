"""/api/league/<id>/team/<team id>: a team's round, lineup and season history."""
from backend.errors import NotFound
from backend.history import lineup_snapshot
from backend.i18n import L
from backend.injuries import injury_report, injury_view
from backend.league import fetch_standings_round, league_meta, lineups, schedule, standings
from backend.players import players_by_ids
from backend.rounds import round_state
from backend.scoring import _slot_sort_key, players_left, score_lineup
from backend.util import pool_map


def team_round_result(meta, team_id, rnd):
    state = round_state(meta, rnd)
    if meta["format"] == "head_to_head":
        m = next((m for m in schedule(meta, rnd)
                  if team_id in ((m["team1"] or {}).get("id"), (m["team2"] or {}).get("id"))), None)
        if not m:
            return {"state": state}
        mine_first = (m["team1"] or {}).get("id") == team_id
        me, opp = (m["score1"], m["score2"]) if mine_first else (m["score2"], m["score1"])
        result = None
        if state == "finished":
            result = "W" if me > opp else "L" if me < opp else "T"
        return {"state": state, "points": me, "opponent": m["team2"] if mine_first else m["team1"],
                "opponentPoints": opp, "result": result}
    if state == "upcoming":
        return {"state": state}
    row = next((r for r in fetch_standings_round(meta, rnd) if r["team"]["id"] == team_id), None)
    if not row:
        return {"state": state}
    return {"state": state, "points": row["pointsRound"], "roundPosition": row.get("roundPosition"),
            "position": row["position"]}


def team_history(meta, team_id, last_round):
    """Per-round results of one team, oldest first: score, league average, and the
    table position / record after the round."""
    if last_round is None:
        return []
    rounds = list(range(meta["firstRound"], last_round + 1))
    if meta["format"] == "head_to_head" and meta["currentRound"] not in rounds:
        rounds.append(meta["currentRound"])

    def one(r):
        res = team_round_result(meta, team_id, r)
        if res["state"] != "upcoming":
            table = fetch_standings_round(meta, r)
            row = next((x for x in table if x["team"]["id"] == team_id), None)
            if row:
                res.update(position=row["position"], pointsTotal=row["pointsTotal"],
                           wins=row.get("wins"), losses=row.get("losses"))
            if table:
                res["leagueAvg"] = round(sum(x["pointsRound"] for x in table) / len(table), 2)
        return r, res

    return [{"round": r, **res} for r, res in pool_map(one, rounds) if len(res) > 1 or res["state"] == "upcoming"]


def team_payload(fid, team_id, rnd=None):
    meta = league_meta(fid)
    current = meta["currentRound"]
    rnd = current if rnd is None else max(meta["firstRound"], min(rnd, current))
    shown, rows = standings(meta)
    row = next((r for r in rows if r["team"]["id"] == team_id), None)
    if not row:
        raise NotFound(L("Komanda šioje lygoje nerasta", "Team not found in this league"))

    current_lineups = lineups(meta)
    now_lineup = current_lineups.get(team_id)
    lineup_note = None
    if now_lineup and now_lineup["round"] == rnd:
        lineup, source = now_lineup, "current"
    else:
        snap = lineup_snapshot(meta, rnd, team_id)
        if snap:
            lineup, source = snap, "snapshot"
            if snap.get("source") == "import":
                lineup_note = None
            elif not snap["locked"]:
                lineup_note = L("Sudėtis išsaugota prieš turui prasidedant, todėl vėlesni pakeitimai galėjo būti nematyti.",
                                "Lineup saved before the round started, so later changes may be missing.")
        else:
            # The API only exposes the current lineup; for earlier rounds show today's roster.
            lineup, source = now_lineup, "roster"
            lineup_note = L("Šio turo sudėtis nebuvo išsaugota (programa dar neveikė), todėl rodomas dabartinis "
                            "komandos sąrašas su to turo taškais. Tikslus to turo penketas ir kapitonas nežinomi.",
                            "This round's lineup was not saved (the app was not running), so the current roster is "
                            "shown with that round's points. The exact starting five and captain are unknown.")

    lineup_players = []
    formation = None
    if lineup:
        formation = lineup.get("formation")
        ids = [p["id"] for p in lineup["players"]]
        pmap = players_by_ids(meta, ids, rnd, rnd)
        report = injury_report(meta)
        for p in lineup["players"]:
            info = pmap.get(p["id"])
            if not info:
                continue
            entry = {**info, "card": p["card"], "slot": p["slot"], "captain": p["captain"],
                     "injury": injury_view(report.get(info["bnId"]), info["health"])}
            if source == "roster":
                entry.update(slot="roster", captain=False)
            lineup_players.append(entry)
        if source == "roster":
            lineup_players.sort(key=lambda x: -(x["roundPts"] or -1))
        else:
            lineup_players.sort(key=_slot_sort_key)

    result = team_round_result(meta, team_id, rnd)
    after = None
    if result["state"] != "upcoming":
        after = next((r for r in fetch_standings_round(meta, rnd) if r["team"]["id"] == team_id), None)
    if result["state"] == "live":
        result["left"] = players_left(lineup_players)

    scoring = None
    if lineup_players and source != "roster":
        scoring = score_lineup(lineup_players)
        if result["state"] == "upcoming":
            scoring = None  # slot labels and multipliers still apply, points don't exist yet

    return {
        "league": meta,
        "team": row["team"],
        "standing": row,
        "standingRound": shown,
        "round": rnd,
        "roundState": result["state"],
        "result": result,
        "after": after,
        "lineup": {"source": source if lineup else None, "note": lineup_note, "formation": formation,
                   "players": lineup_players, "scoring": scoring},
        "history": team_history(meta, team_id, shown),
    }
