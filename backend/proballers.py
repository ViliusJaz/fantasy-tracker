"""Direct Proballers profile links, found through Wikidata and remembered in data/proballers.json."""
import re
import threading
import urllib.parse
from datetime import timedelta

from backend import clock, config, log, net
from backend.sources.wikidata import basketnews_birth_date, wikidata_candidates, wikidata_search
from backend.util import ascii_slug, read_json, write_json


LOG = log.get("fetch")
_proballers_lock = threading.Lock()


def proballers_link(info):
    """Direct Proballers profile URL for a player, or None when it cannot be pinned down."""
    key = info["bnId"] or info["id"]
    with _proballers_lock:
        cache = read_json(config.PROBALLERS_FILE, {})
    hit = cache.get(key)
    if hit and (hit.get("url") or hit.get("checked", "") >= (clock.today() - timedelta(days=7)).isoformat()):
        return hit.get("url")
    url = None
    try:
        # 1) exact name / alias, 2) diacritic-insensitive search, 3) surname only (needs a birth-date match)
        cands, strict = wikidata_candidates(info["name"]), False
        if not cands:
            cands = wikidata_search(info["name"])
        if not cands:
            surname = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", info["name"]).split()[-1]
            cands, strict = wikidata_search(surname, limit=20), True
        if (len(cands) > 1 or strict) and info.get("bnId"):
            dob = basketnews_birth_date(info["bnId"], info["name"])
            cands = [c for c in cands if dob and c["dob"] == dob]
        if len(cands) == 1:
            c = cands[0]
            url = f"https://www.proballers.com/basketball/player/{c['pb']}/{ascii_slug(c['label'])}"
    except (net.FetchError, ValueError, KeyError) as exc:
        LOG.warning("Proballers lookup failed for %s: %s", info["name"], exc)
        return None
    with _proballers_lock:
        cache = read_json(config.PROBALLERS_FILE, {})
        cache[key] = {"name": info["name"], "url": url, "checked": clock.today().isoformat()}
        write_json(config.PROBALLERS_FILE, cache)
    return url


def proballers_target(info):
    """Player's Proballers page, or a search limited to Proballers player pages when Wikidata has none."""
    return proballers_link(info) or proballers_search(info)


def proballers_search(info):
    query = f"site:proballers.com/basketball/player {info['name']} {((info.get('club') or {}).get('nameEn') or '')}"
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query.strip())
