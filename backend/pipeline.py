"""The steps shared by export.py and server.py: fetch what a league needs, check it, then
store what is worth keeping (lineups, injury changes) in the history."""
from backend import history, injuries, log, validation
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


def store(report=None):
    """Write the queued lineups and injury changes, except what validation rejected. A fresh
    injury report is checked here, whoever fetched it, so no code path can record an
    unreadable one. Returns {"lineups": n, "injuries": n} files changed."""
    report = report if report is not None else validation.Report()
    pending = history.pending()
    if pending["injuries"]:
        known = history.injury_log()
        for key, entries in pending["injuries"]:
            validation.check_injury_report(report, entries, known)
            if report.blocked("injuries") and key:
                injuries.use_last_known(key)
    history.discard(lineups=report.blocked("lineups"), injuries=report.blocked("injuries"))
    return history.flush()


def refresh_tracked_leagues():
    """Keep lineup snapshots and the injury log current for every tracked league."""
    for entry in load_config():
        try:
            observe(league_meta(entry["id"]))
        except (UpstreamError, NotFound) as exc:
            LOG.warning("league %s not refreshed: %s", entry["id"], exc)
    store()
