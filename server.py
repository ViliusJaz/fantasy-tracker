"""Local tracker for BasketNews Fantasy draft leagues.

    python3 fantasy-tracker/server.py      ->  http://127.0.0.1:8124

Serves the single-page UI from ./static and a small JSON API that proxies the
public BasketNews GraphQL backend (which does not allow browser CORS calls).
Tracked leagues live in ./leagues.json. Things the public API does not keep --
past-round lineups and injury history -- are recorded under ./data while the
server runs.
"""
import bisect
import contextvars
import gzip
import http.client
import itertools
import json
import unicodedata
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
import urllib.parse
from urllib.parse import parse_qs, urlparse

import injury_lt

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

# UI language of the current request ("lt" | "en"); every user-facing text goes through L().
LANG = contextvars.ContextVar("lang", default="lt")


def L(lt, en):
    return en if LANG.get() == "en" else lt


def pool_map(fn, items):
    """POOL.map that keeps the request's language in the worker threads."""
    futures = [POOL.submit(contextvars.copy_context().run, fn, item) for item in items]
    return [f.result() for f in futures]


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


def read_body(req, timeout=30):
    """Response body, unpacked when the server sent it gzip-compressed."""
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
        raw = resp.read()
        return gzip.decompress(raw) if resp.headers.get("Content-Encoding") == "gzip" else raw


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


def players(meta, stats_round, games_round):
    """Every player of the competition: {playerId: view}. Points are for `stats_round`,
    games are those of `games_round`."""
    data = gql(Q_PLAYERS, {
        "leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
        "gamesRound": games_round, "pcs": meta["pointCalcSystem"],
    }, round_ttl(meta, min(stats_round, games_round)))
    views = {p["id"]: player_view(p) for p in data["playersSearchRecordsFromClient"]["records"]}
    attach_usage(meta, views, stats_round)
    mark_round_days(views)
    return views


def mark_round_days(views):
    """Tag each game with the day of the round it is on (1, 2, ...) when a round spans several days."""
    dates = sorted({g["at"][:10] for v in views.values() for g in v["games"] if g.get("at")})
    if len(dates) < 2:
        return
    day = {d: i + 1 for i, d in enumerate(dates)}
    for v in views.values():
        for g in v["games"]:
            if g.get("at"):
                g["day"] = day[g["at"][:10]]


# --------------------------------------------------------------------------- advanced stats (BasketNews)

ADV_URL = "https://basketnews.com/advanced-stats/team-profile/players.json"
_adv_cache = {}
_adv_lock = threading.Lock()


def advanced_stats(meta, rnd=None):
    """BasketNews advanced player stats, {bnPlayerId: row}: the whole season, or one round
    (each club's game number rnd + 1). Empty when BasketNews has no data for it yet."""
    league, season = meta.get("bnLeagueId"), meta.get("seasonYear")
    if not league or not season:
        return {}
    if rnd is not None and rnd > meta["latestRound"]:
        return {}  # a round that has not been played has no stats (BasketNews would send the last one)
    key = (league, season, rnd)
    with _adv_lock:
        hit = _adv_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    form = {"league_id": league, "season": season}
    if rnd is not None:
        form.update(sequence_from=rnd + 1, sequence_to=rnd + 1)
    req = urllib.request.Request(
        ADV_URL, data=urllib.parse.urlencode(form).encode(),
        headers={"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "gzip",
                 "Referer": f"https://basketnews.com/advanced-stats/{league}/{season}"})
    try:
        payload = json.loads(read_body(req))
        data = payload.get("data") or {}
        max_seq = (data.get("extra") or {}).get("max_sequence") or 0
        if rnd is not None and max_seq < rnd + 1:
            result = {}  # BasketNews clamps to its last game; that round isn't there yet
        else:
            result = {str(r["player_id"]): r for r in data.get("stats") or []}
    except (OSError, http.client.HTTPException, ValueError) as exc:  # network, timeout, bad gzip / JSON
        print(f"Pažangi statistika nepasiekiama: {exc}")
        return hit[1] if hit else {}
    ttl = 3600 if rnd is None else (12 * 3600 if rnd < meta["currentRound"] else 300)
    with _adv_lock:
        _adv_cache[key] = (time.time() + ttl, result)
    return result


def adv_value(row, key):
    if not row:
        return None
    v = row.get(key)
    return v.get("value") if isinstance(v, dict) else v


def attach_usage(meta, views, stats_round):
    """Usage %: BasketNews' own numbers, or a box-score estimate for a round they have not published."""
    season_adv = advanced_stats(meta)
    round_adv = advanced_stats(meta, stats_round) if stats_round is not None else {}
    teams = {}
    for v in views.values():
        line, club = v["roundLine"], (v["club"] or {}).get("abbr")
        if line and club:
            t = teams.setdefault(club, [0.0, 0.0, 0.0, 0.0])
            t[0] += line["sec"] / 60
            t[1] += line["p2a"] + line["p3a"]
            t[2] += line["fta"]
            t[3] += line["tov"]
    for v in views.values():
        if v["season"]:
            v["season"]["usg"] = adv_value(season_adv.get(v["bnId"]), "usage_percentage")
        line = v["roundLine"]
        if not line:
            continue
        usg = adv_value(round_adv.get(v["bnId"]), "usage_percentage")
        team = teams.get((v["club"] or {}).get("abbr"))
        mins = line["sec"] / 60
        if usg is None and team and mins > 0:
            team_poss = team[1] + 0.44 * team[2] + team[3]
            if team_poss:
                usg = round(100 * (line["p2a"] + line["p3a"] + 0.44 * line["fta"] + line["tov"]) * (team[0] / 5)
                            / (mins * team_poss), 1)
        line["usg"] = usg


# (key, short, LT title, EN title, LT explanation, EN explanation)
ADV_GROUPS = [
    ("offense", ("Puolimas", "Offense"), [
        ("usage_percentage", "USG%", "Naudojimo dažnis", "Usage %",
         "Kokią dalį komandos atakų žaidėjas užbaigia pats (metimu, baudomis ar klaida), kol yra aikštėje. Kuo jis didesnis, tuo daugiau puolimo eina per jį.",
         "Share of team possessions a player finishes himself (shot, free throws or turnover) while on court. The higher, the more the offense runs through him."),
        ("ts_percentage", "TS%", "Tikrasis metimų taiklumas", "True shooting %",
         "Metimų efektyvumas, įskaitant tritaškių vertę ir baudas: TŠK ÷ (2 × (metimai + 0.44 × baudų metimai)). Geriau nei paprastas taiklumas.",
         "Shooting efficiency that credits threes and free throws: PTS ÷ (2 × (FGA + 0.44 × FTA)). Better than plain FG%."),
        ("offensive_rating_ind", "IORTG", "Individualus puolimo reitingas", "Individual offensive rating",
         "Kiek taškų žaidėjas sukuria per 100 atakų, kurias jis naudoja (savo taškai, rez. perdavimai, atkovoti kamuoliai puolime).",
         "Points a player produces per 100 possessions he uses (own scoring, assists, offensive rebounds)."),
        ("assist_percentage", "AST%", "Rez. perdavimų dalis", "Assist %",
         "Kokia dalis žaidėjo atakų baigiasi rezultatyviu perdavimu, o ne metimu ar klaida.",
         "Share of the player's plays that end with an assist rather than a shot or turnover."),
        ("turnover_percentage", "TOV%", "Klaidų dalis", "Turnover %",
         "Kokia dalis žaidėjo atakų baigiasi klaida. Mažiau yra geriau.",
         "Share of the player's plays that end in a turnover. Lower is better."),
        ("3p_attempted_rate", "3PAR", "Tritaškių dalis metimuose", "3PA rate",
         "Kiek procentų visų žaidėjo metimų iš žaidimo yra tritaškiai.",
         "Share of the player's field-goal attempts taken from three."),
        ("created_points", "PTS+AST", "Sukurti taškai", "Created points",
         "Paties pelnyti taškai plius taškai, kuriuos komandos draugai pelnė po jo rezultatyvių perdavimų.",
         "Points scored himself plus points teammates scored from his assists."),
        ("fouls_received", "FD", "Išprovokuotos pražangos", "Fouls drawn",
         "Vidutiniškai per rungtynes prieš jį padarytos pražangos. Duoda taškų ir naudingumo balui.",
         "Fouls drawn per game. They add to PIR."),
        ("offensive_rebound_percentage", "OREB%", "Atk. kamuolių puolime dalis", "Offensive rebound %",
         "Kokią dalį galimų atkovoti kamuolių po komandos nepataikytų metimų jis atkovoja, kol yra aikštėje.",
         "Share of available offensive rebounds (after team misses) he grabs while on court."),
    ]),
    ("defense", ("Gynyba", "Defense"), [
        ("defensive_rating_ind", "IDRTG", "Individualus gynybos reitingas", "Individual defensive rating",
         "Kiek taškų varžovai pelno per 100 atakų, vertinant žaidėjo indėlį gynyboje. Mažiau yra geriau.",
         "Points allowed per 100 opponent possessions, based on the player's defensive contribution. Lower is better."),
        ("defensive_rebound_percentage", "DREB%", "Atk. kamuolių gynyboje dalis", "Defensive rebound %",
         "Kokią dalį galimų atkovoti kamuolių po varžovų nepataikytų metimų jis atkovoja, kol yra aikštėje.",
         "Share of available defensive rebounds (after opponent misses) he grabs while on court."),
        ("steal_percentage", "STL%", "Perimtų kamuolių dalis", "Steal %",
         "Kokią dalį varžovų atakų, kol jis aikštėje, jis užbaigia perimdamas kamuolį.",
         "Share of opponent possessions, while he is on court, that end with his steal."),
        ("block_percentage", "BLK%", "Blokų dalis", "Block %",
         "Kokią dalį varžovų metimų, kol jis aikštėje, jis užblokuoja.",
         "Share of opponent shot attempts he blocks while on court."),
        ("stops", "STP", "Sustabdytos atakos", "Stops",
         "Vidutiniškai kiek varžovų atakų per rungtynes jis užbaigia gynyboje (perimtas, blokas, atkovotas kamuolys, priverstas nepataikymas).",
         "Opponent possessions per game he ends on defense (steal, block, defensive rebound, forced miss)."),
        ("stop_percentage", "STP%", "Sustabdytų atakų dalis", "Stop %",
         "Kokią dalį varžovų atakų, kol jis aikštėje, jis sustabdo.",
         "Share of opponent possessions he stops while on court."),
    ]),
    ("overall", ("Bendra", "Overall"), [
        ("net_rating_ind", "INRTG", "Individualus balansas", "Individual net rating",
         "Individualus puolimo reitingas minus gynybos reitingas: kiek taškų per 100 atakų žaidėjas „prideda“ komandai.",
         "Individual offensive minus defensive rating: points per 100 possessions the player adds."),
        ("offensive_rating_lineup", "ORTG", "Komandos puolimo reitingas jam žaidžiant", "Team ORtg with him on court",
         "Kiek taškų komanda pelno per 100 atakų, kai jis yra aikštėje.",
         "Team points scored per 100 possessions while he is on court."),
        ("defensive_rating_lineup", "DRTG", "Komandos gynybos reitingas jam žaidžiant", "Team DRtg with him on court",
         "Kiek taškų varžovai pelno per 100 atakų, kai jis yra aikštėje. Mažiau yra geriau.",
         "Opponent points per 100 possessions while he is on court. Lower is better."),
        ("assist_turnover_ratio", "AST/TO", "Rez. perdavimai / klaidos", "Assists / turnovers",
         "Kiek rezultatyvių perdavimų tenka vienai klaidai.",
         "Assists per turnover."),
        ("possessions", "POSS", "Atakos per rungtynes", "Possessions per game",
         "Kiek atakų vidutiniškai per rungtynes sužaidžiama, kol jis aikštėje. Rodo žaidimo laiką ir tempą.",
         "Possessions played per game while he is on court. Reflects minutes and pace."),
        ("pir", "PIR", "Naudingumo balas", "Performance index rating",
         "TŠK + atk. kamuoliai + rez. perdavimai + perimti + blokai + išprovokuotos pražangos − nepataikyti metimai − klaidos − gauti blokai − pražangos.",
         "PTS + REB + AST + STL + BLK + fouls drawn − missed shots − turnovers − blocks against − fouls."),
    ]),
]


# Metrics where a smaller number is the better one.
LOWER_IS_BETTER = {"turnover_percentage", "defensive_rating_ind", "defensive_rating_lineup"}
ADV_MIN_SECONDS = 600  # league context only counts players averaging 10+ minutes


def _quartiles(values):
    vals = sorted(values)
    def at(q):
        pos = (len(vals) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    return at(0.25), at(0.75)


def advanced_context(table):
    """League average, quartiles and the sorted values per metric, from regular-rotation players."""
    rows = [r for r in table.values() if (adv_value(r, "time_played") or 0) >= ADV_MIN_SECONDS]
    out, dist = {}, {}
    for _, _, items in ADV_GROUPS:
        for key, *_ in items:
            vals = sorted(v for v in (adv_value(r, key) for r in rows) if v is not None)
            if len(vals) < 8:
                continue
            p25, p75 = _quartiles(vals)
            out[key] = {"avg": round(sum(vals) / len(vals), 1), "low": round(p25, 1), "high": round(p75, 1),
                        "n": len(vals), "better": "lower" if key in LOWER_IS_BETTER else "higher"}
            dist[key] = vals
    return out, dist


def _percentile(vals, v):
    """Share of the league with a smaller value (ties count half), 0-100."""
    below = bisect.bisect_left(vals, v)
    same = bisect.bisect_right(vals, v) - below
    return round(100 * (below + same / 2) / len(vals))


def advanced_profile(meta, bn_id):
    table = advanced_stats(meta)
    row = table.get(str(bn_id)) if bn_id else None
    if not row:
        return None
    context, dist = advanced_context(table)
    groups = []
    for gid, (lt, en), items in ADV_GROUPS:
        stats = []
        for key, short, title_lt, title_en, desc_lt, desc_en in items:
            cell = row.get(key)
            if not isinstance(cell, dict) or cell.get("value") is None:
                continue
            ctx = context.get(key)
            level = None
            if ctx:
                level = "high" if cell["value"] >= ctx["high"] else "low" if cell["value"] <= ctx["low"] else "avg"
            stats.append({"key": key, "short": short, "title": L(title_lt, title_en), "desc": L(desc_lt, desc_en),
                          # BasketNews' own "pct" runs in different directions per metric, so the
                          # bar uses the same league sample as the high/low level instead.
                          "value": cell["value"], "rank": cell.get("rank"),
                          "pct": _percentile(dist[key], cell["value"]) if key in dist else None,
                          "context": ctx, "level": level})
        groups.append({"id": gid, "title": L(lt, en), "stats": stats})
    return {"groups": groups, "ranked": len(table), "games": row.get("games_played"),
            "contextMinutes": ADV_MIN_SECONDS // 60,
            "url": f"https://basketnews.com/advanced-stats/{meta['bnLeagueId']}/{meta['seasonYear']}"}


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
    if missing:
        mark_round_days(found)  # the directly fetched players need their round day too
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

# The report marks each status with a class id; the ids are the same on the .com and .lt sites.
STATUS_BY_ID = {"1": "ready", "2": "expected", "3": "questionable", "4": "game-time",
                "5": "doubtful", "6": "out", "7": "uncertain"}
# Wording of the BasketNews.com injury report (English) and of BasketNews.lt (Lithuanian).
SITE_LABELS = {"ready": "Ready", "expected": "Expected", "questionable": "Questionable", "game-time": "Game-time",
               "doubtful": "Doubtful", "out": "Out", "uncertain": "Uncertain"}
LT_LABELS = {"ready": "Pasiruošęs", "expected": "Tikėtina", "questionable": "Abejojama",
             "game-time": "Prieš rungtynes", "doubtful": "Mažai tikėtina", "out": "Nežaidžia",
             "uncertain": "Neaišku"}
REMOVED_NOTE = "Išbrauktas iš traumų sąrašo"  # stored in the log when a player leaves the report


def health_label(key):
    return L(LT_LABELS.get(key, key), SITE_LABELS.get(key, key))

# Report entries that are about availability rather than health.
NOT_INJURY = re.compile(
    r"coach'?s?'? decision|not included|roster|personal|family|suspen|national team|domestic league|"
    r"rest\b|load management|visa|contract|transfer|left the (team|club)|released|trade|paternity",
    re.I,
)

DNP_RE = re.compile(r"DNP in Round\s*(\d+)\s*(?:\(([^)]*)\))?", re.I)
_untranslated = set()


def _lt(text, translate):
    """Lithuanian wording from injury_lt, the first injury phrase in it, or the original."""
    lt = translate(text) or injury_lt.gist(text)
    if lt is None and text not in _untranslated:
        _untranslated.add(text)
        print(f"Traumos komentaras neišverstas: {text}")
    return lt or text


def loc_reason(text):
    """A short report reason ('knee injury', "coach's decision") in the UI language."""
    text = _clean(text).rstrip(".")
    if LANG.get() == "en" or not text:
        return text
    return _lt(text, lambda t: injury_lt.reason(t) or injury_lt.translate(t))


def loc_comment(text):
    """A whole report comment in the UI language."""
    if text == REMOVED_NOTE:
        return L(REMOVED_NOTE, "Removed from the injury report")
    text = _clean(text)
    if LANG.get() == "en" or not text:
        return text
    return _lt(text, injury_lt.translate)


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
        key = STATUS_BY_ID.get(cells[2]["status"], "other")
        entries.append({
            "bnId": m.group(1) if m else None,
            "name": _clean(cells[1]["text"]),
            "club": club,
            "pos": _clean(cells[0]["text"]),
            "status": key,
            "siteLabel": _clean(cells[2]["text"]) or SITE_LABELS.get(key, key),  # "Out", "Game-time", ...
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
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en,lt",
                                                   "Accept-Encoding": "gzip"})
        try:
            entries = parse_injury_report(read_body(req).decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
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
    now = datetime.now().astimezone().isoformat(timespec="minutes")  # when the change was first seen
    with _log_lock:
        log = read_json(INJURY_LOG_FILE, {"players": {}})
        before = json.dumps(log["players"], sort_keys=True)
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
            update = {"date": today, "at": now, "status": e["status"], "return": e["return"], "comment": e["comment"]}
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
            _add_update(ep, {"date": ep["end"], "at": now if gap <= 1 else None, "status": "ready", "return": "",
                             "comment": REMOVED_NOTE})
        # Only touch the file when something changed, so the GitHub copy is not re-committed every run.
        if json.dumps(known, sort_keys=True) != before or not INJURY_LOG_FILE.exists():
            log["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            write_json(INJURY_LOG_FILE, log)


def _add_update(episode, update):
    last = episode["updates"][-1] if episode["updates"] else {}
    if any(last.get(k) != update[k] for k in ("status", "return", "comment")):
        episode["updates"].append(update)


def injury_view(entry, health=None):
    """Compact current status for lists: None when the player is fine."""
    if entry and entry["status"] != "ready":
        key = entry["status"]
        return {
            "status": key,
            "label": L(LT_LABELS.get(key, key), entry.get("siteLabel") or SITE_LABELS.get(key, key)),
            "return": return_local(entry["return"]), "comment": loc_comment(entry["comment"]),
        }
    if health and health != "ready":
        return {"status": health, "label": health_label(health), "return": "", "comment": ""}
    return None


def return_local(text):
    t = _clean(text)
    return t if LANG.get() == "en" or not t else injury_lt.return_text(t)


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

    return [{"round": r, **res} for r, res in pool_map(one, rounds) if len(res) > 1 or res["state"] == "upcoming"]


def team_payload(fid, team_id, rnd=None):
    meta = league_meta(fid)
    current = meta["currentRound"]
    rnd = current if rnd is None else max(meta["firstRound"], min(rnd, current))
    shown, rows = standings(meta)
    row = next((r for r in rows if r["team"]["id"] == team_id), None)
    if not row:
        raise NotFound(L("Komanda šioje lygoje nerasta", "Team not found in this league"))

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
                lineup_note = L("Sudėtis išsaugota prieš turui prasidedant, todėl vėlesni pakeitimai galėjo būti nematyti.",
                                "Lineup saved before the round started, so later changes may be missing.")
        else:
            # The API only exposes the current lineup; for earlier rounds show today's roster.
            lineup, source = now_lineup, "roster"
            lineup_note = L("Šio turo sudėtis nebuvo išsaugota (programa dar neveikė), todėl rodomas dabartinis "
                            "komandos sąrašas su to turo taškais. Tikslus to turo penketas ir kapitonas nežinomi.",
                            "This round's lineup was not saved (the app was not running), so the current roster is "
                            "shown with that round's points. The exact starting five and captain are unknown.")

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


def owners(meta, rnd):
    """Who holds each player in round `rnd`: {playerId: {"team": {...}, "slotLabel": "G" | "6th" | ... | None}}.
    Uses that round's saved lineup; falls back to today's rosters (slot unknown)."""
    names = {r["team"]["id"]: r["team"] for r in standings(meta)[1]}
    current = lineups(meta)
    by_team, exact = None, True
    if any(lu["round"] == rnd for lu in current.values()):
        by_team = {tid: lu["players"] for tid, lu in current.items()}
    else:
        snap = read_json(LINEUPS_DIR / f"{meta['id']}.json", {"rounds": {}})["rounds"].get(str(rnd))
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


def players_payload(fid, scope="free"):
    """Player list with season stats: only free agents, or everybody (scope="all") with their owner."""
    meta = league_meta(fid)
    own = owners(meta, meta["currentRound"])
    stats_round = meta["latestRound"]
    pmap = players(meta, stats_round, meta["currentRound"])
    report = injury_report(meta)
    rows = []
    for p in pmap.values():
        owner = own.get(p["id"])
        if scope == "free" and owner:
            continue
        rows.append({**p, "injury": injury_view(report.get(p["bnId"]), p["health"]), "owner": owner})
    rows.sort(key=lambda p: (p["avgPts"] is None, -(p["avgPts"] or 0), p["name"]))
    return {
        "league": meta,
        "scope": scope,
        "statsRound": stats_round,
        "players": rows,
        "totalPlayers": len(pmap),
        "rosteredPlayers": len(own),
        "injuryReportUrl": meta["injuryReportUrl"],
    }


AVG_KEYS = ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "fd", "ba", "eff", "usg")


def league_averages(meta):
    """Per-game league averages of regular-rotation players (the same 10+ minute sample
    as the advanced-stats context), shown in the stat tooltips."""
    pmap = players(meta, meta["latestRound"], meta["currentRound"])
    regular = [p for p in pmap.values()
               if p["season"] and p["gamesPlayed"] and p["season"]["min"] >= ADV_MIN_SECONDS / 60]
    if not regular:
        return None
    mean = lambda vals: round(sum(vals) / len(vals), 1) if vals else None  # noqa: E731
    out = {"n": len(regular), "minutes": ADV_MIN_SECONDS // 60,
           "fp": mean([p["avgPts"] for p in regular if p.get("avgPts") is not None])}
    for key in AVG_KEYS:
        out[key] = mean([p["season"][key] for p in regular if p["season"].get(key) is not None])
    made_att = {}
    for key, made, att in (("p2", "p2m", "p2a"), ("p3", "p3m", "p3a"), ("ft", "ftm", "fta")):
        # season lines are per-game averages: times games played gives the totals
        m = sum(p["season"][made] * p["gamesPlayed"] for p in regular)
        a = sum(p["season"][att] * p["gamesPlayed"] for p in regular)
        made_att[key] = (m, a)
        out[key] = round(100 * m / a, 1) if a else None
    m, a = made_att["p2"][0] + made_att["p3"][0], made_att["p2"][1] + made_att["p3"][1]
    out["fg"] = round(100 * m / a, 1) if a else None
    return out


def games_payload(fid, rnd=None):
    """Every real game of a round with each player's box score and fantasy owner; future
    rounds list their scheduled games with previews."""
    meta = league_meta(fid)
    last = (meta.get("totalRounds") or meta["currentRound"] + 1) - 1
    # the tab opens on the current round (live, or the next one to be played)
    rnd = meta["currentRound"] if rnd is None else max(meta["firstRound"], min(rnd, last))
    pmap = players(meta, rnd, rnd)
    own = owners(meta, rnd)
    clubs, games = {}, {}
    for p in pmap.values():
        club = p["club"]
        if not club:
            continue
        c = clubs.setdefault(club["abbr"], {"abbr": club["abbr"], "name": club.get("fullName") or club.get("name"),
                                            "logo": club["logo"], "players": [], "gameCount": len(p["games"])})
        if p["roundLine"]:
            c["players"].append({"id": p["id"], "name": p["name"], "photo": p["photo"], "fp": p["roundPts"],
                                 "line": p["roundLine"], "owner": own.get(p["id"]), "position": p["position"]})
        for g in p["games"]:
            home, away = (club["abbr"], g["opponent"]) if g["home"] else (g["opponent"], club["abbr"])
            key = (g["at"], home, away)
            if key in games:
                continue
            score = g["score"]
            games[key] = {
                "id": f"{home}-{away}-{g['at'][:10]}", "at": g["at"], "home": home, "away": away,
                "homeScore": (score[0] if g["home"] else score[1]) if score else None,
                "awayScore": (score[1] if g["home"] else score[0]) if score else None,
                "live": g["live"], "completed": g["completed"], "canceled": g["canceled"],
                "opponentLogo": {away if g["home"] else home: g.get("opponentLogo")},
            }
    out = []
    for key in sorted(games, key=lambda k: (k[0], k[1])):
        g = games[key]
        sides = {}
        for side in ("home", "away"):
            c = clubs.get(g[side]) or {"abbr": g[side], "name": g[side], "logo": g["opponentLogo"].get(g[side]),
                                       "players": [], "gameCount": 1}
            plist = sorted(c["players"], key=lambda x: -(x["fp"] if x["fp"] is not None else -99))
            sides[side] = {"abbr": c["abbr"], "name": c["name"], "logo": c["logo"] or g["opponentLogo"].get(g[side]),
                           "players": plist, "combined": c["gameCount"] > 1}
        g.pop("opponentLogo")
        g.update(sides)
        g["owned"] = sum(1 for s_ in ("home", "away") for x in sides[s_]["players"] if x["owner"])
        top = max((x for s_ in ("home", "away") for x in sides[s_]["players"]),
                  key=lambda x: x["fp"] if x["fp"] is not None else -99, default=None)
        g["top"] = {"name": top["name"], "fp": top["fp"]} if top else None
        out.append(g)
    upcoming = [g for g in out if not g["completed"] and not g["live"] and not g["canceled"]]
    if upcoming:
        ctx = preview_context(meta, rnd, pmap, own)
        for g in upcoming:
            g["preview"] = game_preview(g, ctx)
    return {"league": meta, "round": rnd, "state": round_state(meta, rnd), "games": out}


# --------------------------------------------------------------------------- game previews

def club_results(meta, before_round):
    """Finished games of every club before a round: {abbr: [{at, opp, home, for, against}]} oldest first."""
    rounds = list(range(meta["firstRound"], before_round))
    results = {}
    for _, pm in pool_map(lambda r: (r, players(meta, r, r)), rounds):
        for p in pm.values():
            club = (p["club"] or {}).get("abbr")
            for g in (p["games"] if club else []):
                if not g["completed"] or not g["score"]:
                    continue
                games = results.setdefault(club, {})
                games.setdefault(g["at"], {"at": g["at"], "opp": g["opponent"], "home": g["home"],
                                           "for": g["score"][0], "against": g["score"][1]})
    return {club: sorted(games.values(), key=lambda x: x["at"]) for club, games in results.items()}


TEAM_ADV_URL = "https://basketnews.com/advanced-stats/team-profile/overview.json"
_team_adv_cache = {}
# Tokens every other club shares; they would pair e.g. the two Tel Aviv clubs.
_CLUB_NOISE = {"fc", "bc", "basket", "basketball", "tel", "aviv", "istanbul", "belgrade", "athens", "milan"}
# BasketNews team stat -> (our key, "higher is better")
TEAM_STATS = [
    ("offensive_rating", "ortg", True), ("defensive_rating", "drtg", False), ("possessions", "pace", True),
    ("points", "pts", True), ("points_opponent", "ptsAgainst", False), ("ts_percentage", "ts", True),
    ("3p_percentage", "p3", True), ("3p_percentage_opponent", "p3Against", False),
    ("offensive_rebound_percentage", "oreb", True), ("defensive_rebound_percentage", "dreb", True),
    ("assist_percentage", "ast", True), ("turnover_percentage", "tov", False),
]


def team_advanced(meta):
    """BasketNews team stats for the season: {bnTeamId: {"name", "short", "stats": {key: {value, rank}},
    "strengths", "weaknesses", "games"}}; ranks are among all clubs (1 = best)."""
    league, season = meta.get("bnLeagueId"), meta.get("seasonYear")
    if not league or not season:
        return {}
    key = (league, season, LANG.get())
    hit = _team_adv_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    req = urllib.request.Request(
        TEAM_ADV_URL, data=urllib.parse.urlencode({"league_id": league, "season": season}).encode(),
        headers={"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "gzip",
                 "Referer": f"https://basketnews.com/advanced-stats/{league}/{season}"})
    try:
        data = json.loads(read_body(req)).get("data") or {}
    except (OSError, http.client.HTTPException, ValueError) as exc:  # network, timeout, bad gzip / JSON
        print(f"Komandų statistika nepasiekiama: {exc}")
        return hit[1] if hit else {}
    lang = "en" if LANG.get() == "en" else "lt"
    summaries = {}
    for item in data.get("summaries") or []:
        for part in item.get("items") or []:
            # "general" is a free-text summary that is often a request for more data, not a summary
            if part.get("language") == lang and part.get("type") in ("strengths", "weaknesses"):
                summaries.setdefault(item["team_id"], {})[part["type"]] = part.get("content") or []
    stats = {row["team_id"]: row for row in data.get("stats") or []}
    result = {}
    for team in data.get("teams") or []:
        row = stats.get(team["id"]) or {}
        result[team["id"]] = {
            "name": team.get("name_en"), "short": team.get("short_name_en"),
            "games": ((row.get("games_played") or {}).get("total") or {}).get("value") or 0,
            "stats": {ours: {"value": ((row.get(theirs) or {}).get("total") or {}).get("value"),
                             "rank": ((row.get(theirs) or {}).get("total") or {}).get("rank")}
                      for theirs, ours, _ in TEAM_STATS},
            "strengths": (summaries.get(team["id"]) or {}).get("strengths") or [],
            "weaknesses": (summaries.get(team["id"]) or {}).get("weaknesses") or [],
        }
    _team_adv_cache[key] = (time.time() + 3600, result)
    return result


def _club_tokens(name):
    return set(ascii_slug(name or "").split("-")) - _CLUB_NOISE - {""}


def club_team_ids(clubs, teams):
    """Fantasy club abbr -> BasketNews team id, matched on the English club name."""
    out = {}
    for abbr, club in clubs.items():
        mine = _club_tokens(club.get("nameEn"))
        best, score = None, 0
        for tid, team in teams.items():
            sc = len(mine & _club_tokens(team["name"])) + (2 if ascii_slug(club.get("name") or "") == ascii_slug(team["short"] or "") else 0)
            if sc > score:
                best, score = tid, sc
        if best is not None:
            out[abbr] = best
    return out


def preview_context(meta, rnd, pmap, own):
    report = injury_report(meta)
    roster, clubs = {}, {}
    for p in pmap.values():
        if p["club"]:
            roster.setdefault(p["club"]["abbr"], []).append(p)
            clubs[p["club"]["abbr"]] = p["club"]
    # only rounds that can have results (future rounds have none yet)
    results = club_results(meta, min(rnd, meta["currentRound"] + 1))
    allowed = [g["against"] for games in results.values() for g in games]
    teams = team_advanced(meta)
    return {"roster": roster, "results": results, "own": own, "report": report,
            # a game two or more rounds away: today's injury list says little about it
            "farAhead": rnd - meta["currentRound"] >= 2,
            "leagueAllowed": sum(allowed) / len(allowed) if allowed else None,
            "teamStats": {abbr: teams[tid] for abbr, tid in club_team_ids(clubs, teams).items()},
            "teamCount": len(teams)}


def _preview_player(p, own, report):
    season = p["season"] or {}
    inj = injury_view(report.get(p["bnId"]), p["health"])
    return {**player_brief(p), "owner": (own.get(p["id"]) or {}).get("team"),
            "line": {k: season.get(k) for k in ("min", "pts", "reb", "ast")},
            "injury": inj}


def game_preview(g, ctx):
    """What to know before a game: form, key players, injuries and a few generated notes."""
    own, report = ctx["own"], ctx["report"]
    sides, notes = {}, []
    for side in ("home", "away"):
        abbr = g[side]["abbr"]
        roster = ctx["roster"].get(abbr, [])
        games = ctx["results"].get(abbr, [])
        views = [_preview_player(p, own, report) for p in roster]
        active = sorted((v for v in views if v["gamesPlayed"]), key=lambda v: -(v["avgPts"] or 0))
        injured = sorted((v for v in views if v["injury"]), key=lambda v: -(v["avgPts"] or 0))
        wins = sum(1 for x in games if x["for"] > x["against"])
        sides[side] = {
            "record": {"w": wins, "l": len(games) - wins} if games else None,
            "avgFor": round(sum(x["for"] for x in games) / len(games), 1) if games else None,
            "avgAgainst": round(sum(x["against"] for x in games) / len(games), 1) if games else None,
            "last": [{"won": x["for"] > x["against"], "score": [x["for"], x["against"]], "opp": x["opp"],
                      "home": x["home"]} for x in games[-5:]],
            "key": [v for v in active if not (v["injury"] and v["injury"]["status"] == "out")][:4],
            "injuries": injured,
            "team": ctx["teamStats"].get(abbr),
        }
    notes = preview_notes(g, sides, ctx)
    owned = [{"player": {k: v[k] for k in ("id", "name")}, "owner": v["owner"], "club": g[side]["abbr"]}
             for side in ("home", "away")
             for v in (_preview_player(p, own, report) for p in ctx["roster"].get(g[side]["abbr"], []))
             if v["owner"]]
    return {"home": sides["home"], "away": sides["away"], "notes": notes, "owned": owned,
            "teamCount": ctx["teamCount"]}


def team_notes(g, sides, ctx):
    """Notes from the BasketNews team ratings: attack against a weak defence, pace."""
    n = ctx["teamCount"] or 20
    home, away = sides["home"]["team"], sides["away"]["team"]
    if not home or not away or min(home["games"], away["games"]) < 1:
        return []
    notes = []
    top, bottom = max(3, n // 4), n - max(3, n // 4) + 1  # top / bottom quarter of the league
    for team, other, abbr, opp in ((home, away, g["home"]["abbr"], g["away"]["abbr"]),
                                   (away, home, g["away"]["abbr"], g["home"]["abbr"])):
        o, d = team["stats"]["ortg"], other["stats"]["drtg"]
        if o["rank"] and d["rank"] and o["rank"] <= top and d["rank"] >= bottom:
            notes.append(L(f"Palanku {abbr} žaidėjams: puolimas {o['rank']}-as lygoje, o {opp} gynyba tik {d['rank']}-a.",
                           f"Good for {abbr} players: their offense ranks {o['rank']}, {opp}'s defense only {d['rank']}."))
        elif o["rank"] and d["rank"] and o["rank"] >= bottom and d["rank"] <= top:
            notes.append(L(f"Sunkus vakaras {abbr} puolimui: {opp} gynyba {d['rank']}-a lygoje.",
                           f"Tough night for {abbr}'s offense: {opp}'s defense ranks {d['rank']}."))
    ph, pa = home["stats"]["pace"]["rank"], away["stats"]["pace"]["rank"]
    if ph and pa and ph <= top and pa <= top:
        notes.append(L("Abi komandos žaidžia greitu tempu: daugiau atakų, daugiau fantasy taškų.",
                       "Both teams play fast: more possessions, more fantasy points."))
    elif ph and pa and ph >= bottom and pa >= bottom:
        notes.append(L("Abi komandos žaidžia lėtai: mažiau atakų, mažiau taškų.",
                       "Both teams play slowly: fewer possessions, fewer points."))
    return notes


def preview_notes(g, sides, ctx):
    notes = []
    for side, other in (("home", "away"), ("away", "home")):
        abbr, opp = g[side]["abbr"], g[other]["abbr"]
        info = sides[side]
        for v in ([] if ctx["farAhead"] else info["injuries"]):
            status, avg = v["injury"]["status"], v["avgPts"] or 0
            if status == "out" and avg >= 8:
                notes.append(L(f"{abbr} žais be {v['name']} (vid. {num(avg)} FP).",
                               f"{abbr} will be without {v['name']} ({num(avg)} FP avg)."))
            elif status != "out" and avg >= 12:
                notes.append(L(f"{v['name']} ({abbr}) dalyvavimas neaiškus: {v['injury']['label'].lower()}.",
                               f"{v['name']} ({abbr}) is uncertain: {v['injury']['label'].lower()}."))
        if g[side].get("combined"):
            notes.append(L(f"{abbr} šį turą žaidžia daugiau nei vienas rungtynes: jų žaidėjai gali surinkti daugiau taškų.",
                           f"{abbr} play more than once this round, so their players can score more."))
        last = [x["for"] > x["against"] for x in ctx["results"].get(abbr, [])]
        streak = 0
        for won in reversed(last):
            if won != last[-1]:
                break
            streak += 1
        if streak >= 2:
            notes.append(L(f"{abbr} {'laimėjo' if last[-1] else 'pralaimėjo'} {streak} rungtynes iš eilės.",
                           f"{abbr} have {'won' if last[-1] else 'lost'} {streak} in a row."))
        allowed, league = info["avgAgainst"], ctx["leagueAllowed"]
        if allowed is not None and league and len(last) >= 2 and allowed - league >= 5:
            notes.append(L(f"Palanku {opp} puolėjams: {abbr} praleidžia vid. {num(allowed)} tšk. (lygos vid. {num(round(league, 1))}).",
                           f"Good for {opp} scorers: {abbr} allow {num(allowed)} points a game (league {num(round(league, 1))})."))
    notes += team_notes(g, sides, ctx)
    free = [v for side in ("home", "away") for v in sides[side]["key"] if not v["owner"]]
    if free:
        best = max(free, key=lambda v: v["avgPts"] or 0)
        club = next(g[s_]["abbr"] for s_ in ("home", "away") if best in sides[s_]["key"])
        notes.append(L(f"Geriausias laisvasis agentas šiose rungtynėse: {best['name']} ({club}, vid. {num(best['avgPts'])} FP).",
                       f"Best free agent in this game: {best['name']} ({club}, {num(best['avgPts'])} FP avg)."))
    counts = {}
    for side in ("home", "away"):
        for p in ctx["roster"].get(g[side]["abbr"], []):
            team = (ctx["own"].get(p["id"]) or {}).get("team")
            if team:
                counts.setdefault(team["id"], [team, 0])[1] += 1
    if counts:
        team, n = max(counts.values(), key=lambda x: x[1])
        if n >= 2:
            notes.append(L(f"Daugiausia žaidėjų šiose rungtynėse turi {team['title']} ({n}).",
                           f"{team['title']} have the most players in this game ({n})."))
    return notes


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
            "reason": loc_reason(reason),
            "start": start,
            "end": end,
            "days": max((until - date.fromisoformat(start)).days, 0),
            "ongoing": end is None,
            "status": last_real["status"],
            "statusLabel": health_label(last_real["status"]),
            "return": return_local(last_real.get("return")),
            "missedRounds": ep_missed,
            "updates": [
                {"date": u["date"], "statusLabel": health_label(u["status"]),
                 "return": return_local(u.get("return")), "comment": loc_comment(u.get("comment"))}
                for u in ep["updates"]
            ],
        })
    episodes.reverse()
    return {"episodes": episodes, "missed": [g["round"] for g in missed],
            "summary": injury_summary(episodes, missed, game_log)}


def _plural_rounds(n):
    if LANG.get() == "en":
        return f"{n} round" if n == 1 else f"{n} rounds"
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
            return L(f"Šį sezoną nepraleido nė vieno turo: sužaidė {len(played)} iš {len(team_games)}.",
                     f"Has not missed a round this season: played {len(played)} of {len(team_games)}.")
        return L("Šį sezoną dar nežaidė ir traumų sąraše nebuvo.",
                 "Has not played yet this season and has not been on the injury report.")
    if missed:
        rounds = ", ".join(str(g["round"] + 1) for g in missed)
        parts.append(L(f"Šį sezoną praleido {_plural_rounds(len(missed))} (turai: {rounds}).",
                       f"Missed {_plural_rounds(len(missed))} this season (rounds: {rounds})."))
    else:
        parts.append(L("Šį sezoną nepraleido nė vieno turo.", "Has not missed a round this season."))
    for e in injuries:
        what = e["reason"] or L("trauma", "injury")
        if e["end"]:
            span = L(f"nuo {e['start'][5:]} iki {e['end'][5:]}", f"from {e['start'][5:]} to {e['end'][5:]}")
        else:
            span = L(f"nuo {e['start'][5:]}, tęsiasi", f"since {e['start'][5:]}, ongoing")
        parts.append(f"{what[:1].upper() + what[1:]}: {span} ({e['days']} {L('d.', 'days')}).")
    others = [e for e in episodes if e["kind"] == "other"]
    if others and not injuries:
        reason = others[0]["reason"]
        if reason and missed:
            parts.append(L(f"Traumų nebuvo. Priežastis: {reason}.", f"No injuries. Reason: {reason}."))
        else:
            parts.append(L("Traumų nebuvo.", "No injuries."))
    return " ".join(parts)


# --------------------------------------------------------------------------- Proballers links
# Proballers is bot-protected and has no public search, but Wikidata stores each player's
# Proballers ID (P8548). Namesakes are told apart by birth date from the BasketNews profile.

PROBALLERS_FILE = DATA_DIR / "proballers.json"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_UA = "fantasy-tracker/1.0 (personal basketball fantasy tracker; local app)"
_proballers_lock = threading.Lock()
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def ascii_slug(text):
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip("-")


def name_variants(name):
    base = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", name.strip())
    out = {name.strip(), base}
    m = re.match(r"^([A-Z])\.?\s?([A-Z])\.?\s+(.+)$", base)  # TJ / T.J. / T. J.
    if m:
        a, b, rest = m.groups()
        out |= {f"{a}{b} {rest}", f"{a}.{b}. {rest}", f"{a}. {b}. {rest}"}
    out |= {unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode() for v in list(out)}
    return sorted(out)


_wikidata_clock = threading.Lock()
_wikidata_last = [0.0]


def _wikidata_get(url, data=None):
    """Polite Wikidata request: at most one per second (their API rate-limits bursts)."""
    with _wikidata_clock:
        wait = 1.0 - (time.time() - _wikidata_last[0])
        if wait > 0:
            time.sleep(wait)
        _wikidata_last[0] = time.time()
    headers = {"User-Agent": WIKIDATA_UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
        return json.load(resp)


def wikidata_search(text, limit=10):
    """Full-text Wikidata search (ignores diacritics, covers aliases) among items with a Proballers ID."""
    api = "https://www.wikidata.org/w/api.php?"
    hits = _wikidata_get(api + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": f"{text} haswbstatement:P8548",
        "srlimit": limit, "format": "json"}))["query"]["search"]
    ids = [h["title"] for h in hits]
    if not ids:
        return []
    ents = _wikidata_get(api + urllib.parse.urlencode({
        "action": "wbgetentities", "ids": "|".join(ids), "props": "labels|claims", "languages": "en",
        "format": "json"}))["entities"]
    out = []
    for qid in ids:
        claims = ents.get(qid, {}).get("claims", {})
        pb = ((claims.get("P8548") or [{}])[0].get("mainsnak", {}).get("datavalue") or {}).get("value")
        dob = (((claims.get("P569") or [{}])[0].get("mainsnak", {}).get("datavalue") or {}).get("value") or {}).get("time", "")
        label = ents.get(qid, {}).get("labels", {}).get("en", {}).get("value") or text
        if pb:
            out.append({"pb": pb, "dob": dob[1:11] or None, "label": label})
    return out


def wikidata_candidates(name):
    values = " ".join(f'"{v}"@{lang}' for v in name_variants(name) for lang in ("en", "mul")).replace("\\", "")
    query = f"""SELECT DISTINCT ?pb ?dob ?label WHERE {{
      VALUES ?name {{ {values} }}
      {{ ?item rdfs:label ?name }} UNION {{ ?item skos:altLabel ?name }}
      ?item wdt:P8548 ?pb.
      OPTIONAL {{ ?item wdt:P569 ?dob }}
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
    }}"""
    rows = _wikidata_get(WIKIDATA_SPARQL, urllib.parse.urlencode({"query": query, "format": "json"}).encode()
                         )["results"]["bindings"]
    found = {}
    for r in rows:
        pb = r["pb"]["value"]
        found.setdefault(pb, {"pb": pb, "dob": (r.get("dob") or {}).get("value", "")[:10] or None,
                              "label": (r.get("label") or {}).get("value") or name})
    return list(found.values())


def basketnews_birth_date(bn_id, name):
    """'Age: 33 (1993 April 10)' on the BasketNews player page -> '1993-04-10'."""
    url = f"https://basketnews.com/players/{bn_id}-{ascii_slug(name)}.html"
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    m = re.search(r"\((\d{4})\s+([A-Za-z]+)\s+(\d{1,2})\)", page)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return f"{m.group(1)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(3)):02d}"


def proballers_link(info):
    """Direct Proballers profile URL for a player, or None when it cannot be pinned down."""
    key = info["bnId"] or info["id"]
    with _proballers_lock:
        cache = read_json(PROBALLERS_FILE, {})
    hit = cache.get(key)
    if hit and (hit.get("url") or hit.get("checked", "") >= (date.today() - timedelta(days=7)).isoformat()):
        return hit.get("url")
    url = None
    try:
        # 1) exact name / alias, 2) diacritic-insensitive search, 3) surname only (needs a birth-date match)
        cands, strict = wikidata_candidates(info["name"]), False
        if not cands:
            cands = wikidata_search(info["name"])
        if not cands:
            surname = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", info["name"]).split()[-1]
            cands, strict = wikidata_search(surname, limit=20), True
        if (len(cands) > 1 or strict) and info.get("bnId"):
            dob = basketnews_birth_date(info["bnId"], info["name"])
            cands = [c for c in cands if dob and c["dob"] == dob]
        if len(cands) == 1:
            c = cands[0]
            url = f"https://www.proballers.com/basketball/player/{c['pb']}/{ascii_slug(c['label'])}"
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ValueError, KeyError) as exc:
        print(f"Proballers paieška nepavyko ({info['name']}): {exc}")
        return None
    with _proballers_lock:
        cache = read_json(PROBALLERS_FILE, {})
        cache[key] = {"name": info["name"], "url": url, "checked": date.today().isoformat()}
        write_json(PROBALLERS_FILE, cache)
    return url


def proballers_redirect(fid, player_id):
    meta = league_meta(fid)
    info = players(meta, meta["latestRound"], meta["currentRound"]).get(player_id)
    if not info:
        data = gql(_player_rounds_query([]), {"id": player_id, "leagueId": meta["leagueId"], "locale": LOCALE,
                                              "pcs": meta["pointCalcSystem"], "statsRound": meta["latestRound"],
                                              "gamesRound": meta["currentRound"]})["playerRecordFromClient"]
        if not data:
            raise NotFound(L("Žaidėjas nerastas", "Player not found"))
        info = player_view(data)
    return proballers_target(info)


def proballers_target(info):
    """Player's Proballers page, or a search limited to Proballers player pages when Wikidata has none."""
    return proballers_link(info) or proballers_search(info)


def proballers_search(info):
    query = f"site:proballers.com/basketball/player {info['name']} {((info.get('club') or {}).get('nameEn') or '')}"
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query.strip())


def player_payload(fid, player_id):
    meta = league_meta(fid)
    last = meta["latestRound"]
    rounds = list(range(meta["firstRound"], last + 1))
    data = gql(_player_rounds_query(rounds), {
        "id": player_id, "leagueId": meta["leagueId"], "locale": LOCALE, "pcs": meta["pointCalcSystem"],
        "statsRound": last, "gamesRound": meta["currentRound"],
    })["playerRecordFromClient"]
    if not data:
        raise NotFound(L("Žaidėjas nerastas", "Player not found"))
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
                reasons[rnd_no - 1] = loc_reason(why)
        for r in ep["missedRounds"]:
            reasons.setdefault(r, ep["reason"])
    if current:  # recovered players keep a "DNP in Round N (...)" note in the report
        rnd_no, why = dnp_reason(current["comment"])
        if rnd_no:
            reasons.setdefault(rnd_no - 1, loc_reason(why) or loc_comment(current["comment"]))
    for row in game_log:
        if row["status"] == "dnp":
            row["reason"] = reasons.get(row["round"])
    shooting = season_shooting([row["line"] for row in game_log if row["status"] == "played"])

    owner = None
    for team_id, lu in lineups(meta).items():
        if any(p["id"] == player_id for p in lu["players"]):
            owner = next((r["team"] for r in standings(meta)[1] if r["team"]["id"] == team_id), {"id": team_id})
            break

    return {
        "league": {"id": meta["id"], "title": meta["title"], "currentRound": meta["currentRound"]},
        "player": info,
        "owner": owner,
        "advanced": advanced_profile(meta, info["bnId"]),
        "leagueAvg": league_averages(meta),
        "proballers": f"/go/proballers/{fid}/{player_id}",
        "injury": {
            "current": injury_view(current, info["health"]),
            "reportUrl": meta["injuryReportUrl"],
            **history,
        },
        "gameLog": list(reversed(game_log)),
        "nextGames": info["games"],
        "shooting": shooting,
    }


def season_shooting(lines):
    """Made / attempted / % for the season, summed from the round box scores."""
    def split(made, att):
        m, a = sum(x[made] for x in lines), sum(x[att] for x in lines)
        return {"made": m, "att": a, "pct": round(100 * m / a, 1) if a else None}
    two, three, ft = split("p2m", "p2a"), split("p3m", "p3a"), split("ftm", "fta")
    fg = {"made": two["made"] + three["made"], "att": two["att"] + three["att"]}
    fg["pct"] = round(100 * fg["made"] / fg["att"], 1) if fg["att"] else None
    return {"fg": fg, "two": two, "three": three, "ft": ft, "games": len(lines)}


# --------------------------------------------------------------------------- injury news

# How a report status reads as news ("Name (CLUB) nežais.").
FEED_PHRASES = {
    "out": ("nežais.", "will not play."),
    "doubtful": ("dalyvavimas abejotinas.", "is doubtful."),
    "questionable": ("dalyvavimas abejotinas.", "is questionable."),
    "uncertain": ("būsena neaiški.", "status unclear."),
    "game-time": ("sprendimas bus priimtas prieš rungtynes.", "will be a game-time decision."),
    "expected": ("turėtų žaisti.", "is expected to play."),
    "ready": ("sveikas ir grįžta į rikiuotę.", "is healthy and back in the lineup."),
}
FEED_TONE = {"out": "bad", "ready": "good", "expected": "good"}
FEED_LIMIT = 300


def injuries_payload(fid):
    """Injury-report changes as a news feed, newest first, with each player's owner in this league."""
    meta = league_meta(fid)
    injury_report(meta)  # records any new changes first
    log = read_json(INJURY_LOG_FILE, {"players": {}})["players"]
    pmap = players(meta, meta["latestRound"], meta["currentRound"])
    by_bn = {p["bnId"]: p for p in pmap.values() if p.get("bnId")}
    own = owners(meta, meta["currentRound"])
    events = []
    for bn_id, rec in log.items():
        p = by_bn.get(bn_id)
        owner = (own.get(p["id"]) or {}).get("team") if p else None
        brief = player_brief(p, None, rec.get("name"))
        if not p:
            brief["club"] = {"abbr": rec.get("club")} if rec.get("club") else None
        for ep in rec["episodes"]:
            for u in ep["updates"]:
                status = u["status"]
                comment = "" if u.get("comment") == REMOVED_NOTE else loc_comment(u.get("comment"))
                events.append({
                    "at": u.get("at") or u["date"], "hasTime": bool(u.get("at")),
                    "status": status, "label": health_label(status),
                    "phrase": L(*FEED_PHRASES.get(status, ("būsena pasikeitė.", "status changed."))),
                    "tone": FEED_TONE.get(status, "neutral"),
                    "comment": comment, "return": return_local(u.get("return")),
                    "player": brief, "owner": owner,
                })
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"league": meta, "events": events[:FEED_LIMIT], "total": len(events),
            "teams": [r["team"] for r in standings(meta)[1]], "reportUrl": meta["injuryReportUrl"]}


# --------------------------------------------------------------------------- draft & transfers

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


def player_brief(p, pid=None, name=None):
    if not p:
        return {"id": pid, "name": name or "?", "photo": None, "position": None, "club": None, "avgPts": None}
    return {k: p.get(k) for k in ("id", "name", "photo", "position", "club", "avgPts", "gamesPlayed")}


def draft_payload(fid):
    """Every pick of the league's draft in order, with where the player is now."""
    meta = league_meta(fid)
    rec = gql(Q_DRAFT, {"id": fid}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"] or {}
    picks = sorted(((rec.get("draft") or {}).get("picks") or []), key=lambda p: p["id"])
    teams = {r["team"]["id"]: r["team"] for r in standings(meta)[1]}
    per_round = len(teams) or 1
    ids = [p["player"]["id"] for p in picks if p.get("player")]
    pmap = players_by_ids(meta, ids, meta["latestRound"], meta["currentRound"])
    own = owners(meta, meta["currentRound"])
    rows = []
    for i, pick in enumerate(picks):
        pid = (pick.get("player") or {}).get("id")
        rows.append({
            "overall": i + 1, "round": i // per_round + 1, "pick": i % per_round + 1,
            "team": teams.get(pick["fantasyTeamId"]) or {"id": pick["fantasyTeamId"], "title": "?"},
            "player": player_brief(pmap.get(pid), pid),
            "owner": (own.get(pid) or {}).get("team"),
        })
    return {"league": meta, "picks": rows, "teams": list(teams.values())}


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


def _transfer_side(item, teams, pmap):
    team = (item or {}).get("fantasyTeam") or {}
    listed = (item or {}).get("players") or []
    moved = [x for x in listed if x.get("traded")] or listed
    return {
        "team": (teams.get(team["id"]) or {"id": team["id"], "title": team.get("title")}) if team.get("id") else None,
        "players": [player_brief(pmap.get(x["player"]["id"]), x["player"]["id"],
                                 f"{x['player'].get('firstName', '')} {x['player'].get('lastName', '')}".strip())
                    for x in moved if x.get("player")],
        "credits": (item or {}).get("credits") or 0,
    }


def transfers_payload(fid):
    """Processed free-agent signings and trades per round, credits left per team, and the
    public bid counts for the coming round."""
    meta = league_meta(fid)
    cur = meta["currentRound"]
    raw = raw_transfers(meta)
    try:
        bids = gql(Q_BID_SUMMARY, {"fantasyLeagueId": fid, "fantasyRound": cur})["draftFreeAgentBidsSummaryFromClient"] or []
    except UpstreamError:
        bids = []

    table = standings(meta)[1]
    teams = {r["team"]["id"]: r["team"] for r in table}
    ids = {x["player"]["id"] for _, ts in raw for t in ts for side in ("offer", "request")
           for x in ((t.get(side) or {}).get("players") or []) if x.get("player")}
    ids |= {b["player"]["id"] for b in bids if b.get("player")}
    pmap = players_by_ids(meta, sorted(ids), meta["latestRound"], cur) if ids else {}

    start = meta["draft"].get("startingCredits") or 0
    summary = {tid: {"team": team, "credits": start, "signings": 0, "trades": 0, "spent": 0}
               for tid, team in teams.items()}
    moves = []
    for rnd, ts in raw:
        for t in ts:
            offer, request = _transfer_side(t.get("offer"), teams, pmap), _transfer_side(t.get("request"), teams, pmap)
            change = request["credits"] - offer["credits"]  # for the offering team; the other side gets -change
            kind = "trade" if t.get("type") == "team" else "free_agent"
            moves.append({"id": t["id"], "type": kind, "round": rnd, "at": t.get("updatedAt"),
                          "offer": offer, "request": request, "creditChange": change})
            for side, delta in ((offer, change), (request, -change)):
                row = summary.get((side["team"] or {}).get("id"))
                if not row:
                    continue
                row["credits"] += delta
                row["spent"] += max(-delta, 0)
                row["trades" if kind == "trade" else "signings"] += 1
    moves.sort(key=lambda m: (m["round"], m["at"] or ""), reverse=True)

    upcoming = [{"player": player_brief(pmap.get(b["player"]["id"]), b["player"]["id"]),
                 "highestBid": b.get("highestBid"), "totalBids": b.get("totalBids") or 0}
                for b in bids if b.get("player")]
    upcoming.sort(key=lambda b: (-b["totalBids"], -(b["highestBid"] or 0)))
    order = {r["team"]["id"]: r["position"] for r in table}
    return {
        "league": meta,
        "moves": moves,
        "teams": sorted(summary.values(), key=lambda r: order.get(r["team"]["id"], 99)),
        "upcoming": upcoming,
        "lock": meta.get("transferLock"),
        "usesCredits": meta["draft"].get("tradingMethod") == "credits",
        "startingCredits": start,
    }


# --------------------------------------------------------------------------- season records & awards

def num(v):
    if v is None:
        return "-"
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def rnd_word(r):
    """'3 turas' / 'Round 3' for a 0-based round."""
    return L(f"{r + 1} turas", f"Round {r + 1}")


def teams_word(n):
    if LANG.get() == "en":
        return f"{n} teams"
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} komanda"
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return f"{n} komandos"
    return f"{n} komandų"


def award(title, name, value, sub="", icon="", team_id=None, player_id=None, info=None):
    """One award card; `info` explains how a less obvious award is worked out."""
    return {"title": title, "icon": icon, "name": name, "value": value, "sub": sub,
            "teamId": team_id, "playerId": player_id, "info": info}


def no_award(title, sub, icon="", info=None):
    return award(title, L("Dar nėra", "None yet"), "", sub, icon, info=info)


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
        cards.append(award(L("Daugiausiai surinko", "Most points"), teams[hi]["title"], num(scores[hi]), team_id=hi))
        cards.append(award(L("Mažiausiai surinko", "Fewest points"), teams[lo]["title"], num(scores[lo]), team_id=lo))
    decided = [g for g in bd["games"] if not g["tie"]]
    if decided:
        big = max(decided, key=lambda g: g["margin"])
        small = min(decided, key=lambda g: g["margin"])
        unlucky = max(decided, key=lambda g: g["ls"])
        lucky = min(decided, key=lambda g: g["ws"])
        cards += [
            award(L("Didžiausia pergalė", "Biggest win"), big["winner"]["title"], f"+{num(big['margin'])}",
                  f"{num(big['ws'])} : {num(big['ls'])} {L('prieš', 'vs')} {big['loser']['title']}",
                  team_id=big["winner"]["id"]),
            award(L("Mažiausias skirtumas", "Closest win"), small["winner"]["title"], f"+{num(small['margin'])}",
                  f"{num(small['ws'])} : {num(small['ls'])} {L('prieš', 'vs')} {small['loser']['title']}",
                  team_id=small["winner"]["id"]),
            award(L("Nelaimingiausias pralaimėjimas", "Unluckiest loss"), unlucky["loser"]["title"], num(unlucky["ls"]),
                  L(f"Daugiausia taškų pralaimėjus (prieš {unlucky['winner']['title']})",
                    f"Most points in a loss (vs {unlucky['winner']['title']})"), team_id=unlucky["loser"]["id"]),
            award(L("Laimingiausia pergalė", "Luckiest win"), lucky["winner"]["title"], num(lucky["ws"]),
                  L(f"Mažiausiai taškų laimėjus (prieš {lucky['loser']['title']})",
                    f"Fewest points in a win (vs {lucky['loser']['title']})"), team_id=lucky["winner"]["id"]),
        ]
    lus = bd["lineups"]
    if not lus:
        cards.append(award(L("Sudėčių apdovanojimai", "Lineup awards"), L("Nėra duomenų", "No data"), "",
                           L(f"{rnd + 1} turo sudėtys nebuvo išsaugotos, todėl MVP, kapitonų ir prarastų taškų "
                             "apdovanojimų apskaičiuoti negalima.",
                             f"Round {rnd + 1} lineups were not saved, so MVP, captain and points-lost awards "
                             "cannot be calculated.")))
        return cards
    active = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["slot"] != "inactive"]
    mvp_tid, mvp = max(active, key=lambda x: x[1].get("roundPts") or -999)
    caps = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["captain"]]
    lost_tid = max(lus, key=lambda t: lus[t]["lost"])
    cards.append(award(L("Turo MVP žaidėjas", "Round MVP"), mvp["name"], num(mvp.get("roundPts")),
                       teams.get(mvp_tid, {}).get("title", ""), player_id=mvp["id"]))
    if caps:
        best_tid, best_cap = max(caps, key=lambda x: x[1]["contrib"])
        worst_tid, worst_cap = min(caps, key=lambda x: x[1]["contrib"])
        cards.append(award(L("Geriausias kapitonas", "Best captain"), best_cap["name"], num(best_cap["contrib"]),
                           teams.get(best_tid, {}).get("title", ""), player_id=best_cap["id"]))
        cards.append(award(L("Nesėkmingiausias kapitonas", "Worst captain"), worst_cap["name"], num(worst_cap["contrib"]),
                           teams.get(worst_tid, {}).get("title", ""), player_id=worst_cap["id"]))
    cards.append(award(L("Daugiausiai prarado dėl sudėties", "Most points lost to lineup"), teams.get(lost_tid, {}).get("title", ""),
                       f"−{num(lus[lost_tid]['lost'])}" if lus[lost_tid]["lost"] else "0",
                       L(f"Su optimalia sudėtimi būtų {num(lus[lost_tid]['optimal'])}", f"Optimal lineup: {num(lus[lost_tid]['optimal'])}"), team_id=lost_tid))
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
            cards.append(award(L("Kapitonų karalius", "Captain king"), name(king), num(cap_total[king]),
                               L("daugiausia kapitonų taškų per sezoną", "most captain points this season"), "🏆", team_id=king))
        if best_pick:
            r, tid, p = best_pick
            cards.append(award(L("Geriausias kapitono pasirinkimas", "Best captain pick"), p["name"], num(p["contrib"]),
                               f"{name(tid)}, {rnd_word(r)}", "🎯", player_id=p["id"]))
        if cap_total:
            cards.append(award(L("Kapitonų nesėkmė", "Captain flop"), name(flop), num(cap_total[flop]),
                               L("mažiausiai kapitonų taškų", "fewest captain points"), "🙈", team_id=flop))
        bench = max(lost_total, key=lost_total.get)
        sharp = min(lost_total, key=lost_total.get)
        cards.append(award(L("Suolo karalius", "Bench king"), name(bench), f"−{num(lost_total[bench])}" if lost_total[bench] else "0",
                           L("daugiausia taškų paliko ant suolo", "most points left on the bench"), "🪑", team_id=bench))
        cards.append(award(L("Tiksliausias treneris", "Sharpest coach"), name(sharp), num(lost_total[sharp]),
                           L("mažiausiai prarado dėl sudėties", "fewest points lost to lineup"), "🧠", team_id=sharp))
        if contrib:
            (pid, tid), (pname, total, rounds) = max(contrib.items(), key=lambda kv: kv[1][1])
            cards.append(award(L("Sezono MVP", "Season MVP"), pname, num(total), f"{name(tid)} · {len(rounds)} {L('tur.', 'rd' if len(rounds) == 1 else 'rds')}", "⭐",
                               player_id=pid))
        cards.append(best_transfer(with_lineups, team_names))
    else:
        cards.append(award(L("Sudėčių apdovanojimai", "Lineup awards"), L("Nėra duomenų", "No data"), "",
                           L("Nėra išsaugotų turų sudėčių.", "No saved round lineups.")))
    best_round = max(((bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]), default=None)
    if best_round:
        pts, tid, r = best_round
        cards.append(award(L("Sezono turas", "Round of the season"), name(tid), num(pts), rnd_word(r), "🔥", team_id=tid))
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
    first = rnd_word(with_lineups[0]["round"])
    info = L(
        f"Žaidėjas, kurio komanda neturėjo {first.replace('turas', 'ture')}, t. y. vėliau paimtas iš laisvųjų "
        "agentų arba gautas mainais. Skaičiuojami tik taškai, kuriuos jis atnešė naujajai komandai nuo "
        "įsigijimo, su sudėties koeficientais: startinis penketas ir 6-as žaidėjas ×1, kapitonas ×2, "
        "atsarginiai ×0,5, neregistruoti 0. Sumuojami tik turai, kurių sudėtys išsaugotos. "
        "Laimi daugiausia taškų atnešęs įsigijimas.",
        f"A player who was not on the team's roster in {first}, i.e. picked up later from free agency or "
        "via a trade. Only the points earned for the new team since the move count, with lineup "
        "multipliers: starters and 6th man ×1, captain ×2, bench ×0.5, not registered 0. Only rounds "
        "with a saved lineup are added up. The acquisition with the most points wins.")
    if not gained:
        return no_award(L("Sezono sandoris", "Deal of the season"), L("atsiras po pirmųjų perėjimų", "appears after the first transfers"),
                        "🤝", info=info)
    (pid, tid), (pname, total) = max(gained.items(), key=lambda kv: kv[1][1])
    return award(L("Sezono sandoris", "Deal of the season"), pname, num(total),
                 f"{team_names.get(tid, '')} · {L('taškai nuo įsigijimo', 'points since acquired')}", "🤝",
                 player_id=pid, info=info)


def draft_awards(meta, finished, team_names):
    """Awards about the draft and the moves since: busts, steals, drops and roster value."""
    rec = gql(Q_DRAFT, {"id": meta["id"]}, ttl=SETTLED_TTL)["fantasyLeagueRecordFromClient"] or {}
    picks = [p for p in sorted(((rec.get("draft") or {}).get("picks") or []), key=lambda p: p["id"]) if p.get("player")]
    if not picks or not finished:
        return []
    per_round = len(team_names) or 8
    drafted = {p["player"]["id"]: (i + 1, p["fantasyTeamId"]) for i, p in enumerate(picks)}
    pmap = players_by_ids(meta, list(drafted), meta["latestRound"], meta["currentRound"])
    avg = lambda pid: (pmap.get(pid) or {}).get("avgPts") or 0  # noqa: E731
    games = lambda pid: (pmap.get(pid) or {}).get("gamesPlayed") or 0  # noqa: E731
    name = lambda pid: (pmap.get(pid) or {}).get("name") or "?"  # noqa: E731
    team = lambda tid: team_names.get(tid, "?")  # noqa: E731
    pick_word = lambda n: L(f"{n}-as pasirinkimas", f"pick #{n}")  # noqa: E731
    own = owners(meta, meta["currentRound"])
    holder = lambda pid: ((own.get(pid) or {}).get("team") or {}).get("title") or L("laisvasis agentas", "free agent")  # noqa: E731
    cards = []

    # Biggest bust: early pick furthest below the average of its own draft round.
    early = picks[:3 * per_round]
    round_avg = {}
    for i, p in enumerate(early):
        round_avg.setdefault(i // per_round, []).append(avg(p["player"]["id"]))
    busts = [(sum(round_avg[i // per_round]) / len(round_avg[i // per_round]) - avg(p["player"]["id"]), i, p)
             for i, p in enumerate(early)]
    gap, i, p = max(busts, key=lambda x: x[0])
    pid = p["player"]["id"]
    expected = gap + avg(pid)
    cards.append(award(
        L("Didžiausias nusivylimas", "Biggest bust"), name(pid), f"{num(avg(pid))} FP",
        f"{pick_word(i + 1)} ({team(p['fantasyTeamId'])}) · {L('rato vidurkis', 'round average')} {num(round(expected, 1))}",
        "📉", player_id=pid,
        info=L("Iš pirmųjų trijų drafto ratų: žaidėjas, kurio vidutiniai FP labiausiai atsilieka nuo to paties "
               "drafto rato pasirinkimų vidurkio. Dar nežaidę skaičiuojami kaip 0.",
               "From the first three draft rounds: the player whose average FP is furthest below the "
               "average of the picks in the same draft round. Players who have not played count as 0.")))

    # Best undrafted player (a regular: at least half of the finished rounds played).
    min_games = max(1, (len(finished) + 1) // 2)
    pool = players(meta, meta["latestRound"], meta["currentRound"])
    undrafted = [v for v in pool.values() if v["id"] not in drafted and (v["gamesPlayed"] or 0) >= min_games and v["avgPts"]]
    info = L(f"Žaidėjas, kurio drafte niekas nepasirinko. Didžiausi vidutiniai FP tarp sužaidusių bent {min_games} rungt.",
             f"A player nobody picked in the draft. Highest average FP among players with at least {min_games} games.")
    if undrafted:
        best = max(undrafted, key=lambda v: (v["avgPts"], v["gamesPlayed"]))
        cards.append(award(L("Geriausias nedraftuotas", "Best undrafted player"), best["name"], f"{num(best['avgPts'])} FP",
                           f"{L('dabar', 'now')}: {holder(best['id'])}", "💎", player_id=best["id"], info=info))
    else:
        cards.append(no_award(L("Geriausias nedraftuotas", "Best undrafted player"), L("dar nėra", "none yet"), "💎", info=info))

    # Drops: players a team let go in a free-agent move.
    drops = []
    for rnd, ts in raw_transfers(meta):
        for t in ts:
            tid = ((t.get("offer") or {}).get("fantasyTeam") or {}).get("id")
            if t.get("type") != "team" and tid:
                drops += [(rnd, t.get("updatedAt") or "", tid, pid) for pid in moved_players(t.get("offer"))]
    none_yet = L("atsiras po pirmųjų išmetimų", "appears after the first drops")
    before = lambda r: L(f"prieš {r + 1} turą", f"before round {r + 1}")  # noqa: E731
    drop_titles = (L("Geriausias išmestas", "Best player who was dropped"),
                   L("Labiausiai gailimas išmetimas", "Most regrettable drop"))
    drop_info = (
        L("Iš visų išmestų žaidėjų: didžiausi šio sezono vidutiniai FP.",
          "Of all dropped players: the highest average FP this season."),
        L("Kiek FP žaidėjas surinko baigtuose turuose po to, kai komanda jį išmetė. Laimi daugiausia surinkęs.",
          "FP the player scored in finished rounds after the team let the player go. The most wins."))
    if not drops:
        cards += [no_award(title, none_yet, icon, info=info) for title, icon, info in zip(drop_titles, ("🗑️", "😬"), drop_info)]
    else:
        rnd, _, tid, pid = max(drops, key=lambda d: avg(d[3]))
        cards.append(award(drop_titles[0], name(pid), f"{num(avg(pid))} FP",
                           f"{L('išmetė', 'dropped by')} {team(tid)} {before(rnd)} · {L('dabar', 'now')}: {holder(pid)}",
                           "🗑️", player_id=pid, info=drop_info[0]))
        after = {}
        for r in finished:
            round_pts = {v["id"]: v.get("roundPts") or 0 for v in players(meta, r, r).values()}
            for d in drops:
                if r >= d[0]:
                    after[d] = after.get(d, 0) + round_pts.get(d[3], 0)
        rnd, _, tid, pid = max(drops, key=lambda d: after.get(d, 0))
        cards.append(award(drop_titles[1], team(tid), f"{num(after.get((rnd, _, tid, pid), 0))} FP",
                           f"{L('išmetė', 'dropped')} {name(pid)} {before(rnd)}", "😬", team_id=tid, info=drop_info[1]))

    # Best draft-day roster: season FP of everyone a team drafted, wherever they play now.
    by_team = {}
    for pid, (_, tid) in drafted.items():
        by_team[tid] = by_team.get(tid, 0) + avg(pid) * games(pid)
    tid, total = max(by_team.items(), key=lambda kv: kv[1])
    cards.append(award(L("Geriausia drafto sudėtis", "Best draft-day roster"), team(tid), f"{num(round(total, 2))} FP",
                       L("drafte pasirinktų žaidėjų taškai šį sezoną", "season FP of the players drafted"), "📋",
                       team_id=tid,
                       info=L("Visų komandos drafte pasirinktų žaidėjų šio sezono fantasy taškų suma, nesvarbu, "
                              "ar jie vis dar komandoje.",
                              "Season fantasy points of every player the team drafted, whether or not they are "
                              "still on the team.")))

    # Best current roster versus the draft-day one (sum of average FP).
    current = {t: sum(avg(p["id"]) for p in lu["players"]) for t, lu in lineups(meta).items()}
    original = {t: sum(avg(pid) for pid, (_, owner) in drafted.items() if owner == t) for t in current}
    diff = {t: current[t] - original.get(t, 0) for t in current}
    info = L("Dabartinės sudėties žaidėjų vidutinių FP suma minus drafto sudėties suma: kiek komandai padėjo "
             "perėjimai ir mainai.",
             "Sum of average FP of the current roster minus that of the draft-day roster: how much the "
             "moves and trades helped.")
    if any(abs(v) > 0.01 for v in diff.values()):
        tid = max(diff, key=diff.get)
        cards.append(award(L("Labiausiai pagerėjusi sudėtis", "Best current vs original roster"), team(tid),
                           f"{'+' if diff[tid] >= 0 else ''}{num(round(diff[tid], 1))} FP",
                           L(f"dabar {num(round(current[tid], 1))}, drafte {num(round(original[tid], 1))}",
                             f"now {num(round(current[tid], 1))}, at the draft {num(round(original[tid], 1))}"),
                           "📈", team_id=tid, info=info))
    else:
        cards.append(no_award(L("Labiausiai pagerėjusi sudėtis", "Best current vs original roster"),
                              L("atsiras po pirmųjų perėjimų", "appears after the first transfers"), "📈", info=info))
    return cards


def season_records(meta, breakdowns, team_names, streaks, totals):
    cards = []
    rows = [(bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]]
    opponent = {}
    for bd in breakdowns:
        for g in bd["games"]:
            opponent[(g["winner"]["id"], bd["round"])] = g["loser"]["title"]
            opponent[(g["loser"]["id"], bd["round"])] = g["winner"]["title"]
    vs = lambda t, r: f", {L('prieš', 'vs')} {opponent[(t, r)]}" if (t, r) in opponent else ""  # noqa: E731
    if rows:
        hi, lo = max(rows), min(rows)
        cards.append(award(L("Daugiausia taškų per turą", "Most points in a round"), team_names.get(hi[1], ""), num(hi[0]),
                           f"{rnd_word(hi[2])}{vs(hi[1], hi[2])}", team_id=hi[1]))
        cards.append(award(L("Mažiausiai taškų per turą", "Fewest points in a round"), team_names.get(lo[1], ""), num(lo[0]),
                           f"{rnd_word(lo[2])}{vs(lo[1], lo[2])}", team_id=lo[1]))
    games = [(g, bd["round"]) for bd in breakdowns for g in bd["games"] if not g["tie"]]
    if games:
        big = max(games, key=lambda x: x[0]["margin"])
        small = min(games, key=lambda x: x[0]["margin"])
        unlucky = max(games, key=lambda x: x[0]["ls"])
        lucky = min(games, key=lambda x: x[0]["ws"])
        cards += [
            award(L("Didžiausia pergalė", "Biggest win"), big[0]["winner"]["title"], f"+{num(big[0]['margin'])}",
                  f"{num(big[0]['ws'])} : {num(big[0]['ls'])} {L('prieš', 'vs')} {big[0]['loser']['title']}, "
                  f"{rnd_word(big[1])}",
                  team_id=big[0]["winner"]["id"]),
            award(L("Mažiausias skirtumas", "Closest win"), small[0]["winner"]["title"], f"+{num(small[0]['margin'])}",
                  f"{num(small[0]['ws'])} : {num(small[0]['ls'])} {L('prieš', 'vs')} {small[0]['loser']['title']}, "
                  f"{rnd_word(small[1])}",
                  team_id=small[0]["winner"]["id"]),
            award(L("Nelaimingiausias pralaimėjimas", "Unluckiest loss"), unlucky[0]["loser"]["title"], num(unlucky[0]["ls"]),
                  L(f"Pralaimėjo {unlucky[0]['winner']['title']}", f"Lost to {unlucky[0]['winner']['title']}")
                  + f", {rnd_word(unlucky[1])}", team_id=unlucky[0]["loser"]["id"]),
            award(L("Laimingiausia pergalė", "Luckiest win"), lucky[0]["winner"]["title"], num(lucky[0]["ws"]),
                  L(f"Laimėjo prieš {lucky[0]['loser']['title']}", f"Beat {lucky[0]['loser']['title']}")
                  + f", {rnd_word(lucky[1])}", team_id=lucky[0]["winner"]["id"]),
        ]
        for idx, title, sub in ((0, L("Ilgiausia pergalių serija", "Longest winning streak"),
                                 L("pergalės iš eilės", "wins in a row")),
                                (1, L("Ilgiausia pralaimėjimų serija", "Longest losing streak"),
                                 L("pralaimėjimai iš eilės", "losses in a row"))):
            longest = {t: s[idx] for t, s in streaks.items()}
            top = max(longest.values(), default=0)
            holders = [t for t, v in longest.items() if v == top and top > 0]
            if not holders:
                cards.append(no_award(title, sub))
            elif len(holders) == 1:
                cards.append(award(title, team_names.get(holders[0], ""), str(top), sub, team_id=holders[0]))
            else:
                cards.append(award(title, teams_word(len(holders)), str(top), f"{sub} ({L('žr. lentelę', 'see table')})"))
    if totals:
        played = len(breakdowns)
        most = max(totals, key=totals.get)
        least = min(totals, key=totals.get)
        cards.append(award(L("Daugiausia taškų per sezoną", "Most season points"), team_names.get(most, ""), num(totals[most]),
                           L(f"Vidurkis {num(totals[most] / played)} per turą", f"Average {num(totals[most] / played)} per round"), team_id=most))
        cards.append(award(L("Mažiausiai taškų per sezoną", "Fewest season points"), team_names.get(least, ""), num(totals[least]),
                           L(f"Vidurkis {num(totals[least] / played)} per turą", f"Average {num(totals[least] / played)} per round"), team_id=least))
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
    breakdowns = pool_map(lambda r: round_breakdown(meta, r), finished)
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
        "draftAwards": draft_awards(meta, finished, team_names),
        "records": season_records(meta, breakdowns, team_names, streaks, totals),
        "form": form,
        "missingLineups": [bd["round"] for bd in breakdowns if not bd["lineups"]],
        "partialLineups": [{"round": bd["round"],
                            "teams": sorted(team_names.get(t, t) for t in bd["scores"] if t not in bd["lineups"])}
                           for bd in breakdowns if bd["lineups"] and set(bd["scores"]) - set(bd["lineups"])],
    }


def leagues_payload():
    def card(entry):
        try:
            meta = league_meta(entry["id"])
            shown, rows = standings(meta)
            mine = next((r for r in rows if r["team"]["id"] == entry.get("myTeamId")), None)
            return {"league": meta, "round": shown, "leader": rows[0] if rows else None,
                    "mine": mine, "teams": len(rows), "table": rows}
        except (UpstreamError, NotFound) as exc:
            return {"league": {"id": entry["id"], "title": entry.get("title") or entry["id"]}, "error": str(exc)}
    return {"leagues": pool_map(card, load_config())}


LEAGUE_ID_RE = re.compile(r"([0-9a-f]{24})")


def add_league(text):
    m = LEAGUE_ID_RE.search(text or "")
    if not m:
        raise ValueError(L("Nuorodoje nerastas lygos ID", "No league ID found in the link"))
    fid = m.group(1)
    meta = league_meta(fid)  # validates the league exists
    with _config_lock:
        entries = load_config()
        if any(e["id"] == fid for e in entries):
            raise ValueError(L("Ši lyga jau pridėta", "This league is already added"))
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
            raise NotFound(L("Lyga nesekama", "League is not tracked"))
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
    ".woff2": "font/woff2",
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
        LANG.set("en" if query.get("lang", [""])[0] == "en" else "lt")
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

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        m = re.fullmatch(rf"/go/proballers/{ID}/{ID}", urlparse(self.path).path)
        if m:
            try:
                self._redirect(proballers_redirect(m[1], m[2]))
            except (NotFound, UpstreamError) as exc:
                self._send(404, {"error": str(exc)})
            return
        routes = [
            (r"/api/leagues", lambda m, q: leagues_payload()),
            (rf"/api/league/{ID}/standings", lambda m, q: standings_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/rounds", lambda m, q: rounds_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/free-agents", lambda m, q: players_payload(m[1], "free")),
            (rf"/api/league/{ID}/players", lambda m, q: players_payload(m[1], "all")),
            (rf"/api/league/{ID}/games", lambda m, q: games_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/records", lambda m, q: records_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/draft", lambda m, q: draft_payload(m[1])),
            (rf"/api/league/{ID}/injuries", lambda m, q: injuries_payload(m[1])),
            (rf"/api/league/{ID}/transfers", lambda m, q: transfers_payload(m[1])),
            (rf"/api/league/{ID}/team/{ID}", lambda m, q: team_payload(m[1], m[2], _round_param(q))),
            (rf"/api/league/{ID}/player/{ID}", lambda m, q: player_payload(m[1], m[2])),
        ]
        if self._dispatch(routes):
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})
            return
        self._serve_static(path)

    def do_POST(self):
        routes = [
            (r"/api/leagues", lambda m, q: {"league": add_league(self._json_body().get("url"))}),
            (rf"/api/league/{ID}/my-team",
             lambda m, q: set_my_team(m[1], self._json_body().get("teamId")) or {"ok": True}),
        ]
        if not self._dispatch(routes):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})

    def do_DELETE(self):
        routes = [(rf"/api/leagues/{ID}", lambda m, q: remove_league(m[1]) or {"ok": True})]
        if not self._dispatch(routes):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})

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
