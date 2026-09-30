"""The steps shared by export.py and server.py: fetch what a league needs, then store what
is worth keeping (lineups, injury changes) in the history."""
from backend import history, log
from backend.config import load_config
from backend.errors import NotFound, UpstreamError
from backend.injuries import injury_report
from backend.league import league_meta, lineups

LOG = log.get("fetch")


def observe(meta):
    """Fetch the things only the live API knows (current lineups, the injury report); the
    history module queues them until store()."""
    lineups(meta)
    injury_report(meta)


def store():
    """Write the queued lineups and injury changes. Returns {"lineups": n, "injuries": n} files changed."""
    return history.flush()


def refresh_tracked_leagues():
    """Keep lineup snapshots and the injury log current for every tracked league."""
    for entry in load_config():
        try:
            observe(league_meta(entry["id"]))
        except (UpstreamError, NotFound) as exc:
            LOG.warning("league %s not refreshed: %s", entry["id"], exc)
    store()
