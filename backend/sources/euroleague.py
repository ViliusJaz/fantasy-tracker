"""EuroLeague's own results and box scores, for the game-winner model (backend/predict.py).

    games:      api-live.euroleague.net/v2/competitions/E/seasons/E<year>/games
    box scores: api-live.euroleague.net/v3/competitions/E/seasons/E<year>/games/<n>/stats
                (older downloads may be live.euroleague.net/api/Boxscore answers: both are read)

Both are public. A finished game's box score never changes, so it is kept in the persistent
cache for a year and downloaded once per device. The same normalisation serves the backtests
(tools/predict_research.py), so the model sees identical data there and on the site.
"""
import time
import urllib.request

from backend import cache, log, net
from backend.util import SingleFlight

LOG = log.get("fetch")
GAMES_URL = "https://api-live.euroleague.net/v2/competitions/E/seasons/{season}/games"
BOX_URL = "https://api-live.euroleague.net/v3/competitions/E/seasons/{season}/games/{code}/stats"
UA = "fantasy-tracker (personal EuroLeague fantasy tracker)"
YEAR = 365 * 24 * 3600
_flights = SingleFlight()
_games = {}


def _get(url, keep=0):
    stored = cache.get(url) if keep else None
    if stored is not None:
        return stored
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"})
    data = net.fetch(req, "euroleague", timeout=30, as_json=True)
    if keep:
        cache.put(url, "euroleague", data, keep)
    return data


def minutes(text):
    try:
        m, s = (text or "").split(":")
        return int(m) + int(s) / 60
    except ValueError:
        return 0.0


def team_box(totr):
    return {"pts": totr["Points"], "fga2": totr["FieldGoalsAttempted2"], "fga3": totr["FieldGoalsAttempted3"],
            "fta": totr["FreeThrowsAttempted"], "oreb": totr["OffensiveRebounds"], "tov": totr["Turnovers"]}


def _v3_side(side):
    t = side["total"]
    box = {"pts": t["points"], "fga2": t["fieldGoalsAttempted2"], "fga3": t["fieldGoalsAttempted3"],
           "fta": t["freeThrowsAttempted"], "oreb": t["offensiveRebounds"], "tov": t["turnovers"]}
    players = [{"id": p["player"]["person"]["code"], "min": (p["stats"].get("timePlayed") or 0) / 60,
                "pir": p["stats"].get("valuation") or 0} for p in side.get("players") or []]
    return box, players


def normalize(raw, season, box=None):
    """A game in the model's shape (see backend/predict.py)."""
    home, away = raw["local"]["club"]["code"], raw["road"]["club"]["code"]
    game = {"id": raw["identifier"], "season": season, "phase": raw["phaseType"]["code"], "round": raw["round"],
            "date": raw["utcDate"], "home": home, "away": away, "neutral": bool(raw.get("isNeutralVenue")),
            "hs": raw["local"]["score"] if raw.get("played") else None,
            "as": raw["road"]["score"] if raw.get("played") else None, "box": None, "players": None,
            "names": {home: raw["local"]["club"]["name"], away: raw["road"]["club"]["name"]},
            "audience": raw.get("audience") if raw.get("played") else None}
    if box and "local" in box and "road" in box:
        (hb, hp), (ab, ap) = _v3_side(box["local"]), _v3_side(box["road"])
        game["box"], game["players"] = {home: hb, away: ab}, {home: hp, away: ap}
    elif box:
        by_code, players = {}, {}
        for st in box.get("Stats") or []:
            plist = [p for p in st["PlayersStats"] if (p.get("Team") or "").strip()]
            if not plist:
                continue
            code = plist[0]["Team"].strip()
            by_code[code] = team_box(st["totr"])
            players[code] = [{"id": p["Player_ID"].strip().lstrip("P"), "min": minutes(p["Minutes"]),
                              "pir": p["Valuation"] or 0} for p in plist]
        if home in by_code and away in by_code:
            game["box"], game["players"] = by_code, players
    return game


def season_games(season):
    """Every game of a season, finished ones with their box score, in date order. Box scores
    come from the persistent cache when they were seen before."""
    def load():
        raw = _get(GAMES_URL.format(season=season))["data"]
        games = []
        for g in raw:
            box = None
            if g.get("played"):
                try:
                    box = _get(BOX_URL.format(code=g["gameCode"], season=season), keep=YEAR)
                except net.FetchError as exc:
                    LOG.warning("EuroLeague box score %s %s unavailable: %s", season, g["gameCode"], exc)
            games.append(normalize(g, season, box))
        games.sort(key=lambda x: x["date"])
        return games
    hit = _games.get(season)
    if hit is None or hit[0] < time.time():
        _games[season] = (time.time() + 600, _flights.do(season, load))  # the local server refreshes
    return _games[season][1]
