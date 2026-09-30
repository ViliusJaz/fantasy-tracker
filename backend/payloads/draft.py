"""/api/league/<id>/draft: every pick of the league's draft."""
from backend.config import SETTLED_TTL
from backend.league import league_meta, owners, standings
from backend.players import player_brief, players_by_ids
from backend.sources.basketnews import Q_DRAFT, gql


def draft_payload(fid):
    """Every pick of the league's draft in order, with where the player is now."""
    meta = league_meta(fid)
    rec = gql(Q_DRAFT, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"] or {}
    picks = sorted(((rec.get("draft") or {}).get("picks") or []), key=lambda p: p["id"])
    teams = {r["team"]["id"]: r["team"] for r in standings(meta)[1]}
    per_round = len(teams) or 1
    ids = [p["player"]["id"] for p in picks if p.get("player")]
    pmap = players_by_ids(meta, ids, meta["latestRound"], meta["currentRound"])
    own = owners(meta, meta["currentRound"])
    rows = []
    for i, pick in enumerate(picks):
        pid = (pick.get("player") or {}).get("id")
        rows.append({
            "overall": i + 1, "round": i // per_round + 1, "pick": i % per_round + 1,
            "team": teams.get(pick["fantasyTeamId"]) or {"id": pick["fantasyTeamId"], "title": "?"},
            "player": player_brief(pmap.get(pid), pid),
            "owner": (own.get(pid) or {}).get("team"),
        })
    return {"league": meta, "picks": rows, "teams": list(teams.values())}
