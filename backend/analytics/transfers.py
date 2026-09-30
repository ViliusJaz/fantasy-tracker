"""Return on transfers: what the players a team got scored for it, against what it gave away."""
from backend import history
from backend.analytics import metrics
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
    fp = {r: {v["id"]: v.get("roundPts") or 0 for v in pm.values()}
          for r, pm in pool_map(lambda r: (r, players(meta, r, r)), rounds)}
    rosters = {int(r): {tid: {p["id"] for p in team["players"]} for tid, team in (snap.get("teams") or {}).items()}
               for r, snap in history.lineup_rounds(meta["id"]).items() if r.isdigit()}
    moves_in = [{"id": m["id"], "round": m["round"], "kind": m["type"],
                 "offer": {"team": (m["offer"]["team"] or {}).get("id"), "players": [p["id"] for p in m["offer"]["players"]]},
                 "request": {"team": (m["request"]["team"] or {}).get("id"),
                             "players": [p["id"] for p in m["request"]["players"]]}} for m in moves]
    net = metrics.transfer_net(moves_in, fp, rounds, rosters)
    for m in moves:
        m["roi"] = net[m["id"]]
    return True
