"""Season datasets for metrics.py, built either from the stored history or from the round
breakdowns the records page already has.

from_index()       the SQLite index of data/ (finished rounds as archived, saved lineups,
                   transfers, draft): what api/.../analytics.json is computed from
from_breakdowns()  the live per-round breakdowns of analytics/season.py, so the records
                   page runs the very same formulas
"""
import json


def empty(league, teams):
    return {"league": league, "teams": teams, "rounds": [], "future": {}, "transfers": [], "draft": [], "fp": {},
            "rosters": {}}


def from_breakdowns(meta, breakdowns, team_names, future=None):
    league = {"id": meta["id"], "title": meta["title"], "format": meta["format"]} if meta else None
    ds = empty(league,
               {tid: {"id": tid, "title": title} for tid, title in team_names.items()})
    for bd in breakdowns:
        ds["rounds"].append({
            "round": bd["round"],
            "scores": bd["scores"],
            "matchups": [{"team1": g["winner"]["id"], "team2": g["loser"]["id"], "score1": g["ws"], "score2": g["ls"]}
                         for g in bd["games"]],
            "lineups": {tid: [{"id": p["id"], "card": p["card"], "captain": p["captain"], "fp": p.get("roundPts"),
                               "positions": p.get("positions") or []} for p in lu["players"]]
                        for tid, lu in bd["lineups"].items()},
        })
    ds["future"] = future or {}
    return ds


def from_index(conn, meta, future=None):
    """The league's finished rounds as stored. Rounds whose players were not archived get no
    lineups (their lineups cannot be scored); nothing is filled in."""
    fid = meta["id"]
    q = conn.execute
    teams = {r["team_id"]: {"id": r["team_id"], "title": r["title"], "owner": r["owner"]}
             for r in q("SELECT team_id, title, owner FROM teams WHERE league_id = ? ORDER BY team_id", (fid,))}
    ds = empty({"id": fid, "title": meta["title"], "format": meta["format"]}, teams)
    fp, positions = {}, {}
    for r in q("SELECT round, player_id, fp, positions FROM player_rounds WHERE season = ? AND competition_id = ? "
               "AND point_calc = ?", (meta.get("seasonYear"), meta["leagueId"], meta["pointCalcSystem"])):
        fp.setdefault(r["round"], {})[r["player_id"]] = r["fp"]
        positions.setdefault(r["round"], {})[r["player_id"]] = json.loads(r["positions"] or "[]")
    ds["fp"] = fp
    rounds = [r[0] for r in q("SELECT DISTINCT round FROM standings WHERE league_id = ? ORDER BY round", (fid,))]
    for rnd in rounds:
        scores = {r["team_id"]: r["points_round"] for r in
                  q("SELECT team_id, points_round FROM standings WHERE league_id = ? AND round = ? ORDER BY position",
                    (fid, rnd))}
        matchups = [{"team1": r["team1_id"], "team2": r["team2_id"], "score1": r["score1"], "score2": r["score2"]}
                    for r in q("SELECT * FROM matchups WHERE league_id = ? AND round = ? ORDER BY match_id", (fid, rnd))]
        lineups = {}
        if rnd in fp:
            for r in q("SELECT team_id, player_id, card, captain FROM lineup_slots WHERE league_id = ? AND round = ? "
                       "ORDER BY team_id, rowid", (fid, rnd)):
                lineups.setdefault(r["team_id"], []).append({
                    "id": r["player_id"], "card": r["card"], "captain": bool(r["captain"]),
                    "fp": fp[rnd].get(r["player_id"]), "positions": positions[rnd].get(r["player_id"]) or []})
        ds["rounds"].append({"round": rnd, "scores": scores, "matchups": matchups, "lineups": lineups})
    for r in q("SELECT round, team_id, player_id FROM lineup_slots WHERE league_id = ?", (fid,)):
        ds["rosters"].setdefault(r["round"], {}).setdefault(r["team_id"], set()).add(r["player_id"])
    moved = {}
    for r in q("SELECT transfer_id, side, player_id FROM transfer_players WHERE league_id = ? ORDER BY rowid", (fid,)):
        moved.setdefault((r["transfer_id"], r["side"]), []).append(r["player_id"])
    ds["transfers"] = [
        {"id": r["transfer_id"], "round": r["round"], "kind": r["kind"],
         "offer": {"team": r["offer_team_id"], "players": moved.get((r["transfer_id"], "offer"), [])},
         "request": {"team": r["request_team_id"], "players": moved.get((r["transfer_id"], "request"), [])}}
        for r in q("SELECT * FROM transfers WHERE league_id = ? ORDER BY round, at, transfer_id", (fid,))]
    ds["draft"] = [{"pick": r["pick_no"], "team": r["team_id"], "player": r["player_id"]}
                   for r in q("SELECT * FROM draft_picks WHERE league_id = ? ORDER BY pick_no", (fid,))]
    ds["future"] = future or {}
    return ds
