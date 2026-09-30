"""Static export of the tracker, for hosting on GitHub Pages.

    python3 export.py            ->  ./site

Writes the page plus every answer the page asks the API for as plain JSON files
(site/api/<path>[.r<round>].<lang>.json), so the site works without a running
server. Like the server's background thread it also records lineups and injury
changes in ./data. publish.sh runs this every 15 minutes on the Mac (BasketNews
refuses GitHub's servers), commits ./data and publishes ./site to GitHub Pages.

Every build is checked before anything is stored or published (backend/validation.py).
A build that fails the checks exits with an error, leaves ./site as it was (the new one
is built in ./site.new) and records why in var/health.json; publish.sh then publishes
nothing and the previous site stays online. A good build also writes site/api/health.json.
"""
import contextvars
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend import cache, clock, config, health, history, injuries, log, net, pipeline, validation
from backend.errors import NotFound, UpstreamError
from backend.i18n import LANG
from backend.league import league_meta, lineups, standings
from backend.rounds import round_state
from backend.payloads.analytics import analytics_payload
from backend.payloads.draft import draft_payload
from backend.payloads.games import games_payload
from backend.payloads.injuries import injuries_payload
from backend.payloads.leagues import leagues_payload
from backend.payloads.player import player_payload
from backend.payloads.players import players_payload
from backend.payloads.records import records_payload
from backend.payloads.standings import rounds_payload, standings_payload
from backend.payloads.team import team_payload
from backend.payloads.transfers import transfers_payload
from backend.players import players
from backend.proballers import proballers_search, proballers_target
from backend.sources import basketnews
from backend.storage import archive, db, ingest, snapshots
from backend.util import read_json

OUT = config.SITE_DIR
BUILD = OUT.with_name(OUT.name + ".new")  # built here, moved to OUT only when the build is good
LANGS = ("lt", "en")
RUN_TTL = 3 * 60 * 60        # one run fetches everything once
# new Wikidata lookups per run; the rest wait for the next runs
PROBALLERS_LOOKUPS = int(os.environ.get("FT_PROBALLERS_LOOKUPS", 15))
GENERATED = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Every page of one run should come from the same BasketNews answers.
basketnews.RUN_TTL = RUN_TTL
LOG = log.get("export")

failures, written = [], 0
_lookups = {"left": PROBALLERS_LOOKUPS}
_lookup_lock = threading.Lock()
_write_lock = threading.Lock()
# Own workers: the payload builders use backend.util.POOL themselves, and waiting on it from
# its own threads could deadlock.
WORKERS = ThreadPoolExecutor(max_workers=8)


def run_all(fn, items):
    futures = [WORKERS.submit(contextvars.copy_context().run, fn, item) for item in items]
    return [f.result() for f in futures]


def file_name(path, rnd=None, lang="lt"):
    return f"{path}{'' if rnd is None else f'.r{rnd}'}.{lang}.json"


def job(rel, build):
    """Build one payload and write it; a failure only skips that file."""
    global written
    try:
        payload = build()
    except (UpstreamError, NotFound, ValueError, KeyError, TypeError) as exc:
        with _write_lock:
            failures.append((rel, f"{type(exc).__name__}: {exc}"))
        LOG.warning("%s not written: %s", rel, exc)
        return
    path = BUILD / "api" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # No timestamp inside: a file whose data did not change stays byte-identical, so the
    # upload only carries what changed. The run time goes to api/meta.json.
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with _write_lock:
        written += 1


def prefetch_players(meta, ids, size=10):
    """Fetch player pages ten at a time (one request instead of ten) and seed the cache
    with the exact entries player_payload() will ask for."""
    rounds = list(range(meta["firstRound"], meta["latestRound"] + 1))
    single = basketnews.player_rounds_query(rounds)
    head = "playerRecordFromClient(id: $id) {"
    fields = single[single.index(head) + len(head):single.rindex("} }")]
    base = {"leagueId": meta["leagueId"], "locale": config.LOCALE, "pcs": meta["pointCalcSystem"],
            "statsRound": meta["latestRound"], "gamesRound": meta["currentRound"]}
    decl = "$leagueId: String!, $locale: String!, $pcs: String, $statsRound: Int, $gamesRound: Int"

    def batch(chunk):
        query = f"query({decl}) {{" + "".join(
            f'p{n}: playerRecordFromClient(id: "{pid}") {{{fields}}}' for n, pid in enumerate(chunk)) + "}"
        try:
            data = basketnews.gql(query, base)
        except UpstreamError as exc:
            log.get("fetch").warning("player batch failed (pages fetch them one by one): %s", exc)
            return
        for n, pid in enumerate(chunk):
            basketnews.cache_answer(single, {**base, "id": pid}, {"playerRecordFromClient": data.get(f"p{n}")},
                                    RUN_TTL)

    chunks = [ids[i:i + size] for i in range(0, len(ids), size)]
    run_all(batch, chunks)


def proballers_for(info):
    """Proballers link without a redirect endpoint; new Wikidata lookups are rationed per run."""
    known = read_json(config.PROBALLERS_FILE, {}).get(info["bnId"] or info["id"])
    if known is None:
        with _lookup_lock:
            if _lookups["left"] <= 0:
                return proballers_search(info)
            _lookups["left"] -= 1
    return proballers_target(info)


def player_file(fid, pid):
    payload = player_payload(fid, pid)
    payload["proballers"] = proballers_for(payload["player"])
    return payload


def prepare_league(entry, report, base):
    """FETCH and VALIDATION for one league: what its pages need. Upstream errors propagate."""
    fid = entry["id"]
    meta = league_meta(fid)
    validation.check_meta(report, meta, base)
    with log.timed() as took:
        shown, rows = standings(meta)
        teams = [r["team"]["id"] for r in rows]
        pmap = players(meta, meta["latestRound"], meta["currentRound"])
        current = lineups(meta)
        rostered = {p["id"] for lu in current.values() for p in lu["players"]}
        player_ids = sorted(set(pmap) | rostered)
        prefetch_players(meta, player_ids)
        pipeline.observe(meta)
    log.get("fetch").info("%s: round %d of %d (%s), %d teams, %d players, %.1fs", meta["title"],
                          meta["currentRound"] + 1, meta["totalRounds"], round_state(meta, meta["currentRound"]),
                          len(teams), len(player_ids), took())
    validation.check_standings(report, meta, shown, rows, base)
    validation.check_players(report, meta, pmap, base)
    validation.check_lineups(report, meta, current, teams)
    return {"meta": meta, "teams": teams, "playerIds": player_ids,
            "metrics": validation.league_metrics(meta, shown, rows, pmap)}


def export_league(league):
    """EXPORT: every page of one league, in both languages."""
    meta, teams, player_ids = league["meta"], league["teams"], league["playerIds"]
    fid = meta["id"]
    first, cur, latest = meta["firstRound"], meta["currentRound"], meta["latestRound"]
    finished = list(range(first, cur))
    h2h = meta["format"] == "head_to_head"
    base = f"league/{fid}"
    pages = 0
    started = time.monotonic()

    for lang in LANGS:
        LANG.set(lang)
        jobs = [
            (file_name(f"{base}/standings", None, lang), lambda: standings_payload(fid)),
            (file_name(f"{base}/rounds", None, lang), lambda: rounds_payload(fid)),
            (file_name(f"{base}/games", None, lang), lambda: games_payload(fid)),
            (file_name(f"{base}/records", None, lang), lambda: records_payload(fid)),
            (file_name(f"{base}/free-agents", None, lang), lambda: players_payload(fid, "free")),
            (file_name(f"{base}/players", None, lang), lambda: players_payload(fid, "all")),
            (file_name(f"{base}/draft", None, lang), lambda: draft_payload(fid)),
            (file_name(f"{base}/transfers", None, lang), lambda: transfers_payload(fid)),
            (file_name(f"{base}/injuries", None, lang), lambda: injuries_payload(fid)),
        ]
        for r in range(first, latest + 1):
            jobs.append((file_name(f"{base}/standings", r, lang), lambda r=r: standings_payload(fid, r)))
        for r in range(first, (meta["totalRounds"] if h2h else latest + 1)):
            jobs.append((file_name(f"{base}/rounds", r, lang), lambda r=r: rounds_payload(fid, r)))
        for r in range(first, meta["totalRounds"]):  # the whole schedule, not just played rounds
            jobs.append((file_name(f"{base}/games", r, lang), lambda r=r: games_payload(fid, r)))
        for r in finished:
            jobs.append((file_name(f"{base}/records", r, lang), lambda r=r: records_payload(fid, r)))
        for tid in teams:
            jobs.append((file_name(f"{base}/team/{tid}", None, lang), lambda tid=tid: team_payload(fid, tid)))
            for r in range(first, cur + 1):
                jobs.append((file_name(f"{base}/team/{tid}", r, lang),
                             lambda tid=tid, r=r: team_payload(fid, tid, r)))
        for pid in player_ids:
            jobs.append((file_name(f"{base}/player/{pid}", None, lang), lambda pid=pid: player_file(fid, pid)))
        run_all(lambda j: job(*j), jobs)
        pages += len(jobs)
    LOG.info("%s: %d pages (%s), %.1fs", meta["title"], pages, " + ".join(LANGS), time.monotonic() - started)


def copy_page():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    shutil.copytree(config.STATIC_DIR, BUILD)
    index = BUILD / "index.html"
    html = index.read_text(encoding="utf-8")
    # Tells app.js to read the JSON files instead of calling the server.
    html = html.replace("<head>", '<head>\n  <meta name="ft-static" content="1">', 1)
    # GitHub Pages lets browsers keep files for 10 minutes: a content hash in the URL makes
    # a normal reload pick up a new version right away.
    for name in ("app.js", "style.css", "fonts/fonts.css"):
        digest = hashlib.sha1((BUILD / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={digest}"')
    index.write_text(html, encoding="utf-8")
    (BUILD / ".nojekyll").write_text("")


def store_history(metas):
    """STORAGE: archive finished rounds (data/archive) and refresh the SQLite index."""
    written = archive.save_finished_rounds(metas)
    log.get("storage").info("archive: %d file(s) written", len(written))
    try:
        conn = db.connect()
        try:
            loaded = ingest.refresh(conn)
        finally:
            conn.close()
        log.get("storage").info("SQLite index: %d file(s) loaded, %d rows", loaded["files"], loaded["rows"])
    except sqlite3.Error as exc:  # the index is a convenience: never fail a build over it
        log.get("storage").warning("SQLite index not updated: %s", exc)


def export_analytics(leagues):
    """ANALYTICS: season metrics from the (just refreshed) history, one file per league and language."""
    started = time.monotonic()
    jobs = [(file_name(f"league/{lg['meta']['id']}/analytics", None, lang), lambda fid=lg["meta"]["id"]: analytics_payload(fid))
            for lg in leagues for lang in LANGS]
    for rel, build in jobs:
        job(rel, build)
    log.get("analytics").info("%d league(s): %d files, %.1fs", len(leagues), len(jobs), time.monotonic() - started)


def swap_in():
    """Replace site/ with the finished build in one step (a failed build never touches it)."""
    old = OUT.with_name(OUT.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if OUT.exists():
        OUT.rename(old)
    BUILD.rename(OUT)
    shutil.rmtree(old, ignore_errors=True)


def main():
    log.setup()
    started, started_at = time.time(), clock.now().astimezone().isoformat(timespec="seconds")
    previous = health.load(config.VAR_DIR / "health.json", OUT / "api" / "health.json")
    cache.prune()
    recording = snapshots.start()
    base = health.baseline(previous)
    report = validation.Report()
    leagues = []

    for entry in config.load_config():
        try:
            leagues.append(prepare_league(entry, report, (base.get("leagues") or {}).get(entry["id"])))
        except (UpstreamError, NotFound) as exc:
            report.add(validation.FATAL, f"league {entry.get('title') or entry['id']}", "unavailable", str(exc))
        if report.fatal:
            break

    if not report.fatal:
        changed = pipeline.store(report)
        log.get("storage").info("lineups changed in %d file(s), injury log %s", changed["lineups"],
                                "updated" if changed["injuries"] else "unchanged")
        copy_page()
        for league in leagues:
            export_league(league)
        for lang in LANGS:
            LANG.set(lang)
            job(file_name("leagues", None, lang), leagues_payload)
        (BUILD / "api" / "meta.json").write_text(json.dumps({"generatedAt": GENERATED}), encoding="utf-8")
        validation.check_pages(report, written, len(failures), base.get("pages"),
                               examples=[f"{rel} ({why})" for rel, why in failures[:3]])
        if not report.fatal:
            store_history([lg["meta"] for lg in leagues])
            export_analytics(leagues)
    else:
        history.discard()  # nothing from a failed build becomes history

    requests = net.stats()
    for source, st in sorted(requests.items()):
        log.get("fetch").info("%s: %d requests, %d retried, %d failed, %.2f MB", source, st["requests"],
                              st["retries"], st["failures"], st["bytes"] / 1e6)
    if cache.STATS["hits"] or cache.STATS["stored"]:
        log.get("fetch").info("cache: %d answers from var/cache.sqlite, %d stored", cache.STATS["hits"],
                              cache.STATS["stored"])
    missing = injuries.untranslated()
    if missing:
        LOG.info("%d injury comment(s) without a Lithuanian translation, e.g. %s", len(missing), missing[0])

    snap = snapshots.save(recording)
    if snap:
        log.get("storage").info("snapshot of this build: %s", snap.relative_to(config.ROOT) if
                                snap.is_relative_to(config.ROOT) else snap)
    ok = not report.fatal
    doc = health.document(
        previous, ok=ok, started=started_at, duration=time.time() - started, requests=requests, report=report,
        sources=health.source_status(requests, report, injuries.state()),
        players=max((len(lg["playerIds"]) for lg in leagues), default=0), pages=written, failed_pages=len(failures),
        metrics={"pages": written, "leagues": {lg["meta"]["id"]: lg["metrics"] for lg in leagues}})
    health.write(doc, config.VAR_DIR / "health.json", *([BUILD / "api" / "health.json"] if ok else []))
    summary = report.summary()
    if not ok:
        shutil.rmtree(BUILD, ignore_errors=True)
        log.get("build").error("FAILED after %.1fs: %d fatal, %d error(s), %d warning(s); keeping the previous site",
                               time.time() - started, summary["fatal"], summary["errors"], summary["warnings"])
        sys.exit("Build failed validation; keeping the previous site.")
    swap_in()
    log.get("build").info("%s: %d files, %d failed, %d error(s), %d warning(s), %.1fs", doc["status"], written,
                          len(failures), summary["errors"], summary["warnings"], time.time() - started)


if __name__ == "__main__":
    main()
