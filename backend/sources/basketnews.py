"""BasketNews Fantasy GraphQL API: the queries, the client with its cache, and the adapters that
turn raw records into the tracker's player / game / team dicts."""
import json
import re
import threading
import time
import urllib.request

from backend import cache, log, net
from backend.config import BROWSER_UA, LIVE_TTL, LOCALE, SETTLED_TTL
from backend.errors import UpstreamError
from backend.i18n import L
from backend.util import SingleFlight


GRAPHQL_URL = "https://fantasy.basketnews.com/backend/graphql"

LOGO_URL = "https://fantasy.basketnews.com/backend/api/storage/file/"

_cache = {}

_cache_lock = threading.Lock()
_flights = SingleFlight()
LOG = log.get("fetch")

# When set (export.py does), every answer is kept this long, whatever its usual cache time:
# one export run reads each thing once and all its pages agree with each other.
RUN_TTL = None


def cache_key(query, variables):
    return query + json.dumps(variables, sort_keys=True)


def cache_answer(query, variables, data, ttl):
    """Put an answer fetched some other way (e.g. in a batch) into the cache."""
    with _cache_lock:
        _cache[cache_key(query, variables)] = (time.time() + ttl, data)


def gql(query, variables, ttl=LIVE_TTL, keep=0):
    """`data` of a GraphQL answer, cached in memory for `ttl` seconds and, with `keep`, also
    kept between runs (backend/cache.py). Threads asking for the same thing at the same
    moment share one request."""
    ttl = RUN_TTL or ttl
    key = cache_key(query, variables)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]

    def load():
        data = cache.get(key) if keep else None
        if data is None:
            data = _post(query, variables)
            if keep:
                cache.put(key, "basketnews", data, keep)
        with _cache_lock:
            _cache[key] = (time.time() + ttl, data)
        return data
    return _flights.do(key, load)


def _post(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        # Same headers as the fantasy site's own page: Cloudflare in front of the API turns
        # away requests that do not look like they come from a browser on that site.
        headers={"Content-Type": "application/json", "Accept": "application/json, */*",
                 "User-Agent": BROWSER_UA, "Origin": "https://fantasy.basketnews.com",
                 "Referer": "https://fantasy.basketnews.com/", "Accept-Language": "lt,en;q=0.8",
                 # compressed answers are several times smaller (matters on mobile data)
                 "Accept-Encoding": "gzip"},
    )
    for attempt in (1, 2):
        try:
            payload = net.fetch(req, "basketnews", timeout=25, as_json=True)
        except net.FetchError as exc:
            raise UpstreamError(L(f"BasketNews nepasiekiamas: {exc}", f"BasketNews unreachable: {exc}")) from exc
        if not payload.get("errors"):
            return payload["data"]
        message = payload["errors"][0].get("message") or L("GraphQL klaida", "GraphQL error")
        if attempt == 1:  # BasketNews sometimes answers a query with an internal error once
            LOG.info("basketnews: GraphQL error %r, asking once more", message)
            time.sleep(0 if net.NO_WAIT else 1)
    raise UpstreamError(message)


Q_COMPETITIONS = """
query($locale: String!) {
  allLeagueRecordsFromClient(locale: $locale, game: "fantasy") {
    id currentFantasyRound roundStarted scoreRoundsAvailable totalRounds startingFantasyRound
    basketnewsApiLeagueId seasonYear injury_report_url
    activeDraftTradeLock { locked nextChange }
    translation(locale: $locale) { name shortName injuryReportUrl }
    en: translation(locale: "en") { name injuryReportUrl }
  }
}"""

Q_FANTASY_LEAGUE = """
query($id: String!) {
  fantasyLeagueRecordFromClient(id: $id) {
    id title type format leagueId pointCalcSystem fantasyTeamsCount draftDate
    draftOrder pickOrder draftTradingMethod draftStartingCredits
    publicUser { firstName lastNameInitial }
  }
}"""

Q_TEAMS = """
query($id: String!) {
  fantasyLeagueTeamsFromClient(fantasyLeagueId: $id, offset: 0, limit: 100) {
    records { id title publicUser { firstName lastNameInitial } }
  }
}"""

Q_H2H_STANDINGS = """
query($id: String, $round: Int) {
  allHeadToHeadScoreRecordsFromClient(fantasyLeagueId: $id, fantasyRound: $round, offset: 0, limit: 100, sort: "position") {
    records {
      fantasyTeam { id title publicUser { firstName lastNameInitial } }
      wins losses ties position positionGained
      fantasyTeamScore { pointsGained pointsTotal }
    }
  }
}"""

Q_CLASSIC_STANDINGS = """
query($leagueId: String, $id: String, $round: Int) {
  allFantasyTeamScoreRecordsFromClient(leagueId: $leagueId, fantasyLeagueId: $id, fantasyRound: $round, offset: 0, limit: 100, sort: "position") {
    records {
      fantasyTeam { id title publicUser { firstName lastNameInitial } }
      position positionGained roundPosition pointsTotal pointsGained
    }
  }
}"""

Q_SCHEDULE = """
query($id: String, $round: Int) {
  allHeadToHeadScheduleRecordsFromClient(fantasyLeagueId: $id, fantasyRound: $round) {
    records {
      id
      fantasyTeam1 { id title publicUser { firstName lastNameInitial } }
      fantasyTeam2 { id title publicUser { firstName lastNameInitial } }
      fantasyTeam1ScorePoints fantasyTeam2ScorePoints
    }
  }
}"""

Q_LINEUPS = """
query($id: String!) {
  draftLeagueFantasyTeamLineupsFromClient(fantasyLeagueId: $id) {
    fantasyTeamId fantasyRound formation
    players { cardIdentifier position captain player { id } }
  }
}"""

GAME_FIELDS = """
  originalGameAt live completed canceled delayed
  team1 { points team { id abbreviation logo } }
  team2 { points team { id abbreviation logo } }
"""

STAT_FIELDS = ("s_gp s_time s_pts s_rbs s_orb s_drb s_ast s_stl s_blk s_tov "
               "s_2pm s_2pa s_3pm s_3pa s_ftm s_fta s_pf s_rf s_pm s_eff")

PLAYER_FIELDS = """
  id basketnewsApiPlayerId firstName lastName health photo
  team(leagueId: $leagueId, fantasyRound: $gamesRound) {
    number positions
    team {
      id abbreviation logo
      translation(locale: $locale) { name shortName }
      en: translation(locale: "en") { name }
      games(fantasyRound: $gamesRound, currentRound: false) { %s }
    }
  }
  roundPts: fantasy_pts(leagueId: $leagueId, pointCalcSystem: $pcs, fantasyRound: $statsRound)
  roundStats: stats(leagueId: $leagueId, fantasyRound: $statsRound) { %s }
  avgPts: fantasy_pts(leagueId: $leagueId, pointCalcSystem: $pcs)
  season: stats(leagueId: $leagueId) { %s }
""" % (GAME_FIELDS, STAT_FIELDS, STAT_FIELDS)

Q_PLAYERS = """
query($leagueId: String!, $locale: String!, $statsRound: Int, $gamesRound: Int, $pcs: String) {
  playersSearchRecordsFromClient(leagueId: $leagueId, fantasyRound: $gamesRound) {
    records { %s }
  }
}""" % PLAYER_FIELDS


def owner_name(user):
    if not user:
        return ""
    return f"{(user.get('firstName') or '').strip()} {user.get('lastNameInitial') or ''}.".strip()


def team_ref(team):
    return {"id": team["id"], "title": team["title"], "owner": owner_name(team.get("publicUser"))}


def game_view(game, club_id):
    home = game["team1"]["team"]["id"] == club_id
    me, opp = (game["team1"], game["team2"]) if home else (game["team2"], game["team1"])
    return {
        "at": game["originalGameAt"],
        "home": home,
        "opponent": opp["team"]["abbreviation"],
        "opponentLogo": LOGO_URL + opp["team"]["logo"] if opp["team"].get("logo") else None,
        "score": [me["points"], opp["points"]] if (game["completed"] or game["live"]) else None,
        "live": game["live"] and not game["completed"],  # upstream sometimes leaves finished games "live"
        "completed": game["completed"],
        "canceled": game["canceled"],
        "delayed": game["delayed"],
    }


def minutes(stats):
    return round((stats.get("s_time") or 0) / 60) if stats else None


def stat_line(st):
    """Box-score line (a round's totals, or season averages for `stats(leagueId)`)."""
    if not st or not st.get("s_gp"):
        return None
    get = lambda k: st.get(k) or 0  # noqa: E731
    return {
        # s_rbs is blocks *received*; rebounds are offensive + defensive
        "min": round(get("s_time") / 60, 1), "pts": get("s_pts"), "reb": get("s_orb") + get("s_drb"),
        "oreb": get("s_orb"), "dreb": get("s_drb"), "ast": get("s_ast"), "stl": get("s_stl"),
        "blk": get("s_blk"), "tov": get("s_tov"), "pf": get("s_pf"), "eff": get("s_eff"),
        "p2m": get("s_2pm"), "p2a": get("s_2pa"), "p3m": get("s_3pm"), "p3a": get("s_3pa"),
        "ftm": get("s_ftm"), "fta": get("s_fta"),
        "sec": get("s_time"), "ba": get("s_rbs"), "fd": get("s_rf"), "pm": get("s_pm"),
    }


def player_view(p):
    roster = p.get("team") or {}
    club = roster.get("team") or {}
    games = [game_view(g, club["id"]) for g in (club.get("games") or [])] if club else []
    round_stats = p.get("roundStats")
    season = p.get("season") or {}
    return {
        "id": p["id"],
        "bnId": p.get("basketnewsApiPlayerId"),
        "name": f"{p['firstName']} {p['lastName']}".strip(),
        "photo": p.get("photo"),
        "health": p.get("health"),
        "position": (roster.get("positions") or [None])[0],
        "positions": roster.get("positions") or [],
        "number": roster.get("number"),
        "club": {
            "abbr": club.get("abbreviation"),
            "name": (club.get("translation") or {}).get("shortName"),
            "fullName": (club.get("translation") or {}).get("name"),
            "nameEn": (club.get("en") or {}).get("name"),
            "logo": LOGO_URL + club["logo"] if club.get("logo") else None,
        } if club else None,
        "games": games,
        "roundPts": p.get("roundPts"),
        "roundPlayed": bool(round_stats and round_stats.get("s_gp")),
        "roundLine": stat_line(round_stats),
        "avgPts": p.get("avgPts"),
        "gamesPlayed": season.get("s_gp") or 0,
        "season": stat_line(season),
    }


def player_rounds_query(rounds):
    parts = []
    for r in rounds:
        parts.append(f"""
          r{r}_pts: fantasy_pts(leagueId: $leagueId, pointCalcSystem: $pcs, fantasyRound: {r})
          r{r}_st: stats(leagueId: $leagueId, fantasyRound: {r}) {{ {STAT_FIELDS} }}
          r{r}_tm: team(leagueId: $leagueId, fantasyRound: {r}) {{
            team {{ id abbreviation games(fantasyRound: {r}, currentRound: false) {{ {GAME_FIELDS} }} }}
          }}""")
    return ("query($id: String!, $leagueId: String!, $locale: String!, $pcs: String, $statsRound: Int, $gamesRound: Int) {"
            " playerRecordFromClient(id: $id) {" + PLAYER_FIELDS + "".join(parts) + "} }")


Q_DRAFT = """
query($id: String!) {
  fantasyLeagueRecordFromClient(id: $id) {
    draft { picks { id fantasyTeamId player { id } } }
  }
}"""

_TRANSFER_SIDE = "fantasyTeam { id title } players { player { id firstName lastName } traded } credits"

Q_TRANSFERS = """
query($fantasyLeagueId: String!, $fantasyRound: Int!) {
  draftTransfersFromClient(fantasyLeagueId: $fantasyLeagueId, fantasyRound: $fantasyRound) {
    id type updatedAt
    offer { %s }
    request { %s }
  }
}""" % (_TRANSFER_SIDE, _TRANSFER_SIDE)

# Public count / top bid per free agent for the coming round (who bid stays private).
Q_BID_SUMMARY = """
query($fantasyLeagueId: String!, $fantasyRound: Int!) {
  draftFreeAgentBidsSummaryFromClient(fantasyLeagueId: $fantasyLeagueId, fantasyRound: $fantasyRound) {
    player { id } highestBid totalBids
  }
}"""


# --------------------------------------------------------------------------- fetchers
# Everything below returns the tracker's own records. Nothing outside this module reads
# BasketNews field names, so a renamed field upstream is fixed here only.

def fetch_competitions():
    """{competitionId: {currentRound, roundStarted, firstRound, totalRounds, name: {lt, en}, ...}}."""
    out = {}
    for c in gql(Q_COMPETITIONS, {"locale": LOCALE})["allLeagueRecordsFromClient"]:
        lt, en = c["translation"], c.get("en") or {}
        out[c["id"]] = {
            "currentRound": c["currentFantasyRound"],
            "roundStarted": c["roundStarted"],
            "firstRound": c.get("startingFantasyRound") or 0,
            "totalRounds": c["totalRounds"],
            "name": {"lt": lt["name"], "en": en.get("name") or lt["name"]},
            # the English report is the one the parser and the translations are made for
            "injuryReportUrl": (en.get("injuryReportUrl") or c.get("injury_report_url")
                                or lt.get("injuryReportUrl") or None),
            "transferLock": c.get("activeDraftTradeLock"),
            "bnLeagueId": c.get("basketnewsApiLeagueId"),
            "seasonYear": c.get("seasonYear"),
        }
    return out


def fetch_league(fid):
    """A fantasy league's settings, or None when BasketNews does not know it."""
    rec = gql(Q_FANTASY_LEAGUE, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"]
    if not rec:
        return None
    return {
        "id": rec["id"],
        "title": rec["title"].strip(),
        "format": rec["format"],  # "head_to_head" | "classic"
        "competitionId": rec["leagueId"],
        "pointCalcSystem": rec.get("pointCalcSystem") or "modern",
        "teamsCount": rec.get("fantasyTeamsCount"),
        "commissioner": owner_name(rec.get("publicUser")),
        "draft": {"date": rec.get("draftDate"), "order": rec.get("draftOrder"), "pickOrder": rec.get("pickOrder"),
                  "tradingMethod": rec.get("draftTradingMethod"), "startingCredits": rec.get("draftStartingCredits")},
    }


def fetch_standings(meta, rnd, ttl, keep=0):
    """Table after round `rnd`: [{team, position, positionGained, (wins, losses, ties | roundPosition),
    pointsTotal, pointsRound}]; empty when the round has not been scored."""
    if meta["format"] == "head_to_head":
        recs = gql(Q_H2H_STANDINGS, {"id": meta["id"], "round": rnd}, ttl, keep)["allHeadToHeadScoreRecordsFromClient"]["records"]
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
        Q_CLASSIC_STANDINGS, {"leagueId": meta["leagueId"], "id": meta["id"], "round": rnd}, ttl, keep
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


def fetch_teams(fid):
    return [team_ref(t) for t in gql(Q_TEAMS, {"id": fid})["fantasyLeagueTeamsFromClient"]["records"]]


def fetch_schedule(fid, rnd, ttl, keep=0):
    """Head-to-head matchups of a round: [{id, team1, team2, score1, score2}] (a team may be None)."""
    recs = gql(Q_SCHEDULE, {"id": fid, "round": rnd}, ttl, keep)
    return [
        {
            "id": m["id"],
            "team1": team_ref(m["fantasyTeam1"]) if m.get("fantasyTeam1") else None,
            "team2": team_ref(m["fantasyTeam2"]) if m.get("fantasyTeam2") else None,
            "score1": m.get("fantasyTeam1ScorePoints") or 0,
            "score2": m.get("fantasyTeam2ScorePoints") or 0,
        }
        for m in recs["allHeadToHeadScheduleRecordsFromClient"]["records"]
    ]


def fetch_lineups(fid):
    """Current lineup of every team: [{teamId, round, formation, players: [{id, card, captain}]}]."""
    return [
        {
            "teamId": lu["fantasyTeamId"],
            "round": lu["fantasyRound"],
            "formation": lu.get("formation"),
            "players": [{"id": lp["player"]["id"], "card": lp["cardIdentifier"], "captain": bool(lp.get("captain"))}
                        for lp in lu["players"] if lp.get("player")],
        }
        for lu in gql(Q_LINEUPS, {"id": fid})["draftLeagueFantasyTeamLineupsFromClient"]
    ]


def _transfer_side(item):
    item = item or {}
    team = item.get("fantasyTeam") or {}
    listed = item.get("players") or []
    moved = [x for x in listed if x.get("traded")] or listed  # without flags, everybody listed moves
    return {
        "team": {"id": team["id"], "title": team.get("title")} if team.get("id") else None,
        "players": [{"id": x["player"]["id"],
                     "name": f"{x['player'].get('firstName') or ''} {x['player'].get('lastName') or ''}".strip()}
                    for x in moved if x.get("player")],
        "listed": [x["player"]["id"] for x in listed if x.get("player")],
        "credits": item.get("credits") or 0,
    }


def fetch_transfers(fid, rnd, ttl, keep=0):
    """Processed moves of a round: [{id, kind: "trade" | "free_agent", at, offer, request}]. The offer
    is the team that proposed (for a signing: the team, the players it dropped and its bid); each
    side is {team, players (who changed hands), listed (everyone named), credits}."""
    recs = gql(Q_TRANSFERS, {"fantasyLeagueId": fid, "fantasyRound": rnd}, ttl, keep)["draftTransfersFromClient"] or []
    return [{"id": t["id"], "kind": "trade" if t.get("type") == "team" else "free_agent", "at": t.get("updatedAt"),
             "offer": _transfer_side(t.get("offer")), "request": _transfer_side(t.get("request"))} for t in recs]


def fetch_bids(fid, rnd):
    """Public free-agent bids for a round: [{playerId, highestBid, totalBids}] (who bid stays private)."""
    recs = gql(Q_BID_SUMMARY, {"fantasyLeagueId": fid, "fantasyRound": rnd})["draftFreeAgentBidsSummaryFromClient"] or []
    return [{"playerId": b["player"]["id"], "highestBid": b.get("highestBid"), "totalBids": b.get("totalBids")}
            for b in recs if b.get("player")]


def fetch_draft_picks(fid):
    """Every draft pick in order: [{id, teamId, playerId (None for an empty pick)}]."""
    rec = gql(Q_DRAFT, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"] or {}
    picks = sorted(((rec.get("draft") or {}).get("picks") or []), key=lambda p: p["id"])
    return [{"id": p["id"], "teamId": p["fantasyTeamId"], "playerId": (p.get("player") or {}).get("id")}
            for p in picks]


def _player_vars(meta, stats_round, games_round):
    return {"leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
            "gamesRound": games_round, "pcs": meta["pointCalcSystem"]}


def fetch_players(meta, stats_round, games_round, ttl, keep=0):
    """Every player of the competition: {playerId: player_view}. Points and box score are for
    `stats_round`, games are those of `games_round`."""
    data = gql(Q_PLAYERS, _player_vars(meta, stats_round, games_round), ttl, keep)
    return {p["id"]: player_view(p) for p in data["playersSearchRecordsFromClient"]["records"]}


def fetch_players_by_id(meta, ids, stats_round, games_round):
    """Players looked up one by one (the search list leaves some rostered players out)."""
    fields = "\n".join(f'p{n}: playerRecordFromClient(id: "{pid}") {{ {PLAYER_FIELDS} }}'
                       for n, pid in enumerate(ids) if re.fullmatch(r"[0-9a-f]{24}", pid))
    query = ("query($leagueId: String!, $locale: String!, $statsRound: Int, $gamesRound: Int, $pcs: String) {"
             + fields + "}")
    data = gql(query, _player_vars(meta, stats_round, games_round))
    return {rec["id"]: player_view(rec) for rec in data.values() if rec}


def fetch_player_rounds(meta, player_id, rounds):
    """One player with a line per round: (player_view, [{round, club, games, fp, line}]), or None.
    `line` is the round's box score, None when the player did not play."""
    data = gql(player_rounds_query(rounds), {
        "id": player_id, "leagueId": meta["leagueId"], "locale": LOCALE, "pcs": meta["pointCalcSystem"],
        "statsRound": meta["latestRound"], "gamesRound": meta["currentRound"],
    })["playerRecordFromClient"]
    if not data:
        return None
    per_round = []
    for r in rounds:
        club = ((data.get(f"r{r}_tm") or {}).get("team")) or {}
        per_round.append({
            "round": r,
            "club": club.get("abbreviation"),
            "games": [game_view(g, club["id"]) for g in club.get("games") or []] if club else [],
            "fp": data.get(f"r{r}_pts"),
            "line": stat_line(data.get(f"r{r}_st")),
        })
    return player_view(data), per_round
