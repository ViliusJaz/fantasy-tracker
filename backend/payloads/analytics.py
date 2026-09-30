"""/api/league/<id>/analytics: season metrics computed from the stored history.

Not shown on the site yet: a place for pages to come. Every number comes from
analytics/metrics.py over the SQLite index of data/ (finished rounds only), plus the
future schedule for strength of schedule. Numbers only, no text, so both language files
are the same. What is missing (rounds without saved lineups) is listed under "basedOn",
never filled in.
"""
from backend.analytics import dataset, metrics
from backend.league import league_meta, schedule
from backend.storage import db, ingest
from backend.util import pool_map


def _future(meta):
    if meta["format"] != "head_to_head":
        return {}
    start = meta["currentRound"] + (1 if meta["roundStarted"] else 0)
    return {r: [{"team1": (m["team1"] or {}).get("id"), "team2": (m["team2"] or {}).get("id")} for m in games]
            for r, games in pool_map(lambda r: (r, schedule(meta, r)), list(range(start, meta["totalRounds"])))}


def build(meta, ds):
    """The analytics document for a dataset (separate from I/O for the tests)."""
    h2h = meta["format"] == "head_to_head"
    results = metrics.results(ds) if h2h else {}
    all_play = metrics.all_play(ds)
    luck = metrics.luck(ds) if h2h else {}
    eff = metrics.efficiency(ds)
    captains = metrics.captain_loss(ds)
    strength = metrics.schedule_strength(ds) if h2h else {}
    rounds = [rd["round"] for rd in ds["rounds"]]
    net = metrics.transfer_net(ds["transfers"], ds["fp"], rounds, ds["rosters"])
    draft = metrics.draft_value(ds)
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731

    transfer_net = {}
    for per_team in net.values():
        for tid, row in per_team.items():
            transfer_net[tid] = round(transfer_net.get(tid, 0) + row["net"], 2)

    teams = []
    for tid, team in ds["teams"].items():
        entry = {"team": team, "allPlay": all_play.get(tid), "efficiency": eff.get(tid),
                 "captainLoss": captains.get(tid), "transferNet": transfer_net.get(tid),
                 "draftValue": (draft["teams"].get(tid) or {}).get("value")}
        if h2h:
            longest_w, longest_l, (kind, run) = metrics.streaks([x["result"] for x in results.get(tid, [])])
            st = strength.get(tid) or {}
            entry.update(luck.get(tid) or {}, streak={"kind": kind, "length": run}, longestWin=longest_w,
                         longestLoss=longest_l,
                         schedule={k: mean(st.get(k) or []) for k in ("faced", "against", "next5", "rest")})
        teams.append(entry)

    rivalries = [{"teams": list(pair), **rec} for pair, rec in sorted(metrics.rivalries(ds).items())] if h2h else []
    with_lineups = [rd["round"] for rd in ds["rounds"] if rd["lineups"]]
    return {
        "league": meta,
        "basedOn": {"source": "history", "rounds": rounds, "roundsWithLineups": with_lineups,
                    "missingLineups": [r for r in rounds if r not in with_lineups],
                    "transfers": len(ds["transfers"]), "draftPicks": len(ds["draft"])},
        "teams": teams,
        "rivalries": rivalries,
        "draft": draft,
        "transfers": [{"id": t["id"], "round": t["round"], "kind": t["kind"], "net": net.get(t["id"], {})}
                      for t in ds["transfers"]],
        "records": metrics.records(ds),
    }


def analytics_payload(fid):
    meta = league_meta(fid)
    conn = db.connect()
    try:
        ingest.refresh(conn)  # a no-op after a build; keeps the local server current
        ds = dataset.from_index(conn, meta, _future(meta))
    finally:
        conn.close()
    return build(meta, ds)
