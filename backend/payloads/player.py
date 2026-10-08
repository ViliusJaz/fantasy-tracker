"""/api/league/<id>/player/<player id>: the player card, and the Proballers redirect."""
from backend import pipeline
from backend.advanced import advanced_profile, league_averages
from backend.errors import NotFound
from backend.i18n import L
from backend.injuries import _day, build_injury_history, injury_report, injury_view, loc_comment, loc_reason
from backend.league import league_meta, lineups, standings
from backend.players import players
from backend.previews import club_defense_ranks
from backend.proballers import proballers_target
from backend.sources import basketnews as bn
from backend.sources.injury_report import dnp_reason


def proballers_redirect(fid, player_id):
    meta = league_meta(fid)
    info = players(meta, meta["latestRound"], meta["currentRound"]).get(player_id)
    if not info:
        found = bn.fetch_player_rounds(meta, player_id, [])
        if not found:
            raise NotFound(L("Žaidėjas nerastas", "Player not found"))
        info = found[0]
    return proballers_target(info)


def player_payload(fid, player_id):
    meta = league_meta(fid)
    last = meta["latestRound"]
    rounds = list(range(meta["firstRound"], last + 1))
    found = bn.fetch_player_rounds(meta, player_id, rounds)
    if not found:
        raise NotFound(L("Žaidėjas nerastas", "Player not found"))
    info, per_round = found
    # Usage %: BasketNews' own (or a box-score estimate), which the player lists carry
    usage = {r: ((players(meta, r, r, shared=True).get(player_id) or {}).get("roundLine") or {}).get("usg")
             for r in rounds}
    season_view = players(meta, last, meta["currentRound"], shared=True).get(player_id) or {}
    if info.get("season") and (season_view.get("season") or {}).get("usg") is not None:
        info["season"] = {**info["season"], "usg": season_view["season"]["usg"]}

    game_log = []
    for rr in per_round:
        games = rr["games"]
        row = {"round": rr["round"], "games": games, "date": _day(games[0]["at"]) if games else None,
               "fp": rr["fp"], "club": rr["club"]}
        if rr["line"]:
            line = rr["line"] if usage.get(rr["round"]) is None else {**rr["line"], "usg": usage[rr["round"]]}
            row.update(status="played", line=line)
        elif not games:
            row["status"] = "no-game"
        elif all(g["completed"] or g["canceled"] for g in games):
            row["status"] = "dnp"
        else:
            row["status"] = "pending"
        game_log.append(row)

    report = injury_report(meta)
    pipeline.store()  # the injury story includes changes seen just now
    current = report.get(info["bnId"])
    history = build_injury_history(info["bnId"], game_log) if info["bnId"] else {"episodes": [], "missed": [], "summary": ""}
    # Explain each missed round with the report's own words where possible.
    reasons = {}
    for ep in history["episodes"]:
        for u in ep["updates"]:
            rnd_no, why = dnp_reason(u["comment"])
            if rnd_no:
                reasons[rnd_no - 1] = loc_reason(why)
        for r in ep["missedRounds"]:
            reasons.setdefault(r, ep["reason"])
    if current:  # recovered players keep a "DNP in Round N (...)" note in the report
        rnd_no, why = dnp_reason(current["comment"])
        if rnd_no:
            reasons.setdefault(rnd_no - 1, loc_reason(why) or loc_comment(current["comment"]))
    for row in game_log:
        if row["status"] == "dnp":
            row["reason"] = reasons.get(row["round"])
    shooting = season_shooting([row["line"] for row in game_log if row["status"] == "played"])

    owner = None
    for team_id, lu in lineups(meta).items():
        if any(p["id"] == player_id for p in lu["players"]):
            owner = next((r["team"] for r in standings(meta)[1] if r["team"]["id"] == team_id), {"id": team_id})
            break

    # the opponent's defensive rank on every game, for the FP split against strong / weak defenses
    ranks, ranked = club_defense_ranks(meta)
    for g in [g for row in game_log for g in row["games"]] + info["games"]:
        g["oppDefRank"] = ranks.get(g["opponent"])

    return {
        "league": {"id": meta["id"], "title": meta["title"], "currentRound": meta["currentRound"]},
        "player": info,
        "owner": owner,
        "advanced": advanced_profile(meta, info["bnId"]),
        "leagueAvg": league_averages(meta),
        "proballers": f"/go/proballers/{fid}/{player_id}",
        "injury": {
            "current": injury_view(current, info["health"]),
            "reportUrl": meta["injuryReportUrl"],
            **history,
        },
        "gameLog": list(reversed(game_log)),
        "nextGames": info["games"],
        "shooting": shooting,
        "defense": {"teams": ranked, "top": ranked // 2},
    }


def season_shooting(lines):
    """Made / attempted / % for the season, summed from the round box scores."""
    def split(made, att):
        m, a = sum(x[made] for x in lines), sum(x[att] for x in lines)
        return {"made": m, "att": a, "pct": round(100 * m / a, 1) if a else None}
    two, three, ft = split("p2m", "p2a"), split("p3m", "p3a"), split("ftm", "fta")
    fg = {"made": two["made"] + three["made"], "att": two["att"] + three["att"]}
    fg["pct"] = round(100 * fg["made"] / fg["att"], 1) if fg["att"] else None
    return {"fg": fg, "two": two, "three": three, "ft": ft, "games": len(lines)}
