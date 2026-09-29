"""Static export of the tracker, for hosting on GitHub Pages.

    python3 export.py            ->  ./site

Writes the page plus every answer the page asks the API for as plain JSON files
(site/api/<path>[.r<round>].<lang>.json), so the site works without a running
server. Like the server's background thread it also records lineups and injury
changes in ./data. publish.sh runs this every 15 minutes on the Mac (BasketNews
refuses GitHub's servers), commits ./data and publishes ./site to GitHub Pages.

Exits with an error (and the previous site stays online) when BasketNews could
not be reached for most of the pages.
"""
import contextvars
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import server as s

OUT = s.ROOT / "site"
LANGS = ("lt", "en")
RUN_TTL = 3 * 60 * 60        # one run fetches everything once
PROBALLERS_LOOKUPS = 15      # new Wikidata lookups per run; the rest wait for the next runs
GENERATED = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Every page of one run should come from the same BasketNews answers.
_gql = s.gql
s.gql = lambda query, variables, ttl=None: _gql(query, variables, ttl=RUN_TTL)

failures, written = [], 0
_lookups = {"left": PROBALLERS_LOOKUPS}
_lookup_lock = threading.Lock()
_write_lock = threading.Lock()
# Own workers: the payload builders use server.POOL themselves, and waiting on it from
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
    except (s.UpstreamError, s.NotFound, ValueError, KeyError, TypeError) as exc:
        with _write_lock:
            failures.append(rel)
        print(f"  ! {rel}: {exc}")
        return
    path = OUT / "api" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "generatedAt": GENERATED}
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with _write_lock:
        written += 1


def prefetch_players(meta, ids, size=10):
    """Fetch player pages ten at a time (one request instead of ten) and seed the cache
    with the exact entries player_payload() will ask for."""
    rounds = list(range(meta["firstRound"], meta["latestRound"] + 1))
    single = s._player_rounds_query(rounds)
    head = "playerRecordFromClient(id: $id) {"
    fields = single[single.index(head) + len(head):single.rindex("} }")]
    base = {"leagueId": meta["leagueId"], "locale": s.LOCALE, "pcs": meta["pointCalcSystem"],
            "statsRound": meta["latestRound"], "gamesRound": meta["currentRound"]}
    decl = "$leagueId: String!, $locale: String!, $pcs: String, $statsRound: Int, $gamesRound: Int"

    def batch(chunk):
        query = f"query({decl}) {{" + "".join(
            f'p{n}: playerRecordFromClient(id: "{pid}") {{{fields}}}' for n, pid in enumerate(chunk)) + "}"
        try:
            data = _gql(query, base, ttl=RUN_TTL)
        except s.UpstreamError as exc:
            print(f"  ! player batch: {exc}")  # player_payload() will fetch these one by one
            return
        expires = time.time() + RUN_TTL
        with s._cache_lock:
            for n, pid in enumerate(chunk):
                key = single + json.dumps({**base, "id": pid}, sort_keys=True)
                s._cache[key] = (expires, {"playerRecordFromClient": data.get(f"p{n}")})

    chunks = [ids[i:i + size] for i in range(0, len(ids), size)]
    run_all(batch, chunks)


def proballers_for(info):
    """Proballers link without a redirect endpoint; new Wikidata lookups are rationed per run."""
    known = s.read_json(s.PROBALLERS_FILE, {}).get(info["bnId"] or info["id"])
    if known is None:
        with _lookup_lock:
            if _lookups["left"] <= 0:
                return s.proballers_search(info)
            _lookups["left"] -= 1
    return s.proballers_target(info)


def player_file(fid, pid):
    payload = s.player_payload(fid, pid)
    payload["proballers"] = proballers_for(payload["player"])
    return payload


def export_league(entry):
    fid = entry["id"]
    meta = s.league_meta(fid)
    first, cur, latest = meta["firstRound"], meta["currentRound"], meta["latestRound"]
    finished = list(range(first, cur))
    h2h = meta["format"] == "head_to_head"
    base = f"league/{fid}"
    print(f"{meta['title']}: rounds {first + 1}-{latest + 1}, current {cur + 1}")

    teams = [r["team"]["id"] for r in s.standings(meta)[1]]
    pmap = s.players(meta, latest, cur)
    rostered = {p["id"] for lu in s.lineups(meta).values() for p in lu["players"]}
    player_ids = sorted(set(pmap) | rostered)
    prefetch_players(meta, player_ids)

    for lang in LANGS:
        s.LANG.set(lang)
        jobs = [
            (file_name(f"{base}/standings", None, lang), lambda: s.standings_payload(fid)),
            (file_name(f"{base}/rounds", None, lang), lambda: s.rounds_payload(fid)),
            (file_name(f"{base}/games", None, lang), lambda: s.games_payload(fid)),
            (file_name(f"{base}/records", None, lang), lambda: s.records_payload(fid)),
            (file_name(f"{base}/free-agents", None, lang), lambda: s.players_payload(fid, "free")),
            (file_name(f"{base}/players", None, lang), lambda: s.players_payload(fid, "all")),
            (file_name(f"{base}/draft", None, lang), lambda: s.draft_payload(fid)),
            (file_name(f"{base}/transfers", None, lang), lambda: s.transfers_payload(fid)),
        ]
        for r in range(first, latest + 1):
            jobs.append((file_name(f"{base}/standings", r, lang), lambda r=r: s.standings_payload(fid, r)))
        for r in range(first, (meta["totalRounds"] if h2h else latest + 1)):
            jobs.append((file_name(f"{base}/rounds", r, lang), lambda r=r: s.rounds_payload(fid, r)))
        for r in range(first, cur + 1):
            jobs.append((file_name(f"{base}/games", r, lang), lambda r=r: s.games_payload(fid, r)))
        for r in finished:
            jobs.append((file_name(f"{base}/records", r, lang), lambda r=r: s.records_payload(fid, r)))
        for tid in teams:
            jobs.append((file_name(f"{base}/team/{tid}", None, lang), lambda tid=tid: s.team_payload(fid, tid)))
            for r in range(first, cur + 1):
                jobs.append((file_name(f"{base}/team/{tid}", r, lang),
                             lambda tid=tid, r=r: s.team_payload(fid, tid, r)))
        for pid in player_ids:
            jobs.append((file_name(f"{base}/player/{pid}", None, lang), lambda pid=pid: player_file(fid, pid)))
        run_all(lambda j: job(*j), jobs)
        print(f"  {lang}: {len(jobs)} pages")


def copy_page():
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(s.STATIC_DIR, OUT)
    index = OUT / "index.html"
    html = index.read_text(encoding="utf-8")
    # Tells app.js to read the JSON files instead of calling the server.
    html = html.replace("<head>", '<head>\n  <meta name="ft-static" content="1">', 1)
    index.write_text(html, encoding="utf-8")
    (OUT / ".nojekyll").write_text("")


def main():
    started = time.time()
    copy_page()
    entries = s.load_config()
    for entry in entries:
        try:
            export_league(entry)
        except (s.UpstreamError, s.NotFound) as exc:
            failures.append(entry["id"])
            print(f"! {entry['id']}: {exc}")
    for lang in LANGS:
        s.LANG.set(lang)
        job(file_name("leagues", None, lang), s.leagues_payload)
    print(f"Done in {time.time() - started:.0f}s: {written} files, {len(failures)} failed")
    if not written or len(failures) > 0.2 * (written + len(failures)):
        sys.exit("Too many pages failed; keeping the previous site.")


if __name__ == "__main__":
    main()
