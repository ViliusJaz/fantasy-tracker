"""BasketNews' EuroLeague rosters article: every club's current players with their real
basketball positions ("PG", "SG/SF", "PF/C"), kept up to date by BasketNews during the season.

    https://basketnews.com/news-248207-euroleague-transfer-market-2026-rosters-signings-rumors.html

The injuries "Teams" view reads it for who is on each roster and which positions they play.
The article is not in the fantasy API, so its address is set here, one per season: add the
new season's article when it appears (without one, positions come from the advanced stats).

The page changes a few times a week at most, so it is downloaded at most every 6 hours
(the answer is kept in the persistent cache between runs).
"""
import html
import re
import threading
import time
import urllib.request

from backend import cache, log, net
from backend.config import BROWSER_UA

LOG = log.get("fetch")

# (BasketNews league id, season year) -> the rosters article
ROSTER_ARTICLES = {
    ("25", 2026): "https://basketnews.com/news-248207-euroleague-transfer-market-2026-rosters-signings-rumors.html",
}
KEEP = 6 * 3600
POSITIONS = ("PG", "SG", "SF", "PF", "C")
WIDE = {"G": ["PG", "SG"], "F": ["SF", "PF"]}  # the article sometimes writes just "G" or "F"
MIN_CLUBS, MIN_PLAYERS = 16, 200  # less than this: the page changed shape, do not trust it

_lock = threading.Lock()
_memo = {}

_TABLE = re.compile(r"<table\b(?:(?!<table\b).)*?</table>", re.S | re.I)  # innermost tables only
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)
_TITLE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S | re.I)
_PLAYER = re.compile(r"basketnews\.com/players/(\d+)-")


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def positions_of(text):
    """'SG/SF' -> ['SG', 'SF'] (first = main position); 'G' -> ['PG', 'SG']."""
    out = []
    for part in re.split(r"[/,\s-]+", (text or "").upper()):
        for pos in WIDE.get(part, [part] if part in POSITIONS else []):
            if pos not in out:
                out.append(pos)
    return out


def parse_rosters(page):
    """{club name: [{"bnId", "name", "positions"}]} from the article's roster tables."""
    clubs = {}
    for table in _TABLE.findall(page):
        title = _TITLE.search(table)
        if not title:
            continue
        players = []
        for row in _ROW.findall(table):
            cells = _CELL.findall(row)
            if len(cells) < 2:
                continue
            pos = positions_of(_text(cells[0]))
            link = _PLAYER.search(cells[1])
            if not pos or not link:
                continue  # header rows, and the odd player without a BasketNews page
            name = _text(re.sub(r"\(.*?\)", "", re.sub(r"<a\b[^>]*news-\d+.*?</a>", "", cells[1], flags=re.S)))
            players.append({"bnId": link.group(1), "name": name.strip(" ()"), "positions": pos})
        if players:
            clubs[_text(title.group(1))] = players
    return clubs


def rosters(meta):
    """This season's rosters (see parse_rosters), or {} when there is no article or it is unusable."""
    url = ROSTER_ARTICLES.get((str(meta.get("bnLeagueId")), meta.get("seasonYear")))
    if not url:
        return {}
    with _lock:
        hit = _memo.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        page = None
        stored = cache.get(url)
        if stored is not None:
            page = stored.get("html") or ""
            net.remember("GET", url, None, page.encode("utf-8"), content_type="text/html")
        else:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en",
                                                       "Accept-Encoding": "gzip"})
            try:
                page = net.fetch(req, "rosters").decode("utf-8", errors="replace")
            except net.FetchError as exc:
                LOG.warning("rosters article unavailable: %s", exc)
        clubs = parse_rosters(page) if page else {}
        if page and (len(clubs) < MIN_CLUBS or sum(map(len, clubs.values())) < MIN_PLAYERS):
            LOG.warning("rosters article not understood (%d clubs, %d players); using other position sources",
                        len(clubs), sum(map(len, clubs.values())))
            clubs = {}
        elif page and stored is None:
            cache.put(url, "rosters", {"html": page}, KEEP)
        _memo[url] = (time.time() + (KEEP if clubs else 600), clubs)
        return clubs
