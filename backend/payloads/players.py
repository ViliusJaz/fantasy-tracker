"""/api/league/<id>/free-agents and /players."""
from backend.advanced import advanced_table
from backend.injuries import injury_report, injury_view
from backend.league import league_meta, owners
from backend.players import double_doubles, players


def players_payload(fid, scope="free"):
    """Player list with season stats: only free agents, or everybody (scope="all") with their owner."""
    meta = league_meta(fid)
    own = owners(meta, meta["currentRound"])
    stats_round = meta["latestRound"]
    pmap = players(meta, stats_round, meta["currentRound"])
    report = injury_report(meta)
    advanced, adv = advanced_table(meta, pmap)
    doubles = double_doubles(meta)
    rows = []
    for p in pmap.values():
        owner = own.get(p["id"])
        if scope == "free" and owner:
            continue
        rows.append({**p, "injury": injury_view(report.get(p["bnId"]), p["health"]), "owner": owner,
                     "adv": adv.get(p["id"]), "dd": doubles.get(p["id"], 0)})
    rows.sort(key=lambda p: (p["avgPts"] is None, -(p["avgPts"] or 0), p["name"]))
    return {
        "league": meta,
        "scope": scope,
        "statsRound": stats_round,
        "players": rows,
        "totalPlayers": len(pmap),
        "rosteredPlayers": len(own),
        "injuryReportUrl": meta["injuryReportUrl"],
        "advanced": advanced,
    }
