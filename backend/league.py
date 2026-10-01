"""A fantasy league from BasketNews: its settings, standings, schedule, lineups, owners, transfers."""
from backend import history
from backend.config import LIVE_TTL, SETTLED_TTL
from backend.errors import NotFound, UpstreamError
from backend.i18n import L
from backend.rounds import keep_for, round_ttl
from backend.scoring import _slot_sort_key, slot_label, slot_of
from backend.sources import basketnews as bn
from backend.util import pool_map


def league_meta(fid):
    """Everything the pages need to know about a league and its competition's calendar."""
    rec = bn.fetch_league(fid)
    if not rec:
        raise NotFound(L("Lyga nerasta", "League not found"))
    comp = bn.fetch_competitions().get(rec["competitionId"])
    if not comp:
        raise UpstreamError(L("Nerasta lygos varžybų informacija", "Competition info not found"))
    current = comp["currentRound"]
    first = comp["firstRound"]
    # Round whose results are shown by default: the live one, or the last finished one.
    latest = current if comp["roundStarted"] else max(current - 1, first)
    return {
        "id": rec["id"],
        "title": rec["title"],
        "format": rec["format"],  # "head_to_head" | "classic"
        "leagueId": rec["competitionId"],
        "pointCalcSystem": rec["pointCalcSystem"],
        "teamsCount": rec["teamsCount"],
        "commissioner": rec["commissioner"],
        "competition": L(comp["name"]["lt"], comp["name"]["en"]),
        "currentRound": current,
        "roundStarted": comp["roundStarted"],
        "firstRound": first,
        "latestRound": latest,
        "totalRounds": comp["totalRounds"],
        "injuryReportUrl": comp["injuryReportUrl"],
        # Free-agent bids and trades are processed when this lock next changes (3 h before a round).
        "transferLock": comp["transferLock"],
        "draft": rec["draft"],
        "bnLeagueId": comp["bnLeagueId"],
        "seasonYear": comp["seasonYear"],
        "url": f"https://fantasy.basketnews.com/fantasy-leagues/{fid}/leaderboards",
    }


def fetch_standings_round(meta, rnd):
    return bn.fetch_standings(meta, rnd, round_ttl(meta, rnd), keep_for(meta, rnd))


def league_teams(meta):
    return bn.fetch_teams(meta["id"])


def standings(meta, rnd=None):
    """Standings after `rnd` (default: latest). Falls back to earlier rounds if the
    requested one has not been scored yet, and to an all-zero table before round 1."""
    rnd = meta["latestRound"] if rnd is None else rnd
    for r in range(rnd, meta["firstRound"] - 1, -1):
        rows = fetch_standings_round(meta, r)
        if rows and not (r > meta["firstRound"] and not_scored(rows)):
            return r, rows
    rows = [
        {"team": t, "position": i + 1, "positionGained": 0, "wins": 0, "losses": 0, "ties": 0,
         "pointsTotal": 0, "pointsRound": 0}
        for i, t in enumerate(league_teams(meta))
    ]
    return None, rows


def not_scored(rows):
    """A points league's table for a round BasketNews has not scored yet: while the round is on it
    lists every team with zero points, season total included."""
    return all(not r.get("pointsTotal") and not r.get("pointsRound") for r in rows)


def schedule(meta, rnd):
    return bn.fetch_schedule(meta["id"], rnd, round_ttl(meta, rnd), keep_for(meta, rnd))


def lineups(meta):
    """Current lineup of every team: {teamId: {round, formation, players: [{id, card, slot, captain}]}}.
    Every fetch is also queued for the history, so past rounds stay viewable after the API moves on."""
    result = {}
    for lineup in bn.fetch_lineups(meta["id"]):
        entries = [{"id": p["id"], "card": p["card"], "slot": slot_of(p["card"]), "captain": p["captain"]}
                   for p in lineup["players"]]
        entries.sort(key=_slot_sort_key)
        result[lineup["teamId"]] = {"round": lineup["round"], "formation": lineup["formation"], "players": entries}
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
    """[(round, [transfer])] of every round so far, oldest first (see basketnews.fetch_transfers)."""
    cur = meta["currentRound"]

    def fetch(r):
        return r, bn.fetch_transfers(meta["id"], r, LIVE_TTL if r >= cur else SETTLED_TTL, keep_for(meta, r))

    return pool_map(fetch, list(range(meta["firstRound"], cur + 1)))


def moved_players(side):
    """Player ids that changed hands on one side of a transfer."""
    return [p["id"] for p in side["players"]]
