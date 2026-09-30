"""BasketNews advanced statistics (basketnews.com/advanced-stats): player rows per season or round,
and team ratings with the strengths / weaknesses summaries."""
import http.client
import json
import threading
import time
import urllib.parse
import urllib.request

from backend import log
from backend.config import BROWSER_UA
from backend.i18n import LANG
from backend.net import read_body


LOG = log.get("fetch")

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
        LOG.warning("advanced stats unavailable: %s", exc)
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


def link_advanced_rows(table, views, line_key, games=lambda v: v["gamesPlayed"] or 0):
    """BasketNews' advanced stats list a few players under a different id than the fantasy
    game. Pair each such row with the player missing one by identical box-score totals and
    add the player's id as a second key for the same row."""
    claimed = {str(v["bnId"]) for v in views.values() if v.get("bnId")}
    free = {k: r for k, r in table.items() if k not in claimed}
    for v in views.values():
        bn, line, gp = str(v.get("bnId") or ""), v.get(line_key), games(v)
        if not free or not bn or bn in table or not line or not gp:
            continue
        def same(r):
            near = lambda key, total, tol: abs((adv_value(r, key) or 0) - total) <= tol  # noqa: E731
            return (near("points", line["pts"] * gp, 0.6 * gp) and near("rebounds", line["reb"] * gp, 0.6 * gp)
                    and near("assists", line["ast"] * gp, 0.6 * gp) and near("time_played", line["min"] * 60 * gp, 90 * gp))
        matches = [k for k, r in free.items() if same(r)]
        if len(matches) == 1:
            table[bn] = free.pop(matches[0])


def unique_rows(table):
    """Advanced rows without the alias keys added by link_advanced_rows."""
    return list({id(r): r for r in table.values()}.values())


TEAM_ADV_URL = "https://basketnews.com/advanced-stats/team-profile/overview.json"

_team_adv_cache = {}

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
        LOG.warning("team advanced stats unavailable: %s", exc)
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
