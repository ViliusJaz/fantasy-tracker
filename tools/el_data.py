"""EuroLeague results and box scores for the game-winner model (research and backtests).

    python3 tools/el_data.py fetch [E2023 E2024 E2025]   download what is missing into var/backtest/
    python3 tools/el_data.py summary                     what is there

Games come from the public EuroLeague API (api-live.euroleague.net), box scores from
live.euroleague.net. Finished games never change, so each is downloaded once; requests are
spaced out so the site is not hammered. Nothing here is used when the tracker builds the site.
"""
import gzip
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.net import SSL_CTX  # noqa: E402  (python.org builds on macOS have no CA bundle)

OUT = ROOT / "var" / "backtest"
GAMES_URL = "https://api-live.euroleague.net/v2/competitions/E/seasons/{season}/games"
BOX_URL = "https://api-live.euroleague.net/v3/competitions/E/seasons/{season}/games/{code}/stats"
UA = "fantasy-tracker research (personal, low volume)"
PAUSE = 0.3  # seconds between box score requests (be gentle: live.euroleague.net answered 429)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                raw = resp.read()
                return gzip.decompress(raw) if resp.headers.get("Content-Encoding") == "gzip" else raw
        except Exception as exc:  # noqa: BLE001 - research tool: retry anything a few times
            if attempt == 3:
                raise
            wait = 2 * (attempt + 1)
            if getattr(exc, "code", None) == 429:  # rate limited: wait as long as asked, at least a minute
                wait = max(60, int((exc.headers or {}).get("Retry-After") or 0))
            print(f"  retry in {wait}s {url}: {exc}", flush=True)
            time.sleep(wait)
    return b""


def games_path(season):
    return OUT / f"games-{season}.json.gz"


def box_path(season, code):
    return OUT / "box" / season / f"{code}.json.gz"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data))


def load(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def fetch(seasons):
    for season in seasons:
        path = games_path(season)
        if not path.exists() or season == seasons[-1]:
            save(path, get(GAMES_URL.format(season=season)))
        games = [g for g in load(path)["data"] if g.get("played")]
        missing = [g for g in games if not box_path(season, g["gameCode"]).exists()]
        print(f"{season}: {len(games)} played games, {len(missing)} box scores to download")
        def one(g):
            save(box_path(season, g["gameCode"]), get(BOX_URL.format(code=g["gameCode"], season=season)))
            time.sleep(PAUSE)
        # one at a time with a pause: the server rate-limits (HTTP 429) faster clients
        with ThreadPoolExecutor(max_workers=1) as pool:
            for i, _ in enumerate(pool.map(one, missing), 1):
                if i % 100 == 0:
                    print(f"  {i}/{len(missing)}", flush=True)


def summary():
    for path in sorted(OUT.glob("games-*.json.gz")):
        season = path.name[6:-8]
        games = [g for g in load(path)["data"] if g.get("played")]
        boxes = len(list((OUT / "box" / season).glob("*.json.gz")))
        print(f"{season}: {len(games)} played, {boxes} box scores")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        fetch(sys.argv[2:] or ["E2023", "E2024", "E2025"])
    else:
        summary()
