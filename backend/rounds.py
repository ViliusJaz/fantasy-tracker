"""Round states: finished, live or upcoming, and how long their data may be cached."""
from backend.config import LIVE_TTL, SETTLED_TTL


def is_live(meta, rnd):
    return rnd is not None and bool(meta.get("roundStarted")) and rnd == meta["currentRound"]


def round_state(meta, rnd):
    if rnd < meta["currentRound"]:
        return "finished"
    return "live" if is_live(meta, rnd) else "upcoming"


def round_ttl(meta, rnd):
    return LIVE_TTL if rnd >= meta["currentRound"] - 1 else SETTLED_TTL


DAY = 24 * 60 * 60


def keep_for(meta, rnd):
    """Seconds an answer about round `rnd` may be kept between runs (backend/cache.py).
    Settled rounds (two or more back: stat corrections come within a day or two) a week;
    rounds two or more ahead (their schedule) a few hours; the previous, current and next
    round never -- they are live."""
    cur = meta["currentRound"]
    if rnd <= cur - 2:
        return 7 * DAY
    if rnd >= cur + 2:
        return 3 * 60 * 60
    return 0
