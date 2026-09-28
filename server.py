"""Local tracker for BasketNews Fantasy draft leagues.

    python3 fantasy-tracker/server.py      ->  http://127.0.0.1:8124

Serves the single-page UI from ./static and a small JSON API that proxies the
public BasketNews GraphQL backend (which does not allow browser CORS calls).
Tracked leagues live in ./leagues.json. Things the public API does not keep --
past-round lineups and injury history -- are recorded under ./data while the
server runs.
"""
import itertools
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
LEAGUES_FILE = ROOT / "leagues.json"
DATA_DIR = ROOT / "data"
LINEUPS_DIR = DATA_DIR / "lineups"
INJURY_LOG_FILE = DATA_DIR / "injuries.json"
GRAPHQL_URL = "https://fantasy.basketnews.com/backend/graphql"
LOGO_URL = "https://fantasy.basketnews.com/backend/api/storage/file/"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LOCALE = "lt"
HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", 8124))

LIVE_TTL = 60             # seconds to cache data that can still change
SETTLED_TTL = 60 * 60     # seconds to cache finished rounds
INJURY_TTL = 30 * 60      # seconds between injury report downloads
BACKGROUND_EVERY = 10 * 60  # seconds between lineup snapshots / injury checks

STARTER_PREFIXES = ("g", "f", "c")  # lineup card ids: g-1, f-1, c-1 start; b-* bench; i-* inactive


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # python.org builds on macOS ship without a CA bundle; the system one works.
    if Path("/etc/ssl/cert.pem").exists():
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


SSL_CTX = _ssl_context()
POOL = ThreadPoolExecutor(max_workers=8)


class UpstreamError(Exception):
    pass


class NotFound(Exception):
    pass


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- GraphQL

_cache = {}
_cache_lock = threading.Lock()


def gql(query, variables, ttl=LIVE_TTL):
    key = query + json.dumps(variables, sort_keys=True)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (fantasy-tracker)"},
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=25) as resp:
            payload = json.load(resp)
    except urllib.error.URLError as exc:
        raise UpstreamError(f"BasketNews nepasiekiamas: {exc}") from exc
    if payload.get("errors"):
        raise UpstreamError(payload["errors"][0].get("message", "GraphQL klaida"))
    data = payload["data"]
    with _cache_lock:
        _cache[key] = (now + ttl, data)
    return data


Q_COMPETITIONS = """
query($locale: String!) {
  allLeagueRecordsFromClient(locale: $locale, game: "fantasy") {
    id currentFantasyRound roundStarted scoreRoundsAvailable totalRounds startingFantasyRound
    injury_report_url
    translation(locale: $locale) { name shortName injuryReportUrl }
    en: translation(locale: "en") { injuryReportUrl }
  }
}"""

Q_FANTASY_LEAGUE = """
query($id: String!) {
  fantasyLeagueRecordFromClient(id: $id) {
    id title type format leagueId pointCalcSystem fantasyTeamsCount draftDate
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
  team1 { points team { id abbreviation } }
  team2 { points team { id abbreviation } }
"""

STAT_FIELDS = ("s_gp s_time s_pts s_rbs s_orb s_drb s_ast s_stl s_blk s_tov "
               "s_2pm s_2pa s_3pm s_3pa s_ftm s_fta s_pf s_eff")

PLAYER_FIELDS = """
  id basketnewsApiPlayerId firstName lastName health photo
  team(leagueId: $leagueId, fantasyRound: $gamesRound) {
    number positions
    team {
      id abbreviation logo
      translation(locale: $locale) { name shortName }
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


# --------------------------------------------------------------------------- config

_config_lock = threading.Lock()


def load_config():
    return read_json(LEAGUES_FILE, [])


def config_entry(fid):
    return next((e for e in load_config() if e["id"] == fid), None)


# --------------------------------------------------------------------------- league basics

def owner_name(user):
    if not user:
        return ""
    return f"{(user.get('firstName') or '').strip()} {user.get('lastNameInitial') or ''}.".strip()


def team_ref(team):
    return {"id": team["id"], "title": team["title"], "owner": owner_name(team.get("publicUser"))}


def competitions():
    return {c["id"]: c for c in gql(Q_COMPETITIONS, {"locale": LOCALE})["allLeagueRecordsFromClient"]}


def league_meta(fid):
    rec = gql(Q_FANTASY_LEAGUE, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"]
    if not rec:
        raise NotFound("Lyga nerasta")
    comp = competitions().get(rec["leagueId"])
    if not comp:
        raise UpstreamError("Nerasta lygos varžybų informacija")
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
        "competition": comp["translation"]["name"],
        "currentRound": current,
        "roundStarted": comp["roundStarted"],
        "firstRound": first,
        "latestRound": latest,
        "totalRounds": comp["totalRounds"],
        "injuryReportUrl": injury_url,
        "url": f"https://fantasy.basketnews.com/fantasy-leagues/{fid}/leaderboards",
    }


def is_live(meta, rnd):
    return rnd is not None and meta["roundStarted"] and rnd == meta["currentRound"]


def round_state(meta, rnd):
    if rnd < meta["currentRound"]:
        return "finished"
    return "live" if is_live(meta, rnd) else "upcoming"


def round_ttl(meta, rnd):
    return LIVE_TTL if rnd >= meta["currentRound"] - 1 else SETTLED_TTL


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


# --------------------------------------------------------------------------- players

def game_view(game, club_id):
    home = game["team1"]["team"]["id"] == club_id
    me, opp = (game["team1"], game["team2"]) if home else (game["team2"], game["team1"])
    return {
        "at": game["originalGameAt"],
        "home": home,
        "opponent": opp["team"]["abbreviation"],
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
        "min": round(get("s_time") / 60, 1), "pts": get("s_pts"), "reb": get("s_rbs"),
        "oreb": get("s_orb"), "dreb": get("s_drb"), "ast": get("s_ast"), "stl": get("s_stl"),
        "blk": get("s_blk"), "tov": get("s_tov"), "pf": get("s_pf"), "eff": get("s_eff"),
        "p2m": get("s_2pm"), "p2a": get("s_2pa"), "p3m": get("s_3pm"), "p3a": get("s_3pa"),
        "ftm": get("s_ftm"), "fta": get("s_fta"),
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


def players(meta, stats_round, games_round):
    """Every player of the competition: {playerId: view}. Points are for `stats_round`,
    games are those of `games_round`."""
    data = gql(Q_PLAYERS, {
        "leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
        "gamesRound": games_round, "pcs": meta["pointCalcSystem"],
    }, round_ttl(meta, min(stats_round, games_round)))
    return {p["id"]: player_view(p) for p in data["playersSearchRecordsFromClient"]["records"]}


def players_by_ids(meta, ids, stats_round, games_round):
    """Like players(), plus a direct lookup for rostered players the search list omits."""
    found = players(meta, stats_round, games_round)
    missing = [i for i in ids if i not in found]
    if missing:
        fields = "\n".join(f'p{n}: playerRecordFromClient(id: "{pid}") {{ {PLAYER_FIELDS} }}'
                           for n, pid in enumerate(missing) if re.fullmatch(r"[0-9a-f]{24}", pid))
        query = ("query($leagueId: String!, $locale: String!, $statsRound: Int, $gamesRound: Int, $pcs: String) {"
                 + fields + "}")
        data = gql(query, {"leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
                           "gamesRound": games_round, "pcs": meta["pointCalcSystem"]})
        for rec in data.values():
            if rec:
                found[rec["id"]] = player_view(rec)
    return found


# --------------------------------------------------------------------------- lineups (+ snapshots)

def slot_of(card):
    prefix = (card or "").split("-")[0]
    if prefix in STARTER_PREFIXES:
        return "starter"
    return "bench" if prefix == "b" else "inactive"


def _slot_sort_key(p):
    order = {"starter": 0, "bench": 1, "inactive": 2}
    pos_order = {"c": 0, "f": 1, "g": 2}
    return (order[p["slot"]], pos_order.get(p["card"][:1], 3) if p["slot"] == "starter" else 0, p["card"])


_snapshot_lock = threading.Lock()


def lineups(meta):
    """Current lineup of every team: {teamId: {round, formation, players: [{id, card, slot, captain}]}}.
    Every fetch is also stored, so past rounds stay viewable after the API moves on."""
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
    save_lineup_snapshot(meta, result)
    return result


def save_lineup_snapshot(meta, lineup_by_team):
    by_round = {}
    for team_id, lu in lineup_by_team.items():
        by_round.setdefault(lu["round"], {})[team_id] = {"formation": lu["formation"], "players": lu["players"]}
    path = LINEUPS_DIR / f"{meta['id']}.json"
    with _snapshot_lock:
        store = read_json(path, {"rounds": {}})
        changed = False
        for rnd, teams in by_round.items():
            key = str(rnd)
            locked = rnd < meta["currentRound"] or is_live(meta, rnd)
            old = store["rounds"].get(key)
            if old and old.get("locked") and not locked:
                continue
            if old and old["teams"] == teams and old.get("locked") == locked:
                continue
            store["rounds"][key] = {"savedAt": datetime.now().isoformat(timespec="seconds"),
                                    "locked": locked, "teams": teams}
            changed = True
        if changed:
            write_json(path, store)


def lineup_snapshot(meta, rnd, team_id):
    snap = read_json(LINEUPS_DIR / f"{meta['id']}.json", {"rounds": {}})["rounds"].get(str(rnd))
    if not snap or team_id not in snap["teams"]:
        return None
    return {**snap["teams"][team_id], "savedAt": snap["savedAt"], "locked": snap.get("locked", False),
            "source": snap.get("source")}


def players_left(lineup_players):
    """Starters whose game in the round is still to be finished."""
    return sum(
        1 for p in lineup_players
        if p["slot"] == "starter" and any(not g["completed"] and not g["canceled"] for g in p["games"])
    )


# --------------------------------------------------------------------------- scoring
# Draft-league scoring (verified against official round totals): the five court
# players count x1 and the captain x2, the 6th man (b-1) x1, bench b-2..b-5 x0.5 and
# inactive players nothing. Court formations are centers-forwards-guards.

FORMATIONS = [(1, 2, 2), (2, 1, 2), (2, 2, 1), (1, 3, 1), (1, 1, 3)]
POS_INDEX = {"center": 0, "forward": 1, "guard": 2}


def slot_label(card):
    prefix, _, num = (card or "").partition("-")
    if prefix in STARTER_PREFIXES:
        return prefix.upper()
    if prefix == "b":
        return "6th" if num == "1" else f"B{num}"
    return "Out"


def multiplier(card, captain):
    prefix, _, num = (card or "").partition("-")
    if prefix in STARTER_PREFIXES:
        return 2 if captain else 1
    if prefix == "b":
        return 1 if num == "1" else 0.5
    return 0


def _fits_formation(group):
    """Can these five players cover one of the allowed formations?"""
    options = [[POS_INDEX[p] for p in pl["positions"] if p in POS_INDEX] or [0, 1, 2] for pl in group]
    for combo in itertools.product(*options):
        counts = (combo.count(0), combo.count(1), combo.count(2))
        if counts in FORMATIONS:
            return True
    return False


def optimal_score(lineup_players):
    """Best possible total from the same active players (inactive ones stay out)."""
    active = [p for p in lineup_players if p["slot"] != "inactive"]
    fp = lambda p: p.get("roundPts") or 0  # noqa: E731
    everyone = sum(fp(p) for p in active)
    best = None
    for court in itertools.combinations(active, min(5, len(active))):
        if len(court) == 5 and not _fits_formation(court):
            continue
        on_court = {id(p) for p in court}
        rest = [p for p in active if id(p) not in on_court]
        sixth = max((fp(p) for p in rest), default=0)
        court_sum = sum(fp(p) for p in court)
        captain = max((fp(p) for p in court), default=0)
        # bench counts half, so: half of everyone + the other half for court + 6th, + captain bonus
        total = everyone / 2 + (court_sum + sixth) / 2 + captain
        if best is None or total > best:
            best = total
    return round(best or 0, 2)


def score_lineup(lineup_players):
    """Adds slotLabel / mult / contrib to each player and returns the lineup totals."""
    for p in lineup_players:
        p["slotLabel"] = slot_label(p["card"])
        p["mult"] = multiplier(p["card"], p["captain"])
        p["contrib"] = round((p.get("roundPts") or 0) * p["mult"], 2)
    total = round(sum(p["contrib"] for p in lineup_players), 2)
    optimal = optimal_score(lineup_players)
    return {"total": total, "optimal": optimal, "lost": round(max(optimal - total, 0), 2)}


# --------------------------------------------------------------------------- injuries

STATUS_BY_ID = {
    "1": ("ready", "Pasiruošęs"),
    "2": ("expected", "Tikėtina, kad žais"),
    "3": ("questionable", "Abejojama"),
    "4": ("game-time", "Sprendžiama prieš rungtynes"),
    "5": ("doubtful", "Abejotinas"),
    "6": ("out", "Nežaidžia"),
    "7": ("uncertain", "Neaišku"),
}
HEALTH_LABELS = {key: label for key, label in STATUS_BY_ID.values()}
# Wording used by the BasketNews.com injury report, for when only the API health flag is known.
SITE_LABELS = {"ready": "Ready", "expected": "Expected", "questionable": "Questionable", "game-time": "Game-time",
               "doubtful": "Doubtful", "out": "Out", "uncertain": "Uncertain"}

# Report entries that are about availability rather than health.
NOT_INJURY = re.compile(
    r"coach'?s?'? decision|not included|roster|personal|family|suspen|national team|domestic league|"
    r"rest\b|load management|visa|contract|transfer|left the (team|club)|released|trade|paternity",
    re.I,
)

REASON_LT = {
    "coach's decision": "trenerio sprendimas",
    "coaches decision": "trenerio sprendimas",
    "knee injury": "kelio trauma",
    "ankle injury": "čiurnos trauma",
    "minor injury": "lengva trauma",
    "muscle issue": "raumenų problema",
    "muscle injury": "raumens trauma",
    "undisclosed injury": "neatskleista trauma",
    "illness": "liga",
    "personal reasons": "asmeninės priežastys",
    "rehab after surgery": "reabilitacija po operacijos",
    "quad injury": "keturgalvio raumens trauma",
    "back injury": "nugaros trauma",
    "back problems": "nugaros problemos",
    "calf injury": "blauzdos trauma",
    "hamstring injury": "šlaunies raumens trauma",
    "shoulder injury": "peties trauma",
    "foot injury": "pėdos trauma",
    "hand injury": "plaštakos trauma",
    "wrist injury": "riešo trauma",
    "hip injury": "klubo trauma",
    "groin injury": "kirkšnies trauma",
    "achilles injury": "Achilo sausgyslės trauma",
    "concussion": "smegenų sukrėtimas",
    "acl injury": "kelio kryžminio raiščio trauma",
    "left knee acl injury": "kairio kelio kryžminio raiščio trauma",
    "right knee acl injury": "dešinio kelio kryžminio raiščio trauma",
    "minor undisclosed injury": "lengva neatskleista trauma",
    "continues rehab after achilles injury": "tęsia reabilitaciją po Achilo sausgyslės traumos",
    "ankle sprain": "čiurnos patempimas",
    "tendinitis": "sausgyslės uždegimas",
    "hamstring strain": "šlaunies raumens patempimas",
    "calf strain": "blauzdos raumens patempimas",
    "not included in the euroleague roster": "neįtrauktas į Eurolygos sudėtį",
}

DNP_RE = re.compile(r"DNP in Round\s*(\d+)\s*(?:\(([^)]*)\))?", re.I)


def reason_lt(text):
    key = (text or "").strip().rstrip(".").strip().lower()
    return REASON_LT.get(key)


def is_injury(text):
    """Health reason unless the report clearly gives another one. Uses the reason in
    'DNP in Round N (...)' when present, so 'coach's decision. Played in the domestic
    league' is not counted as an injury."""
    rnd, reason = dnp_reason(text)
    t = reason if rnd and reason else (text or "")
    return bool(t.strip()) and not NOT_INJURY.search(t)


class _InjuryTableParser(HTMLParser):
    """Reads BasketNews' injury_reports widget (same markup on .com and .lt)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and a.get("id") == "injury-reports-table":
            self.inside = True
        if not self.inside:
            return
        if tag == "tr":
            self._row = {"cls": a.get("class") or "", "cells": []}
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "href": None, "status": None}
        elif self._cell is not None:
            if tag == "a" and not self._cell["href"]:
                self._cell["href"] = a.get("href")
            m = re.search(r"player-status__id-(\d+)", a.get("class") or "")
            if m:
                self._cell["status"] = m.group(1)

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self.inside = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _clean(text):
    return " ".join((text or "").split())


def parse_injury_report(page):
    parser = _InjuryTableParser()
    parser.feed(page)
    entries, club = [], None
    for row in parser.rows:
        cells = row["cells"]
        if "team-row" in row["cls"]:
            club = _clean(cells[0]["text"]) if cells else club
            continue
        if len(cells) < 5:
            continue
        m = re.search(r"/(\d+)-[^/]*\.html", cells[1]["href"] or "")
        key, label = STATUS_BY_ID.get(cells[2]["status"], ("other", _clean(cells[2]["text"])))
        entries.append({
            "bnId": m.group(1) if m else None,
            "name": _clean(cells[1]["text"]),
            "club": club,
            "pos": _clean(cells[0]["text"]),
            "status": key,
            "statusLabel": label,
            "siteLabel": _clean(cells[2]["text"]) or label,  # the report's own wording ("Out", "Game-time", ...)
            "return": _clean(cells[3]["text"]),
            "comment": _clean(cells[4]["text"]),
        })
    return entries


_injury_cache = {}
_injury_lock = threading.Lock()


def injury_report(meta):
    """Current injury report of the league's competition: {bnPlayerId: entry}."""
    url = meta.get("injuryReportUrl")
    if not url:
        return {}
    with _injury_lock:
        hit = _injury_cache.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en,lt"})
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                page = resp.read().decode("utf-8", errors="replace")
            entries = parse_injury_report(page)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Traumų sąrašas nepasiekiamas ({url}): {exc}")
            if hit:
                return hit[1]
            return {}
        by_player = {e["bnId"]: e for e in entries if e["bnId"]}
        update_injury_log(entries)
        _injury_cache[url] = (time.time() + INJURY_TTL, by_player)
        return by_player


_log_lock = threading.Lock()


def update_injury_log(entries):
    """Turn daily report snapshots into per-player episodes (start, end, status changes)."""
    today = date.today().isoformat()
    with _log_lock:
        log = read_json(INJURY_LOG_FILE, {"players": {}})
        known = log["players"]
        seen = set()
        for e in entries:
            if not e["bnId"]:
                continue
            seen.add(e["bnId"])
            rec = known.setdefault(e["bnId"], {"episodes": []})
            rec["name"], rec["club"] = e["name"], e["club"]
            episodes = rec["episodes"]
            open_ep = episodes[-1] if episodes and episodes[-1]["end"] is None else None
            update = {"date": today, "status": e["status"], "return": e["return"], "comment": e["comment"]}
            if e["status"] == "ready":
                if open_ep:
                    open_ep["end"] = today
                    _add_update(open_ep, update)
                elif not episodes and is_injury(e["comment"]):
                    # First sighting of someone already recovered: keep the finished injury.
                    episodes.append({"start": today, "end": today, "lastSeen": today, "updates": [update]})
                continue
            if open_ep:
                open_ep["lastSeen"] = today
                _add_update(open_ep, update)
            else:
                episodes.append({"start": today, "end": None, "lastSeen": today, "updates": [update]})
        for bn_id, rec in known.items():
            episodes = rec["episodes"]
            if bn_id in seen or not episodes or episodes[-1]["end"] is not None:
                continue
            ep = episodes[-1]
            gap = (date.fromisoformat(today) - date.fromisoformat(ep["lastSeen"])).days
            ep["end"] = today if gap <= 1 else ep["lastSeen"]
            _add_update(ep, {"date": ep["end"], "status": "ready", "return": "",
                             "comment": "Išbrauktas iš traumų sąrašo"})
        log["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        write_json(INJURY_LOG_FILE, log)


def _add_update(episode, update):
    last = episode["updates"][-1] if episode["updates"] else {}
    if any(last.get(k) != update[k] for k in ("status", "return", "comment")):
        episode["updates"].append(update)


def injury_view(entry, health=None):
    """Compact current status for lists: None when the player is fine."""
    if entry and entry["status"] != "ready":
        return {
            "status": entry["status"], "label": entry.get("siteLabel") or entry["statusLabel"],
            "labelLt": entry["statusLabel"],
            "return": return_lt(entry["return"]), "comment": entry["comment"],
            "reasonLt": reason_lt(dnp_reason(entry["comment"])[1]),
        }
    if health and health != "ready":
        return {"status": health, "label": SITE_LABELS.get(health, health), "labelLt": HEALTH_LABELS.get(health, health),
                "return": "", "comment": "", "reasonLt": None}
    return None


def return_lt(text):
    t = _clean(text)
    m = re.fullmatch(r"Round\s*(\d+)(?:\s*[-–]\s*(\d+))?", t, re.I)
    if m:
        return f"{m.group(1)}–{m.group(2)} turas" if m.group(2) else f"{m.group(1)} turas"
    return {"indefinitely": "neribotam laikui", "season": "sezono pabaiga",
            "end of season": "sezono pabaiga"}.get(t.lower(), t)


def dnp_reason(comment):
    """('DNP in Round 1 (knee injury)') -> (1, 'knee injury'); otherwise (None, comment)."""
    m = DNP_RE.search(comment or "")
    if m:
        return int(m.group(1)), _clean(m.group(2) or "")
    return None, _clean(comment).rstrip(".")


# --------------------------------------------------------------------------- payloads

def standings_payload(fid, rnd=None):
    meta = league_meta(fid)
    shown, rows = standings(meta, rnd)
    live = is_live(meta, shown)
    if live:
        lu = lineups(meta)
        pmap = players(meta, shown, shown)
        for row in rows:
            team_lu = lu.get(row["team"]["id"])
            row["left"] = players_left(
                [{**p, "games": (pmap.get(p["id"]) or {}).get("games", [])} for p in team_lu["players"]]
            ) if team_lu else 0
    else:
        for row in rows:
            row["left"] = 0
    entry = config_entry(fid) or {}
    return {
        "league": meta,
        "round": shown,
        "live": live,
        "rows": rows,
        "myTeamId": entry.get("myTeamId"),
        "hasTies": any(r.get("ties") for r in rows),
    }


def rounds_payload(fid, rnd=None):
    meta = league_meta(fid)
    if meta["format"] == "head_to_head":
        rnd = meta["currentRound"] if rnd is None else rnd
        return {"league": meta, "round": rnd, "live": is_live(meta, rnd), "matchups": schedule(meta, rnd)}
    rnd = meta["latestRound"] if rnd is None else rnd
    rows = fetch_standings_round(meta, rnd)
    rows.sort(key=lambda r: -r["pointsRound"])
    return {"league": meta, "round": rnd, "live": is_live(meta, rnd), "rows": rows}


def team_round_result(meta, team_id, rnd):
    state = round_state(meta, rnd)
    if meta["format"] == "head_to_head":
        m = next((m for m in schedule(meta, rnd)
                  if team_id in ((m["team1"] or {}).get("id"), (m["team2"] or {}).get("id"))), None)
        if not m:
            return {"state": state}
        mine_first = (m["team1"] or {}).get("id") == team_id
        me, opp = (m["score1"], m["score2"]) if mine_first else (m["score2"], m["score1"])
        result = None
        if state == "finished":
            result = "W" if me > opp else "L" if me < opp else "T"
        return {"state": state, "points": me, "opponent": m["team2"] if mine_first else m["team1"],
                "opponentPoints": opp, "result": result}
    if state == "upcoming":
        return {"state": state}
    row = next((r for r in fetch_standings_round(meta, rnd) if r["team"]["id"] == team_id), None)
    if not row:
        return {"state": state}
    return {"state": state, "points": row["pointsRound"], "roundPosition": row.get("roundPosition"),
            "position": row["position"]}


def team_history(meta, team_id, last_round):
    """Per-round results of one team, oldest first: score, league average, and the
    table position / record after the round."""
    if last_round is None:
        return []
    rounds = list(range(meta["firstRound"], last_round + 1))
    if meta["format"] == "head_to_head" and meta["currentRound"] not in rounds:
        rounds.append(meta["currentRound"])

    def one(r):
        res = team_round_result(meta, team_id, r)
        if res["state"] != "upcoming":
            table = fetch_standings_round(meta, r)
            row = next((x for x in table if x["team"]["id"] == team_id), None)
            if row:
                res.update(position=row["position"], pointsTotal=row["pointsTotal"],
                           wins=row.get("wins"), losses=row.get("losses"))
            if table:
                res["leagueAvg"] = round(sum(x["pointsRound"] for x in table) / len(table), 2)
        return r, res

    return [{"round": r, **res} for r, res in POOL.map(one, rounds) if len(res) > 1 or res["state"] == "upcoming"]


def team_payload(fid, team_id, rnd=None):
    meta = league_meta(fid)
    current = meta["currentRound"]
    rnd = current if rnd is None else max(meta["firstRound"], min(rnd, current))
    shown, rows = standings(meta)
    row = next((r for r in rows if r["team"]["id"] == team_id), None)
    if not row:
        raise NotFound("Komanda šioje lygoje nerasta")

    current_lineups = lineups(meta)
    now_lineup = current_lineups.get(team_id)
    lineup_note = None
    if now_lineup and now_lineup["round"] == rnd:
        lineup, source = now_lineup, "current"
    else:
        snap = lineup_snapshot(meta, rnd, team_id)
        if snap:
            lineup, source = snap, "snapshot"
            if snap.get("source") == "import":
                lineup_note = None
            elif not snap["locked"]:
                lineup_note = "Sudėtis išsaugota prieš turui prasidedant – vėlesni pakeitimai galėjo būti nematyti."
        else:
            # The API only exposes the current lineup; for earlier rounds show today's roster.
            lineup, source = now_lineup, "roster"
            lineup_note = ("Šio turo sudėtis nebuvo išsaugota (programa dar neveikė), todėl rodomas dabartinis "
                           "komandos sąrašas su to turo taškais. Tikslus to turo penketas ir kapitonas nežinomi.")

    lineup_players = []
    formation = None
    if lineup:
        formation = lineup.get("formation")
        ids = [p["id"] for p in lineup["players"]]
        pmap = players_by_ids(meta, ids, rnd, rnd)
        report = injury_report(meta)
        for p in lineup["players"]:
            info = pmap.get(p["id"])
            if not info:
                continue
            entry = {**info, "card": p["card"], "slot": p["slot"], "captain": p["captain"],
                     "injury": injury_view(report.get(info["bnId"]), info["health"])}
            if source == "roster":
                entry.update(slot="roster", captain=False)
            lineup_players.append(entry)
        if source == "roster":
            lineup_players.sort(key=lambda x: -(x["roundPts"] or -1))
        else:
            lineup_players.sort(key=_slot_sort_key)

    result = team_round_result(meta, team_id, rnd)
    after = None
    if result["state"] != "upcoming":
        after = next((r for r in fetch_standings_round(meta, rnd) if r["team"]["id"] == team_id), None)
    if result["state"] == "live":
        result["left"] = players_left(lineup_players)

    scoring = None
    if lineup_players and source != "roster":
        scoring = score_lineup(lineup_players)
        if result["state"] == "upcoming":
            scoring = None  # slot labels and multipliers still apply, points don't exist yet

    entry = config_entry(fid) or {}
    return {
        "league": meta,
        "team": row["team"],
        "standing": row,
        "standingRound": shown,
        "round": rnd,
        "roundState": result["state"],
        "result": result,
        "after": after,
        "lineup": {"source": source if lineup else None, "note": lineup_note, "formation": formation,
                   "players": lineup_players, "scoring": scoring},
        "history": team_history(meta, team_id, shown),
        "isMine": entry.get("myTeamId") == team_id,
    }


def free_agents_payload(fid):
    meta = league_meta(fid)
    owned = {p["id"] for lu in lineups(meta).values() for p in lu["players"]}
    stats_round = meta["latestRound"]
    pmap = players(meta, stats_round, meta["currentRound"])
    report = injury_report(meta)
    agents = []
    for p in pmap.values():
        if p["id"] in owned:
            continue
        agents.append({**p, "injury": injury_view(report.get(p["bnId"]), p["health"])})
    agents.sort(key=lambda p: (p["avgPts"] is None, -(p["avgPts"] or 0), p["name"]))
    return {
        "league": meta,
        "statsRound": stats_round,
        "players": agents,
        "totalPlayers": len(pmap),
        "rosteredPlayers": len(owned),
        "injuryReportUrl": meta["injuryReportUrl"],
    }


def _player_rounds_query(rounds):
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


def _day(iso):
    return iso[:10] if iso else None


def build_injury_history(bn_id, game_log):
    """Season injury story of one player from the recorded episodes and the rounds he missed."""
    rec = read_json(INJURY_LOG_FILE, {"players": {}})["players"].get(bn_id) or {"episodes": []}
    round_dates = {g["round"]: g["date"] for g in game_log if g.get("date")}
    missed = [g for g in game_log if g["status"] == "dnp"]
    today = date.today()
    episodes = []
    for ep in rec["episodes"]:
        comments = [u["comment"] for u in ep["updates"] if u.get("comment")]
        start = ep["start"]
        for c in comments:
            for m in DNP_RE.finditer(c):
                d = round_dates.get(int(m.group(1)) - 1)
                if d and d < start:
                    start = d
        # Rounds missed back-to-back right before the player showed up in the report
        # belong to the same absence.
        for g in sorted((g for g in game_log if g.get("date") and g["date"] < start),
                        key=lambda g: g["date"], reverse=True):
            if g["status"] == "played":
                break
            if g["status"] == "dnp":
                start = g["date"]
        end = ep["end"]
        last_real = next((u for u in reversed(ep["updates"]) if u["status"] != "ready"), ep["updates"][0])
        rnd_hint, reason = dnp_reason(last_real["comment"])
        kind = "injury" if any(is_injury(c) for c in comments) else "other"
        until = date.fromisoformat(end) if end else today
        ep_missed = [g["round"] for g in missed
                     if g.get("date") and start <= g["date"] <= (end or today.isoformat())]
        episodes.append({
            "kind": kind,
            "reason": reason,
            "reasonLt": reason_lt(reason),
            "start": start,
            "end": end,
            "days": max((until - date.fromisoformat(start)).days, 0),
            "ongoing": end is None,
            "status": last_real["status"],
            "statusLabel": HEALTH_LABELS.get(last_real["status"], last_real["status"]),
            "return": return_lt(last_real.get("return")),
            "missedRounds": ep_missed,
            "updates": [
                {"date": u["date"], "statusLabel": HEALTH_LABELS.get(u["status"], u["status"]),
                 "return": return_lt(u.get("return")), "comment": u.get("comment")}
                for u in ep["updates"]
            ],
        })
    episodes.reverse()
    return {"episodes": episodes, "missed": [g["round"] for g in missed],
            "summary": injury_summary(episodes, missed, game_log)}


def _plural_rounds(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} turą"
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return f"{n} turus"
    return f"{n} turų"


def injury_summary(episodes, missed, game_log):
    played = [g for g in game_log if g["status"] == "played"]
    team_games = [g for g in game_log if g["status"] in ("played", "dnp")]
    parts = []
    injuries = [e for e in episodes if e["kind"] == "injury"]
    if not missed and not episodes:
        if team_games:
            return f"Šį sezoną nepraleido nė vieno turo – sužaidė {len(played)} iš {len(team_games)}."
        return "Šį sezoną dar nežaidė ir traumų sąraše nebuvo."
    if missed:
        rounds = ", ".join(str(g["round"] + 1) for g in missed)
        parts.append(f"Šį sezoną praleido {_plural_rounds(len(missed))} (turai: {rounds}).")
    else:
        parts.append("Šį sezoną nepraleido nė vieno turo.")
    for e in injuries:
        what = e["reasonLt"] or e["reason"] or "trauma"
        span = f"nuo {e['start'][5:]}" + (f" iki {e['end'][5:]}" if e["end"] else ", tęsiasi")
        parts.append(f"{what[:1].upper() + what[1:]}: {span} ({e['days']} d.).")
    others = [e for e in episodes if e["kind"] == "other"]
    if others and not injuries:
        reason = others[0]["reasonLt"] or others[0]["reason"]
        parts.append(f"Traumų nebuvo{f' – priežastis: {reason}' if reason and missed else ''}.")
    return " ".join(parts)


def player_payload(fid, player_id):
    meta = league_meta(fid)
    last = meta["latestRound"]
    rounds = list(range(meta["firstRound"], last + 1))
    data = gql(_player_rounds_query(rounds), {
        "id": player_id, "leagueId": meta["leagueId"], "locale": LOCALE, "pcs": meta["pointCalcSystem"],
        "statsRound": last, "gamesRound": meta["currentRound"],
    })["playerRecordFromClient"]
    if not data:
        raise NotFound("Žaidėjas nerastas")
    info = player_view(data)

    game_log = []
    for r in rounds:
        club = ((data.get(f"r{r}_tm") or {}).get("team")) or {}
        games = [game_view(g, club["id"]) for g in club.get("games") or []] if club else []
        st = data.get(f"r{r}_st")
        played = bool(st and st.get("s_gp"))
        row = {"round": r, "games": games, "date": _day(games[0]["at"]) if games else None,
               "fp": data.get(f"r{r}_pts"), "club": club.get("abbreviation")}
        if played:
            row.update(status="played", line=stat_line(st))
        elif not games:
            row["status"] = "no-game"
        elif all(g["completed"] or g["canceled"] for g in games):
            row["status"] = "dnp"
        else:
            row["status"] = "pending"
        game_log.append(row)

    report = injury_report(meta)
    current = report.get(info["bnId"])
    history = build_injury_history(info["bnId"], game_log) if info["bnId"] else {"episodes": [], "missed": [], "summary": ""}
    # Explain each missed round with the report's own words where possible.
    reasons = {}
    for ep in history["episodes"]:
        for u in ep["updates"]:
            rnd_no, why = dnp_reason(u["comment"])
            if rnd_no:
                reasons[rnd_no - 1] = reason_lt(why) or why
        for r in ep["missedRounds"]:
            reasons.setdefault(r, ep["reasonLt"] or ep["reason"])
    if current:  # recovered players keep a "DNP in Round N (...)" note in the report
        rnd_no, why = dnp_reason(current["comment"])
        if rnd_no:
            reasons.setdefault(rnd_no - 1, reason_lt(why) or why or current["comment"])
    for row in game_log:
        if row["status"] == "dnp":
            row["reason"] = reasons.get(row["round"])

    owner = None
    for team_id, lu in lineups(meta).items():
        if any(p["id"] == player_id for p in lu["players"]):
            owner = next((r["team"] for r in standings(meta)[1] if r["team"]["id"] == team_id), {"id": team_id})
            break

    return {
        "league": {"id": meta["id"], "title": meta["title"], "currentRound": meta["currentRound"]},
        "player": info,
        "owner": owner,
        "injury": {
            "current": injury_view(current, info["health"]),
            "reportUrl": meta["injuryReportUrl"],
            **history,
        },
        "gameLog": list(reversed(game_log)),
        "nextGames": info["games"],
    }


# --------------------------------------------------------------------------- season records & awards

def num(v):
    if v is None:
        return "–"
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def teams_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} komanda"
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return f"{n} komandos"
    return f"{n} komandų"


def award(title, name, value, sub="", icon="", team_id=None, player_id=None):
    return {"title": title, "icon": icon, "name": name, "value": value, "sub": sub,
            "teamId": team_id, "playerId": player_id}


def no_award(title, sub, icon=""):
    return award(title, "Dar nėra", "", sub, icon)


def round_breakdown(meta, rnd):
    """Scores, matchups and (when a lineup snapshot exists) scored lineups of one finished round."""
    table = fetch_standings_round(meta, rnd)
    teams = {r["team"]["id"]: r["team"] for r in table}
    scores = {r["team"]["id"]: r["pointsRound"] for r in table}
    games = []
    if meta["format"] == "head_to_head":
        for m in schedule(meta, rnd):
            if not (m["team1"] and m["team2"]):
                continue
            a, b = (m["team1"], m["score1"]), (m["team2"], m["score2"])
            (win, ws), (lose, ls) = (a, b) if a[1] >= b[1] else (b, a)
            games.append({"winner": win, "loser": lose, "ws": ws, "ls": ls, "margin": round(ws - ls, 2),
                          "tie": ws == ls})
    lineups_by_team = {}
    snap = read_json(LINEUPS_DIR / f"{meta['id']}.json", {"rounds": {}})["rounds"].get(str(rnd))
    if snap:
        ids = [p["id"] for lu in snap["teams"].values() for p in lu["players"]]
        pmap = players_by_ids(meta, ids, rnd, rnd)
        for tid, lu in snap["teams"].items():
            plist = [{**pmap[p["id"]], "card": p["card"], "slot": p["slot"], "captain": p["captain"]}
                     for p in lu["players"] if p["id"] in pmap]
            lineups_by_team[tid] = {"players": plist, **score_lineup(plist)}
    return {"round": rnd, "teams": teams, "scores": scores, "games": games, "lineups": lineups_by_team}


def _streaks(results):
    """Longest win / loss streaks and the current streak from a list like ['W','L','W']."""
    best = {"W": 0, "L": 0}
    run_kind, run = None, 0
    for res in results:
        if res == run_kind:
            run += 1
        else:
            run_kind, run = res, 1
        if res in best:
            best[res] = max(best[res], run)
    return best["W"], best["L"], (run_kind, run)


def round_awards(meta, bd):
    teams, scores, rnd = bd["teams"], bd["scores"], bd["round"]
    cards = []
    if scores:
        hi = max(scores, key=scores.get)
        lo = min(scores, key=scores.get)
        cards.append(award("Daugiausiai surinko", teams[hi]["title"], num(scores[hi]), team_id=hi))
        cards.append(award("Mažiausiai surinko", teams[lo]["title"], num(scores[lo]), team_id=lo))
    decided = [g for g in bd["games"] if not g["tie"]]
    if decided:
        big = max(decided, key=lambda g: g["margin"])
        small = min(decided, key=lambda g: g["margin"])
        unlucky = max(decided, key=lambda g: g["ls"])
        lucky = min(decided, key=lambda g: g["ws"])
        cards += [
            award("Didžiausia pergalė", big["winner"]["title"], f"+{num(big['margin'])}",
                  f"{num(big['ws'])} : {num(big['ls'])} prieš {big['loser']['title']}", team_id=big["winner"]["id"]),
            award("Mažiausias skirtumas", small["winner"]["title"], f"+{num(small['margin'])}",
                  f"{num(small['ws'])} : {num(small['ls'])} prieš {small['loser']['title']}", team_id=small["winner"]["id"]),
            award("Nelaimingiausias pralaimėjimas", unlucky["loser"]["title"], num(unlucky["ls"]),
                  f"Daugiausia taškų pralaimėjus (prieš {unlucky['winner']['title']})", team_id=unlucky["loser"]["id"]),
            award("Laimingiausia pergalė", lucky["winner"]["title"], num(lucky["ws"]),
                  f"Mažiausiai taškų laimėjus (prieš {lucky['loser']['title']})", team_id=lucky["winner"]["id"]),
        ]
    lus = bd["lineups"]
    if not lus:
        cards.append(award("Sudėčių apdovanojimai", "Nėra duomenų", "",
                           f"{rnd + 1} turo sudėtys nebuvo išsaugotos, todėl MVP, kapitonų ir prarastų taškų "
                           "apdovanojimų apskaičiuoti negalima."))
        return cards
    active = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["slot"] != "inactive"]
    mvp_tid, mvp = max(active, key=lambda x: x[1].get("roundPts") or -999)
    caps = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["captain"]]
    lost_tid = max(lus, key=lambda t: lus[t]["lost"])
    cards.append(award("Turo MVP žaidėjas", mvp["name"], num(mvp.get("roundPts")),
                       teams.get(mvp_tid, {}).get("title", ""), player_id=mvp["id"]))
    if caps:
        best_tid, best_cap = max(caps, key=lambda x: x[1]["contrib"])
        worst_tid, worst_cap = min(caps, key=lambda x: x[1]["contrib"])
        cards.append(award("Geriausias kapitonas", best_cap["name"], num(best_cap["contrib"]),
                           teams.get(best_tid, {}).get("title", ""), player_id=best_cap["id"]))
        cards.append(award("Nesėkmingiausias kapitonas", worst_cap["name"], num(worst_cap["contrib"]),
                           teams.get(worst_tid, {}).get("title", ""), player_id=worst_cap["id"]))
    cards.append(award("Daugiausiai prarado dėl sudėties", teams.get(lost_tid, {}).get("title", ""),
                       f"−{num(lus[lost_tid]['lost'])}" if lus[lost_tid]["lost"] else "0",
                       f"Su optimalia sudėtimi būtų {num(lus[lost_tid]['optimal'])}", team_id=lost_tid))
    return cards


def season_oscars(meta, breakdowns, team_names):
    with_lineups = [bd for bd in breakdowns if bd["lineups"]]
    name = lambda tid: team_names.get(tid, "")  # noqa: E731
    cards = []
    if with_lineups:
        cap_total, lost_total, contrib = {}, {}, {}
        best_pick = None
        for bd in with_lineups:
            for tid, lu in bd["lineups"].items():
                lost_total[tid] = lost_total.get(tid, 0) + lu["lost"]
                for p in lu["players"]:
                    if p["captain"]:
                        cap_total[tid] = cap_total.get(tid, 0) + p["contrib"]
                        if best_pick is None or p["contrib"] > best_pick[2]["contrib"]:
                            best_pick = (bd["round"], tid, p)
                    if p["contrib"]:
                        key = (p["id"], tid)
                        pname, total, rounds = contrib.get(key, (p["name"], 0, set()))
                        rounds.add(bd["round"])
                        contrib[key] = (pname, total + p["contrib"], rounds)
        if cap_total:
            king = max(cap_total, key=cap_total.get)
            flop = min(cap_total, key=cap_total.get)
            cards.append(award("Kapitonų karalius", name(king), num(cap_total[king]),
                               "daugiausia kapitonų taškų per sezoną", "🏆", team_id=king))
        if best_pick:
            r, tid, p = best_pick
            cards.append(award("Geriausias kapitono pasirinkimas", p["name"], num(p["contrib"]),
                               f"{name(tid)}, {r + 1} turas", "🎯", player_id=p["id"]))
        if cap_total:
            cards.append(award("Kapitonų nesėkmė", name(flop), num(cap_total[flop]),
                               "mažiausiai kapitonų taškų", "🙈", team_id=flop))
        bench = max(lost_total, key=lost_total.get)
        sharp = min(lost_total, key=lost_total.get)
        cards.append(award("Suolo karalius", name(bench), f"−{num(lost_total[bench])}" if lost_total[bench] else "0",
                           "daugiausia taškų paliko ant suolo", "🪑", team_id=bench))
        cards.append(award("Tiksliausias treneris", name(sharp), num(lost_total[sharp]),
                           "mažiausiai prarado dėl sudėties", "🧠", team_id=sharp))
        if contrib:
            (pid, tid), (pname, total, rounds) = max(contrib.items(), key=lambda kv: kv[1][1])
            cards.append(award("Sezono MVP", pname, num(total), f"{name(tid)} · {len(rounds)} tur.", "⭐",
                               player_id=pid))
        cards.append(best_transfer(with_lineups, team_names))
    else:
        cards.append(award("Sudėčių apdovanojimai", "Nėra duomenų", "", "Nėra išsaugotų turų sudėčių."))
    best_round = max(((bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]), default=None)
    if best_round:
        pts, tid, r = best_round
        cards.append(award("Sezono turas", name(tid), num(pts), f"{r + 1} turas", "🔥", team_id=tid))
    return cards


def best_transfer(with_lineups, team_names):
    """Player added after the first recorded round who has given his new team the most points."""
    initial = {tid: {p["id"] for p in lu["players"]} for tid, lu in with_lineups[0]["lineups"].items()}
    gained = {}
    for bd in with_lineups[1:]:
        for tid, lu in bd["lineups"].items():
            for p in lu["players"]:
                if p["id"] in initial.get(tid, set()):
                    continue
                key = (p["id"], tid)
                pname, total = gained.get(key, (p["name"], 0))
                gained[key] = (pname, total + p["contrib"])
    if not gained:
        return no_award("Sezono sandoris", "atsiras po pirmųjų perėjimų", "🤝")
    (pid, tid), (pname, total) = max(gained.items(), key=lambda kv: kv[1][1])
    return award("Sezono sandoris", pname, num(total), f"{team_names.get(tid, '')} · taškai nuo įsigijimo", "🤝",
                 player_id=pid)


def season_records(meta, breakdowns, team_names, streaks, totals):
    cards = []
    rows = [(bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]]
    opponent = {}
    for bd in breakdowns:
        for g in bd["games"]:
            opponent[(g["winner"]["id"], bd["round"])] = g["loser"]["title"]
            opponent[(g["loser"]["id"], bd["round"])] = g["winner"]["title"]
    vs = lambda t, r: f", prieš {opponent[(t, r)]}" if (t, r) in opponent else ""  # noqa: E731
    if rows:
        hi, lo = max(rows), min(rows)
        cards.append(award("Daugiausia taškų per turą", team_names.get(hi[1], ""), num(hi[0]),
                           f"{hi[2] + 1} turas{vs(hi[1], hi[2])}", team_id=hi[1]))
        cards.append(award("Mažiausiai taškų per turą", team_names.get(lo[1], ""), num(lo[0]),
                           f"{lo[2] + 1} turas{vs(lo[1], lo[2])}", team_id=lo[1]))
    games = [(g, bd["round"]) for bd in breakdowns for g in bd["games"] if not g["tie"]]
    if games:
        big = max(games, key=lambda x: x[0]["margin"])
        small = min(games, key=lambda x: x[0]["margin"])
        unlucky = max(games, key=lambda x: x[0]["ls"])
        lucky = min(games, key=lambda x: x[0]["ws"])
        cards += [
            award("Didžiausia pergalė", big[0]["winner"]["title"], f"+{num(big[0]['margin'])}",
                  f"{num(big[0]['ws'])} : {num(big[0]['ls'])} prieš {big[0]['loser']['title']}, {big[1] + 1} turas",
                  team_id=big[0]["winner"]["id"]),
            award("Mažiausias skirtumas", small[0]["winner"]["title"], f"+{num(small[0]['margin'])}",
                  f"{num(small[0]['ws'])} : {num(small[0]['ls'])} prieš {small[0]['loser']['title']}, {small[1] + 1} turas",
                  team_id=small[0]["winner"]["id"]),
            award("Nelaimingiausias pralaimėjimas", unlucky[0]["loser"]["title"], num(unlucky[0]["ls"]),
                  f"Pralaimėjo {unlucky[0]['winner']['title']}, {unlucky[1] + 1} turas", team_id=unlucky[0]["loser"]["id"]),
            award("Laimingiausia pergalė", lucky[0]["winner"]["title"], num(lucky[0]["ws"]),
                  f"Laimėjo prieš {lucky[0]['loser']['title']}, {lucky[1] + 1} turas", team_id=lucky[0]["winner"]["id"]),
        ]
        for idx, title, sub in ((0, "Ilgiausia pergalių serija", "pergalės iš eilės"),
                                (1, "Ilgiausia pralaimėjimų serija", "pralaimėjimai iš eilės")):
            longest = {t: s[idx] for t, s in streaks.items()}
            top = max(longest.values(), default=0)
            holders = [t for t, v in longest.items() if v == top and top > 0]
            if not holders:
                cards.append(no_award(title, sub))
            elif len(holders) == 1:
                cards.append(award(title, team_names.get(holders[0], ""), str(top), sub, team_id=holders[0]))
            else:
                cards.append(award(title, teams_word(len(holders)), str(top), f"{sub} (žr. lentelę)"))
    if totals:
        played = len(breakdowns)
        most = max(totals, key=totals.get)
        least = min(totals, key=totals.get)
        cards.append(award("Daugiausia taškų per sezoną", team_names.get(most, ""), num(totals[most]),
                           f"Vidurkis {num(totals[most] / played)} per turą", team_id=most))
        cards.append(award("Mažiausiai taškų per sezoną", team_names.get(least, ""), num(totals[least]),
                           f"Vidurkis {num(totals[least] / played)} per turą", team_id=least))
    return cards


def records_payload(fid, rnd=None):
    meta = league_meta(fid)
    finished = list(range(meta["firstRound"], meta["currentRound"]))
    _, table = standings(meta)
    team_names = {r["team"]["id"]: r["team"]["title"] for r in table}
    base = {"league": meta, "finished": finished, "round": None, "roundAwards": [], "oscars": [],
            "records": [], "form": [], "missingLineups": []}
    if not finished:
        return base
    rnd = finished[-1] if rnd not in finished else rnd
    breakdowns = list(POOL.map(lambda r: round_breakdown(meta, r), finished))
    h2h = meta["format"] == "head_to_head"

    results, totals, per_round = {}, {}, {}
    for bd in breakdowns:
        for tid, pts in bd["scores"].items():
            totals[tid] = totals.get(tid, 0) + pts
            per_round.setdefault(tid, []).append({"round": bd["round"], "points": pts})
        for g in bd["games"]:
            for team, res, opp, mine, theirs in (
                    (g["winner"], "T" if g["tie"] else "W", g["loser"], g["ws"], g["ls"]),
                    (g["loser"], "T" if g["tie"] else "L", g["winner"], g["ls"], g["ws"])):
                results.setdefault(team["id"], []).append(
                    {"round": bd["round"], "result": res, "opponent": opp["title"], "points": mine, "against": theirs})
    streaks = {t: _streaks([x["result"] for x in rs]) for t, rs in results.items()}

    form = []
    for row in table:
        tid = row["team"]["id"]
        rounds = per_round.get(tid, [])
        pts = [x["points"] for x in rounds]
        entry = {"team": row["team"], "position": row["position"],
                 "avg": round(sum(pts) / len(pts), 2) if pts else None,
                 "best": max(pts) if pts else None, "worst": min(pts) if pts else None}
        if h2h:
            longest_w, longest_l, (kind, run) = streaks.get(tid, (0, 0, (None, 0)))
            entry.update(last=results.get(tid, [])[-5:], streak={"kind": kind, "length": run},
                         longestWin=longest_w, longestLoss=longest_l)
        else:
            entry["last"] = rounds[-5:]
        form.append(entry)

    selected = next(bd for bd in breakdowns if bd["round"] == rnd)
    return {
        **base,
        "round": rnd,
        "roundAwards": round_awards(meta, selected),
        "oscars": season_oscars(meta, breakdowns, team_names),
        "records": season_records(meta, breakdowns, team_names, streaks, totals),
        "form": form,
        "missingLineups": [bd["round"] for bd in breakdowns if not bd["lineups"]],
    }


def leagues_payload():
    def card(entry):
        try:
            meta = league_meta(entry["id"])
            shown, rows = standings(meta)
            mine = next((r for r in rows if r["team"]["id"] == entry.get("myTeamId")), None)
            return {"league": meta, "round": shown, "leader": rows[0] if rows else None,
                    "mine": mine, "teams": len(rows)}
        except (UpstreamError, NotFound) as exc:
            return {"league": {"id": entry["id"], "title": entry.get("title") or entry["id"]}, "error": str(exc)}
    return {"leagues": list(POOL.map(card, load_config()))}


LEAGUE_ID_RE = re.compile(r"([0-9a-f]{24})")


def add_league(text):
    m = LEAGUE_ID_RE.search(text or "")
    if not m:
        raise ValueError("Nuorodoje nerastas lygos ID")
    fid = m.group(1)
    meta = league_meta(fid)  # validates the league exists
    with _config_lock:
        entries = load_config()
        if any(e["id"] == fid for e in entries):
            raise ValueError("Ši lyga jau pridėta")
        entries.append({"id": fid, "title": meta["title"]})
        write_json(LEAGUES_FILE, entries)
    return meta


def remove_league(fid):
    with _config_lock:
        write_json(LEAGUES_FILE, [e for e in load_config() if e["id"] != fid])


def set_my_team(fid, team_id):
    with _config_lock:
        entries = load_config()
        for e in entries:
            if e["id"] == fid:
                if team_id:
                    e["myTeamId"] = team_id
                else:
                    e.pop("myTeamId", None)
                break
        else:
            raise NotFound("Lyga nesekama")
        write_json(LEAGUES_FILE, entries)


# --------------------------------------------------------------------------- background

def refresh_tracked_leagues():
    """Keep lineup snapshots and the injury log current for every tracked league."""
    for entry in load_config():
        try:
            meta = league_meta(entry["id"])
            lineups(meta)
            injury_report(meta)
        except (UpstreamError, NotFound) as exc:
            print(f"Nepavyko atnaujinti lygos {entry['id']}: {exc}")


def background_loop():
    while True:
        try:
            refresh_tracked_leagues()
        except Exception as exc:  # keep the loop alive whatever happens upstream
            print(f"Foninis atnaujinimas nepavyko: {exc}")
        time.sleep(BACKGROUND_EVERY)


# --------------------------------------------------------------------------- HTTP

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _round_param(query):
    val = query.get("round", [None])[0]
    return int(val) if val not in (None, "") else None


ID = r"([0-9a-f]{24})"


class Handler(BaseHTTPRequestHandler):
    server_version = "FantasyTracker/2.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _dispatch(self, routes):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        for pattern, fn in routes:
            m = re.fullmatch(pattern, url.path)
            if m:
                try:
                    self._send(200, fn(m, query))
                except NotFound as exc:
                    self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                except UpstreamError as exc:
                    self._send(502, {"error": str(exc)})
                return True
        return False

    def do_GET(self):
        routes = [
            (r"/api/leagues", lambda m, q: leagues_payload()),
            (rf"/api/league/{ID}/standings", lambda m, q: standings_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/rounds", lambda m, q: rounds_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/free-agents", lambda m, q: free_agents_payload(m[1])),
            (rf"/api/league/{ID}/records", lambda m, q: records_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/team/{ID}", lambda m, q: team_payload(m[1], m[2], _round_param(q))),
            (rf"/api/league/{ID}/player/{ID}", lambda m, q: player_payload(m[1], m[2])),
        ]
        if self._dispatch(routes):
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._send(404, {"error": "Nežinomas adresas"})
            return
        self._serve_static(path)

    def do_POST(self):
        routes = [
            (r"/api/leagues", lambda m, q: {"league": add_league(self._json_body().get("url"))}),
            (rf"/api/league/{ID}/my-team",
             lambda m, q: set_my_team(m[1], self._json_body().get("teamId")) or {"ok": True}),
        ]
        if not self._dispatch(routes):
            self._send(404, {"error": "Nežinomas adresas"})

    def do_DELETE(self):
        routes = [(rf"/api/leagues/{ID}", lambda m, q: remove_league(m[1]) or {"ok": True})]
        if not self._dispatch(routes):
            self._send(404, {"error": "Nežinomas adresas"})

    def _serve_static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            target = STATIC_DIR / "index.html"
        self._send(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))


def main():
    if not LEAGUES_FILE.exists():
        write_json(LEAGUES_FILE, [])
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=background_loop, daemon=True).start()
    print(f"Fantasy tracker: http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
