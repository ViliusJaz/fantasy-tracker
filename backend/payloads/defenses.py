"""/api/league/<id>/defenses: the clubs ordered by defense, for the player card's
"against the best / worst defenses" split (one small file instead of a copy in every player)."""
from backend.league import league_meta
from backend.previews import defense_table


def defenses_payload(fid):
    meta = league_meta(fid)
    rows, n = defense_table(meta)
    return {"league": meta, "teams": n, "top": n // 2, "rows": rows}
