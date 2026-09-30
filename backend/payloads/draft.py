"""/api/league/<id>/draft: every pick of the league's draft."""
from backend.league import league_meta, owners, standings
from backend.players import player_brief, players_by_ids
from backend.sources import basketnews as bn


def draft_payload(fid):
    """Every pick of the league's draft in order, with where the player is now."""
    meta = league_meta(fid)
    picks = bn.fetch_draft_picks(fid)
    teams = {r["team"]["id"]: r["team"] for r in standings(meta)[1]}
    per_round = len(teams) or 1
    ids = [p["playerId"] for p in picks if p["playerId"]]
    pmap = players_by_ids(meta, ids, meta["latestRound"], meta["currentRound"])
    own = owners(meta, meta["currentRound"])
    rows = []
    for i, pick in enumerate(picks):
        pid = pick["playerId"]
        rows.append({
            "overall": i + 1, "round": i // per_round + 1, "pick": i % per_round + 1,
            "team": teams.get(pick["teamId"]) or {"id": pick["teamId"], "title": "?"},
            "player": player_brief(pmap.get(pid), pid),
            "owner": (own.get(pid) or {}).get("team"),
        })
    return {"league": meta, "picks": rows, "teams": list(teams.values())}
