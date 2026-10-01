"""Live box scores from the EuroLeague's live feed, for games BasketNews has not scored yet
(it posts players' stats and fantasy points only after the games).

    https://live.euroleague.net/api/Header?gamecode=<n>&seasoncode=E<year>    score, quarter, clock
    https://live.euroleague.net/api/Boxscore?gamecode=<n>&seasoncode=E<year>  every player's line

Read by each build (every 15 minutes), only for games of a live round that have started. A
finished game no longer changes, so its answers are kept in the persistent cache for two days.
Before tip-off the feed answers with an empty body.
"""
import json
import threading
import time
import urllib.request

from backend import cache, log, net
from backend.util import SingleFlight

LOG = log.get("fetch")
HEADER_URL = "https://live.euroleague.net/api/Header?gamecode={code}&seasoncode={season}"
BOX_URL = "https://live.euroleague.net/api/Boxscore?gamecode={code}&seasoncode={season}"
UA = "fantasy-tracker (personal EuroLeague fantasy tracker)"
FINAL_KEEP = 2 * 24 * 3600
TTL = 120  # one build asks for the same game many times within seconds

_flights = SingleFlight()
_memo = {}
_lock = threading.Lock()


def _fetch(url):
    """The answer as JSON, or None for the empty body of a game that has not started."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    body = net.fetch(req, "euroleague-live", timeout=30)
    return json.loads(body) if body.strip() else None


def seconds(text):
    """'15:26' -> 926; 'DNP' or nothing -> None."""
    try:
        m, s = (text or "").strip().split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def player_line(p):
    """A live player row as the tracker's box-score line (backend/sources/basketnews.stat_line)."""
    sec = seconds(p.get("Minutes"))
    if sec is None:
        return None
    get = lambda k: p.get(k) or 0  # noqa: E731
    return {
        "min": round(sec / 60, 1), "pts": get("Points"), "reb": get("OffensiveRebounds") + get("DefensiveRebounds"),
        "oreb": get("OffensiveRebounds"), "dreb": get("DefensiveRebounds"), "ast": get("Assistances"),
        "stl": get("Steals"), "blk": get("BlocksFavour"), "tov": get("Turnovers"), "pf": get("FoulsCommited"),
        "eff": get("Valuation"), "p2m": get("FieldGoalsMade2"), "p2a": get("FieldGoalsAttempted2"),
        "p3m": get("FieldGoalsMade3"), "p3a": get("FieldGoalsAttempted3"), "ftm": get("FreeThrowsMade"),
        "fta": get("FreeThrowsAttempted"), "sec": sec, "ba": get("BlocksAgainst"), "fd": get("FoulsReceived"),
        "pm": get("Plusminus"),
    }


def parse(header, box):
    """{"live", "final", "quarter", "clock", "score": {code: points}, "players": {code: [{number, name, line}]}},
    or None before tip-off."""
    if not header or not box or not header.get("CodeTeamA"):
        return None
    a, b = header["CodeTeamA"].strip(), header["CodeTeamB"].strip()
    score = {a: int(header.get("ScoreA") or 0), b: int(header.get("ScoreB") or 0)}
    played = seconds(header.get("GameTime")) or 0
    live = bool(header.get("Live"))
    players = {}
    for team in box.get("Stats") or []:
        for p in team.get("PlayersStats") or []:
            code = (p.get("Team") or "").strip()
            line = player_line(p)
            if code and line:
                players.setdefault(code, []).append({"number": str(p.get("Dorsal") or "").strip(),
                                                     "name": (p.get("Player") or "").strip(), "line": line})
    return {"live": live, "final": not live and played >= 40 * 60 and any(score.values()),
            "quarter": header.get("Quarter") or None, "clock": (header.get("RemainingPartialTime") or "").strip() or None,
            "score": score, "players": players}


def game(season, code):
    """The live state of one game (see parse), or None when it has not started or the feed fails."""
    key = (season, code)
    with _lock:
        hit = _memo.get(key)
        if hit and hit[0] > time.time():
            return hit[1]

    def load():
        urls = HEADER_URL.format(code=code, season=season), BOX_URL.format(code=code, season=season)
        stored = cache.get(f"euroleague-live {season} {code}")
        if stored is not None:  # a finished game: the snapshot of this build should still contain it
            for url, part in zip(urls, ("header", "box")):
                net.remember("GET", url, None, json.dumps(stored[part]).encode(), content_type="application/json")
            result = parse(stored["header"], stored["box"])
        else:
            try:
                header, box = _fetch(urls[0]), _fetch(urls[1])
            except (net.FetchError, ValueError) as exc:
                LOG.warning("EuroLeague live game %s %s unavailable: %s", season, code, exc)
                return hit[1] if hit else None
            result = parse(header, box)
            if result and result["final"]:
                cache.put(f"euroleague-live {season} {code}", "euroleague-live", {"header": header, "box": box}, FINAL_KEEP)
        with _lock:
            _memo[key] = (time.time() + TTL, result)
        return result
    return _flights.do(key, load)
