"""BasketNews Fantasy GraphQL API: the queries, the client with its cache, and the adapters that
turn raw records into the tracker's player / game / team dicts."""
import http.client
import json
import threading
import time
import urllib.error
import urllib.request

from backend.config import BROWSER_UA, LIVE_TTL, LOCALE
from backend.errors import UpstreamError
from backend.i18n import L
from backend.net import read_body


GRAPHQL_URL = "https://fantasy.basketnews.com/backend/graphql"

LOGO_URL = "https://fantasy.basketnews.com/backend/api/storage/file/"

_cache = {}

_cache_lock = threading.Lock()

# When set (export.py does), every answer is kept this long, whatever its usual cache time:
# one export run reads each thing once and all its pages agree with each other.
RUN_TTL = None


def cache_key(query, variables):
    return query + json.dumps(variables, sort_keys=True)


def cache_answer(query, variables, data, ttl):
    """Put an answer fetched some other way (e.g. in a batch) into the cache."""
    with _cache_lock:
        _cache[cache_key(query, variables)] = (time.time() + ttl, data)


def gql(query, variables, ttl=LIVE_TTL):
    ttl = RUN_TTL or ttl
    key = cache_key(query, variables)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
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
    for attempt in range(3):  # BasketNews sometimes stalls mid-answer: retry before giving up
        try:
            payload = json.loads(read_body(req, timeout=25))
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise UpstreamError(L(f"BasketNews nepasiekiamas: {exc}", f"BasketNews unreachable: {exc}")) from exc
        except (OSError, http.client.HTTPException, ValueError) as exc:  # network, timeout, bad gzip / JSON
            if attempt == 2:
                raise UpstreamError(L(f"BasketNews nepasiekiamas: {exc}", f"BasketNews unreachable: {exc}")) from exc
        time.sleep(1 + attempt)
    if payload.get("errors"):
        raise UpstreamError(payload["errors"][0].get("message", L("GraphQL klaida", "GraphQL error")))
    data = payload["data"]
    with _cache_lock:
        _cache[key] = (now + ttl, data)
    return data


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


def competitions():
    return {c["id"]: c for c in gql(Q_COMPETITIONS, {"locale": LOCALE})["allLeagueRecordsFromClient"]}


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
