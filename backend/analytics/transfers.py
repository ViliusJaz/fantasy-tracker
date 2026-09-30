"""Return on transfers: what the players a team got scored for it, against what it gave away."""
from backend import history
from backend.players import players
from backend.util import pool_map


def transfer_roi(meta, moves):
    """Adds move["roi"] = {teamId: {inFp, outFp, net, rounds}}: fantasy points the players a
    team got scored for it in rounds since the move (while still on the team; a round in progress
    counts with its points so far), what
    the players it gave away scored anywhere, and the difference. False when no round has
    finished since any move."""
    finished = list(range(meta["firstRound"], meta["latestRound"] + 1))  # includes a round in progress
    first = min((m["round"] for m in moves), default=None)
    rounds = [r for r in finished if first is not None and r >= first]
    if not rounds:
        return False
    pts = {r: {v["id"]: v.get("roundPts") or 0 for v in pm.values()}
           for r, pm in pool_map(lambda r: (r, players(meta, r, r)), rounds)}
    snaps = history.lineup_rounds(meta["id"])

    def kept(tid, pid, r):
        team = ((snaps.get(str(r)) or {}).get("teams") or {}).get(tid)
        return True if not team else any(p["id"] == pid for p in team["players"])

    for m in moves:
        after = [r for r in rounds if r >= m["round"]]
        sides = [(m["offer"], m["request"])] + ([(m["request"], m["offer"])] if m["type"] == "trade" else [])
        m["roi"] = {}
        for own, other in sides:
            tid = (own["team"] or {}).get("id")
            if not tid:
                continue
            got = sum(pts[r].get(p["id"], 0) for r in after for p in other["players"] if kept(tid, p["id"], r))
            gave = sum(pts[r].get(p["id"], 0) for r in after for p in own["players"])
            m["roi"][tid] = {"inFp": round(got, 2), "outFp": round(gave, 2), "net": round(got - gave, 2),
                             "rounds": len(after)}
    return True
