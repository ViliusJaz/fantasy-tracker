"""Winner predictions for the game previews: backend/predict.py's model over this season's
EuroLeague results (backend/sources/euroleague.py). Injuries are not an input: in the backtests
they added nothing the ratings did not already know (the previews still list them).

The model starts from the ratings it ended last season with (backend/model/euroleague.json),
replays every game of this season so far, then scores each upcoming game.
"""
import threading
import time

from backend import log, net, predict
from backend.i18n import L
from backend.sources import euroleague
from backend.util import ascii_slug

LOG = log.get("analytics")
_lock = threading.Lock()
_memo = {}


def season_code(meta):
    return f"E{meta.get('seasonYear')}"


def _state(meta):
    """(state after this season's finished games, this season's games, params) or None."""
    params = predict.load_params()
    if not params or not meta.get("seasonYear"):
        return None
    season = season_code(meta)
    with _lock:
        hit = _memo.get(season)
        if hit and hit[0] > time.time():
            return hit[1]
    try:
        games = euroleague.season_games(season)
    except net.FetchError as exc:
        LOG.warning("EuroLeague results unavailable, no game predictions: %s", exc)
        return None
    state = predict.State(params)
    for code, rating in params["ratings"]["teams"].items():
        state.team(code).rating = rating
    for code, prior in (params.get("priors") or {}).items():
        state.team(code).prior = dict(prior)
    state.season = params["ratings"]["after"]
    state.start_season(season)
    for g in games:
        if g["hs"] is not None:
            state.update(g)
    result = (state, games, params)
    with _lock:
        _memo[season] = (time.time() + 600, result)
    return result


def _tokens(name):
    noise = {"fc", "bc", "basket", "basketball", "club", "de", "the"}
    return set(ascii_slug(name or "").split("-")) - noise - {""}


def club_codes(clubs, games):
    """BasketNews club abbreviation -> EuroLeague club code, matched on the names."""
    names = {}
    for g in games:
        names.update(g.get("names") or {})
    out = {}
    for abbr, club in clubs.items():
        mine = _tokens(club.get("nameEn")) | _tokens(club.get("fullName"))
        best, score = None, 0
        for code, name in names.items():
            sc = 2 * len(mine & _tokens(name)) + (3 if ascii_slug(abbr) == ascii_slug(code) else 0)
            if sc > score:
                best, score = code, sc
        if best:
            out[abbr] = best
    return out


FACTOR_TEXT = {
    "home": ("Namų aikštė", "Home court"),
    "elo": ("Bendras komandos reitingas", "Overall team rating"),
    "form": ("Pastarųjų rungtynių forma", "Recent form"),
    "rest": ("Poilsio dienos", "Days of rest"),
    "b2b": ("Antros rungtynės per tris dienas", "Second game in three days"),
    "absent": ("Traumuoti ir nežaisiantys žaidėjai", "Injured and missing players"),
    "matchup": ("Puolimas prieš varžovo gynybą", "Offense against the other side's defense"),
    "vs_type": ("Kaip sekasi prieš tokio lygio varžovus", "Record against opponents of this level"),
}


def predict_games(meta, previews, clubs):
    """{game key: prediction} for upcoming BasketNews games [(key, home abbr, away abbr, ISO time)]."""
    ready = _state(meta)
    if not ready:
        return {}
    state, games, params = ready
    codes = club_codes(clubs, games)
    by_teams = {(g["home"], g["away"]): g for g in games if g["hs"] is None}
    out = {}
    for key, home, away, at in previews:
        h, a = codes.get(home), codes.get(away)
        if not h or not a:
            continue
        game = by_teams.get((h, a)) or {"home": h, "away": a, "date": at, "neutral": False}
        game = {**game, "date": game.get("date") or at}
        feats = state.features(game, absent={}, roster={})
        p = state.probability(feats)
        factors = []
        for name, weight in predict.explain(feats, params, h, a)[:3]:
            if abs(weight) < 0.05:
                continue
            factors.append({"key": name, "text": L(*FACTOR_TEXT.get(name, (name, name))),
                            "toward": home if weight > 0 else away, "weight": round(abs(weight), 2)})
        out[key] = {"homeWin": round(p, 3), "winner": home if p >= 0.5 else away, "prob": round(max(p, 1 - p), 3),
                    "factors": factors, "accuracy": params.get("backtest", {}).get("accuracy")}
    return out
