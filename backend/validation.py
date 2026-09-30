"""Sanity checks on fetched data, before anything is stored or published.

The worst failure is not a crash but a successful publish of obviously wrong data, so every
build is checked first. Three levels:

  WARNING  logged and counted in health.json; nothing else changes
  ERROR    that source's new data is not stored and the pages use its last good data
           (the injury report falls back to the injury log); the site is still published,
           marked "degraded"
  FATAL    nothing from the run is stored or published; the previous site stays online

Checks use loose sanity limits plus the last successful build of the same season (its
numbers are kept in health.json), never hard-coded facts about a season.
"""
import threading

from backend import log

LOG = log.get("validation")

WARNING, ERROR, FATAL = "warning", "error", "fatal"
_LOG_LEVEL = {WARNING: 30, ERROR: 40, FATAL: 50}

MIN_PLAYERS = 100            # a EuroLeague season lists 300+
MIN_SHARE_OF_LAST = 0.6      # players / pages may not drop below 60% / 80% of the last good build
MIN_PAGE_SHARE = 0.8
MAX_FAILED_PAGES = 0.05
MAX_NO_BN_ID = 0.10          # players without a basketnews.com id (injuries / advanced stats need it)
CLUBS = (16, 24)             # EuroLeague has 18-20 clubs
MIN_INJURY_SHARE = 0.4       # injury report entries compared with the players currently injured
MAX_POINTS_DROP = 0.10       # league points total may not shrink by more than this within a season
ROSTER = (5, 16)             # players on a fantasy roster


class Fatal(Exception):
    """Raised to stop a build; the report says why."""


class Report:
    def __init__(self):
        self.issues = []
        self._lock = threading.Lock()

    def add(self, level, source, code, message):
        with self._lock:
            self.issues.append({"level": level, "source": source, "code": code, "message": message})
        LOG.log(_LOG_LEVEL[level], "%s %s: %s", level.upper(), source, message)

    def count(self, level):
        return sum(1 for i in self.issues if i["level"] == level)

    @property
    def fatal(self):
        return self.count(FATAL) > 0

    def blocked(self, source):
        """True when `source` has an ERROR or FATAL issue: its new data must not be stored."""
        return any(i["source"] == source and i["level"] in (ERROR, FATAL) for i in self.issues)

    def summary(self):
        return {"warnings": self.count(WARNING), "errors": self.count(ERROR), "fatal": self.count(FATAL)}


def _same_season(meta, base):
    return bool(base) and base.get("seasonYear") == meta.get("seasonYear") and base.get("id") == meta.get("id")


def check_meta(report, meta, base=None):
    src = f"league {meta.get('title') or meta.get('id')}"
    if meta.get("format") not in ("head_to_head", "classic"):
        report.add(FATAL, src, "format", f"unknown league format {meta.get('format')!r}")
    total = meta.get("totalRounds") or 0
    if not 1 <= total <= 80:
        report.add(FATAL, src, "total-rounds", f"season has {total} rounds")
    if not meta["firstRound"] <= meta["currentRound"] <= max(total, meta["firstRound"]):
        report.add(FATAL, src, "current-round", f"current round {meta['currentRound'] + 1} is outside the season "
                                                 f"({meta['firstRound'] + 1}-{total})")
    if _same_season(meta, base) and meta["currentRound"] < base.get("currentRound", 0):
        report.add(FATAL, src, "round-backwards", f"current round went back from {base['currentRound'] + 1} "
                                                  f"to {meta['currentRound'] + 1}")


def check_standings(report, meta, shown, rows, base=None):
    src = f"league {meta['title']}"
    if not rows:
        report.add(FATAL, src, "no-teams", "the standings list no teams")
        return
    if meta.get("teamsCount") and len(rows) != meta["teamsCount"]:
        report.add(FATAL, src, "team-count", f"standings list {len(rows)} teams, the league has {meta['teamsCount']}")
    if any(not (r.get("team") or {}).get("id") for r in rows):
        report.add(FATAL, src, "team-id", "a standings row has no team id")
    if sorted(r["position"] for r in rows) != list(range(1, len(rows) + 1)):
        report.add(WARNING, src, "positions", "table positions are not 1..n")
    if meta["format"] == "head_to_head" and shown is not None:
        played = {r["wins"] + r["losses"] + r["ties"] for r in rows}
        if len(played) > 1:
            report.add(WARNING, src, "games-played", f"teams have played different numbers of games: {sorted(played)}")
    if any(r["pointsRound"] > r["pointsTotal"] + 0.01 for r in rows if r["pointsTotal"] >= 0 and r["pointsRound"] >= 0):
        report.add(WARNING, src, "points", "a team scored more this round than in the whole season")
    total = sum(r["pointsTotal"] for r in rows)
    if (_same_season(meta, base) and shown is not None and shown >= (base.get("shownRound") or 0)
            and base.get("pointsTotal") and total < (1 - MAX_POINTS_DROP) * base["pointsTotal"]):
        report.add(FATAL, src, "points-dropped", f"league points total fell from {base['pointsTotal']:.0f} "
                                                 f"to {total:.0f} since the last good build")


def check_players(report, meta, pmap, base=None):
    src = "basketnews players"
    n = len(pmap)
    if n < MIN_PLAYERS:
        report.add(FATAL, src, "player-count", f"only {n} players (at least {MIN_PLAYERS} expected)")
        return
    if _same_season(meta, base) and base.get("players") and n < MIN_SHARE_OF_LAST * base["players"]:
        report.add(FATAL, src, "player-drop", f"{n} players, the last good build had {base['players']}")
    views = list(pmap.values())
    if any(not v.get("id") or not v.get("name") for v in views):
        report.add(ERROR, src, "player-id", "players without an id or a name")
    no_bn = sum(1 for v in views if not v.get("bnId"))
    if no_bn > MAX_NO_BN_ID * n:
        report.add(WARNING, src, "bn-id", f"{no_bn} of {n} players have no basketnews.com id")
    clubs = {(v.get("club") or {}).get("abbr") for v in views if v.get("club")}
    if not CLUBS[0] <= len(clubs) <= CLUBS[1]:
        report.add(WARNING, src, "clubs", f"{len(clubs)} clubs (expected {CLUBS[0]}-{CLUBS[1]})")
    with_games = sum(1 for v in views if v.get("gamesPlayed"))
    if (_same_season(meta, base) and base.get("playersWithGames", 0) >= MIN_PLAYERS
            and with_games < 0.5 * base["playersWithGames"]):
        report.add(FATAL, src, "season-stats", f"only {with_games} players have season stats, "
                                               f"the last good build had {base['playersWithGames']}")
    if meta["latestRound"] < meta["currentRound"]:  # the round shown is finished: it must have box scores
        played = [v for v in views if v.get("roundPlayed")]
        if not played:
            report.add(FATAL, src, "round-stats", f"nobody has stats for finished round {meta['latestRound'] + 1}")
        elif sum(1 for v in played if v.get("roundPts") is None) > 0.5 * len(played):
            report.add(FATAL, src, "round-points", f"most players of round {meta['latestRound'] + 1} have no fantasy points")


def check_lineups(report, meta, lineups, team_ids):
    """Lineups are ERROR-level: a bad fetch is not stored, the site still builds from the rest."""
    src = "lineups"
    if not team_ids:
        return
    missing = [t for t in team_ids if t not in lineups]
    if len(missing) > len(team_ids) / 2:
        report.add(ERROR, src, "missing", f"{meta['title']}: {len(missing)} of {len(team_ids)} teams have no lineup")
    odd = [t for t, lu in lineups.items() if not ROSTER[0] <= len(lu["players"]) <= ROSTER[1]]
    if odd:
        report.add(WARNING, src, "roster-size", f"{meta['title']}: {len(odd)} team(s) with an unusual roster size")
    # right after a round the API still shows it until managers set the next one: that is normal
    stale = [t for t, lu in lineups.items() if lu["round"] < meta["currentRound"] - 1]
    if stale:
        report.add(WARNING, src, "old-round", f"{meta['title']}: {len(stale)} lineup(s) are for an earlier round")


def check_injury_report(report, entries, log_players):
    """A report that cannot be right (empty while players are known to be injured, rows the
    parser cannot read) is an ERROR: the injury log is not updated from it."""
    src = "injuries"
    injured = sum(1 for p in log_players.values() if p["episodes"] and p["episodes"][-1]["end"] is None)
    n = len(entries)
    if n == 0 and injured >= 5:
        report.add(ERROR, src, "empty", f"the injury report is empty while {injured} players are injured")
        return
    if injured >= 10 and n < MIN_INJURY_SHARE * injured:
        report.add(ERROR, src, "shrunk", f"the injury report lists {n} players, {injured} are currently injured")
        return
    if n and sum(1 for e in entries if not e.get("bnId")) > 0.3 * n:
        report.add(ERROR, src, "unreadable", "most injury report rows have no player link (page layout changed?)")
        return
    if n and sum(1 for e in entries if e.get("status") == "other") > 0.3 * n:
        report.add(WARNING, src, "statuses", "many injury statuses are not recognised")


def check_pages(report, written, failed, base_pages=None, examples=()):
    src = "export"
    which = f": {'; '.join(examples)}" if examples else ""
    total = written + failed
    if not written:
        report.add(FATAL, src, "no-pages", "no page was written")
        return
    if failed > MAX_FAILED_PAGES * total:
        report.add(FATAL, src, "failed-pages", f"{failed} of {total} pages failed{which}")
    elif failed:
        report.add(WARNING, src, "failed-pages", f"{failed} of {total} pages failed{which}")
    if base_pages and written < MIN_PAGE_SHARE * base_pages:
        report.add(FATAL, src, "page-drop", f"{written} pages, the last good build had {base_pages}")


def league_metrics(meta, shown, rows, pmap):
    """What the next build compares itself with (kept in health.json)."""
    return {"id": meta["id"], "title": meta["title"], "seasonYear": meta.get("seasonYear"),
            "currentRound": meta["currentRound"], "shownRound": shown, "teams": len(rows),
            "pointsTotal": round(sum(r["pointsTotal"] for r in rows), 2), "players": len(pmap),
            "playersWithGames": sum(1 for v in pmap.values() if v.get("gamesPlayed"))}
