"""Round states: finished, live or upcoming, and how long their data may be cached."""
from backend.config import LIVE_TTL, SETTLED_TTL


def is_live(meta, rnd):
    return rnd is not None and meta["roundStarted"] and rnd == meta["currentRound"]


def round_state(meta, rnd):
    if rnd < meta["currentRound"]:
        return "finished"
    return "live" if is_live(meta, rnd) else "upcoming"


def round_ttl(meta, rnd):
    return LIVE_TTL if rnd >= meta["currentRound"] - 1 else SETTLED_TTL
