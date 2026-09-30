"""A fantasy league from BasketNews: its settings, standings, schedule, lineups, owners, transfers."""
from backend import history
from backend.config import LIVE_TTL, SETTLED_TTL
from backend.errors import NotFound, UpstreamError
from backend.i18n import L
from backend.rounds import round_ttl
from backend.scoring import _slot_sort_key, slot_label, slot_of
from backend.sources.basketnews import (
    Q_CLASSIC_STANDINGS, Q_FANTASY_LEAGUE, Q_H2H_STANDINGS, Q_LINEUPS, Q_SCHEDULE, Q_TEAMS, Q_TRANSFERS,
    competitions, gql, owner_name, team_ref,
)
from backend.util import pool_map


def league_meta(fid):
    rec = gql(Q_FANTASY_LEAGUE, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"]
    if not rec:
        raise NotFound(L("Lyga nerasta", "League not found"))
    comp = competitions().get(rec["leagueId"])
    if not comp:
        raise UpstreamError(L("Nerasta lygos varžybų informacija", "Competition info not found"))
    current = comp["currentFantasyRound"]
    first = comp.get("startingFantasyRound") or 0
    # Round whose results are shown by default: the live one, or the last finished one.
    latest = current if comp["roundStarted"] else max(current - 1, first)
    injury_url = ((comp.get("en") or {}).get("injuryReportUrl") or comp.get("injury_report_url")
                  or (comp.get("translation") or {}).get("injuryReportUrl") or None)
    return {
        "id": rec["id"],
        "title": rec["title"].strip(),
        "format": rec["format"],  # "head_to_head" | "classic"
        "leagueId": rec["leagueId"],
        "pointCalcSystem": rec.get("pointCalcSystem") or "modern",
        "teamsCount": rec.get("fantasyTeamsCount"),
        "commissioner": owner_name(rec.get("publicUser")),
        "competition": L(comp["translation"]["name"], (comp.get("en") or {}).get("name") or comp["translation"]["name"]),
        "currentRound": current,
        "roundStarted": comp["roundStarted"],
        "firstRound": first,
        "latestRound": latest,
        "totalRounds": comp["totalRounds"],
        "injuryReportUrl": injury_url,
        # Free-agent bids and trades are processed when this lock next changes (3 h before a round).
        "transferLock": comp.get("activeDraftTradeLock"),
        "draft": {"date": rec.get("draftDate"), "order": rec.get("draftOrder"), "pickOrder": rec.get("pickOrder"),
                  "tradingMethod": rec.get("draftTradingMethod"), "startingCredits": rec.get("draftStartingCredits")},
        "bnLeagueId": comp.get("basketnewsApiLeagueId"),
        "seasonYear": comp.get("seasonYear"),
        "url": f"https://fantasy.basketnews.com/fantasy-leagues/{fid}/leaderboards",
    }


def fetch_standings_round(meta, rnd):
    ttl = round_ttl(meta, rnd)
    if meta["format"] == "head_to_head":
        recs = gql(Q_H2H_STANDINGS, {"id": meta["id"], "round": rnd}, ttl)["allHeadToHeadScoreRecordsFromClient"]["records"]
        return [
            {
                "team": team_ref(r["fantasyTeam"]),
                "position": r["position"],
                "positionGained": r.get("positionGained") or 0,
                "wins": r["wins"],
                "losses": r["losses"],
                "ties": r["ties"],
                "pointsTotal": (r.get("fantasyTeamScore") or {}).get("pointsTotal") or 0,
                "pointsRound": (r.get("fantasyTeamScore") or {}).get("pointsGained") or 0,
            }
            for r in recs
        ]
    recs = gql(
        Q_CLASSIC_STANDINGS, {"leagueId": meta["leagueId"], "id": meta["id"], "round": rnd}, ttl
    )["allFantasyTeamScoreRecordsFromClient"]["records"]
    return [
        {
            "team": team_ref(r["fantasyTeam"]),
            "position": r["position"],
            "positionGained": r.get("positionGained") or 0,
            "roundPosition": r.get("roundPosition"),
            "pointsTotal": r.get("pointsTotal") or 0,
            "pointsRound": r.get("pointsGained") or 0,
        }
        for r in recs
    ]


def league_teams(meta):
    recs = gql(Q_TEAMS, {"id": meta["id"]})["fantasyLeagueTeamsFromClient"]["records"]
    return [team_ref(t) for t in recs]


def standings(meta, rnd=None):
    """Standings after `rnd` (default: latest). Falls back to earlier rounds if the
    requested one has not been scored yet, and to an all-zero table before round 1."""
    rnd = meta["latestRound"] if rnd is None else rnd
    for r in range(rnd, meta["firstRound"] - 1, -1):
        rows = fetch_standings_round(meta, r)
        if rows:
            return r, rows
    rows = [
        {"team": t, "position": i + 1, "positionGained": 0, "wins": 0, "losses": 0, "ties": 0,
         "pointsTotal": 0, "pointsRound": 0}
        for i, t in enumerate(league_teams(meta))
    ]
    return None, rows


def schedule(meta, rnd):
    recs = gql(Q_SCHEDULE, {"id": meta["id"], "round": rnd}, round_ttl(meta, rnd))
    out = []
    for m in recs["allHeadToHeadScheduleRecordsFromClient"]["records"]:
        out.append({
            "id": m["id"],
            "team1": team_ref(m["fantasyTeam1"]) if m.get("fantasyTeam1") else None,
            "team2": team_ref(m["fantasyTeam2"]) if m.get("fantasyTeam2") else None,
            "score1": m.get("fantasyTeam1ScorePoints") or 0,
            "score2": m.get("fantasyTeam2ScorePoints") or 0,
        })
    return out


def lineups(meta):
    """Current lineup of every team: {teamId: {round, formation, players: [{id, card, slot, captain}]}}.
    Every fetch is also queued for the history, so past rounds stay viewable after the API moves on."""
    data = gql(Q_LINEUPS, {"id": meta["id"]})["draftLeagueFantasyTeamLineupsFromClient"]
    result = {}
    for lineup in data:
        entries = [
            {"id": lp["player"]["id"], "card": lp["cardIdentifier"], "slot": slot_of(lp["cardIdentifier"]),
             "captain": bool(lp.get("captain"))}
            for lp in lineup["players"] if lp.get("player")
        ]
        entries.sort(key=_slot_sort_key)
        result[lineup["fantasyTeamId"]] = {
            "round": lineup["fantasyRound"], "formation": lineup.get("formation"), "players": entries,
        }
    history.observe_lineups(meta, result)
    return result


def owners(meta, rnd):
    """Who holds each player in round `rnd`: {playerId: {"team": {...}, "slotLabel": "G" | "6th" | ... | None}}.
    Uses that round's saved lineup; falls back to today's rosters (slot unknown)."""
    names = {r["team"]["id"]: r["team"] for r in standings(meta)[1]}
    current = lineups(meta)
    by_team, exact = None, True
    if any(lu["round"] == rnd for lu in current.values()):
        by_team = {tid: lu["players"] for tid, lu in current.items()}
    else:
        snap = history.lineup_rounds(meta["id"]).get(str(rnd))
        if snap:
            by_team = {tid: lu["players"] for tid, lu in snap["teams"].items()}
        else:
            by_team, exact = {tid: lu["players"] for tid, lu in current.items()}, False
    out = {}
    for tid, plist in by_team.items():
        for p in plist:
            out[p["id"]] = {"team": names.get(tid, {"id": tid, "title": ""}),
                            "slotLabel": slot_label(p["card"]) if exact else None,
                            "slot": p["slot"] if exact else None}
    return out


def raw_transfers(meta):
    """[(round, transfer)] as BasketNews lists them, oldest round first."""
    cur = meta["currentRound"]

    def fetch(r):
        ttl = LIVE_TTL if r >= cur else SETTLED_TTL
        return r, gql(Q_TRANSFERS, {"fantasyLeagueId": meta["id"], "fantasyRound": r}, ttl=ttl)["draftTransfersFromClient"] or []

    return pool_map(fetch, list(range(meta["firstRound"], cur + 1)))


def moved_players(item):
    """Player ids that actually changed hands on one side of a transfer."""
    listed = (item or {}).get("players") or []
    return [x["player"]["id"] for x in ([x for x in listed if x.get("traded")] or listed) if x.get("player")]
