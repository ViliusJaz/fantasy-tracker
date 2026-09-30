"""/api/league/<id>/transfers: signings, trades, credits and bids."""
from backend.analytics.transfers import transfer_roi
from backend.errors import UpstreamError
from backend.league import league_meta, raw_transfers, standings
from backend.players import player_brief, players_by_ids
from backend.sources import basketnews as bn


def _transfer_side(side, teams, pmap):
    return {
        "team": (teams.get(side["team"]["id"]) or side["team"]) if side["team"] else None,
        "players": [player_brief(pmap.get(p["id"]), p["id"], p["name"]) for p in side["players"]],
        "credits": side["credits"],
    }


def transfers_payload(fid):
    """Processed free-agent signings and trades per round, credits left per team, and the
    public bid counts for the coming round."""
    meta = league_meta(fid)
    cur = meta["currentRound"]
    raw = raw_transfers(meta)
    try:
        bids = bn.fetch_bids(fid, cur)
    except UpstreamError:
        bids = []

    table = standings(meta)[1]
    teams = {r["team"]["id"]: r["team"] for r in table}
    ids = {pid for _, ts in raw for t in ts for side in ("offer", "request") for pid in t[side]["listed"]}
    ids |= {b["playerId"] for b in bids}
    pmap = players_by_ids(meta, sorted(ids), meta["latestRound"], cur) if ids else {}

    start = meta["draft"].get("startingCredits") or 0
    summary = {tid: {"team": team, "credits": start, "signings": 0, "trades": 0, "spent": 0}
               for tid, team in teams.items()}
    moves = []
    for rnd, ts in raw:
        for t in ts:
            offer, request = _transfer_side(t["offer"], teams, pmap), _transfer_side(t["request"], teams, pmap)
            change = request["credits"] - offer["credits"]  # for the offering team; the other side gets -change
            kind = t["kind"]
            moves.append({"id": t["id"], "type": kind, "round": rnd, "at": t["at"],
                          "offer": offer, "request": request, "creditChange": change})
            for side, delta in ((offer, change), (request, -change)):
                row = summary.get((side["team"] or {}).get("id"))
                if not row:
                    continue
                row["credits"] += delta
                row["spent"] += max(-delta, 0)
                row["trades" if kind == "trade" else "signings"] += 1
    moves.sort(key=lambda m: (m["round"], m["at"] or ""), reverse=True)
    roi = transfer_roi(meta, moves)
    for row in summary.values():
        mine = [r["net"] for m in moves for tid, r in m.get("roi", {}).items() if tid == row["team"]["id"]]
        row["roi"] = round(sum(mine), 2) if roi and mine else None

    upcoming = [{"player": player_brief(pmap.get(b["playerId"]), b["playerId"]),
                 "highestBid": b["highestBid"], "totalBids": b["totalBids"] or 0}
                for b in bids]
    upcoming.sort(key=lambda b: (-b["totalBids"], -(b["highestBid"] or 0)))
    order = {r["team"]["id"]: r["position"] for r in table}
    return {
        "league": meta,
        "moves": moves,
        "teams": sorted(summary.values(), key=lambda r: order.get(r["team"]["id"], 99)),
        "upcoming": upcoming,
        "lock": meta.get("transferLock"),
        "usesCredits": meta["draft"].get("tradingMethod") == "credits",
        "startingCredits": start,
    }
