"""Wikidata lookups for Proballers IDs (property P8548), plus birth dates from BasketNews profiles
to tell namesakes apart."""
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from backend.config import BROWSER_UA
from backend.net import SSL_CTX
from backend.util import ascii_slug


WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

WIKIDATA_UA = "fantasy-tracker/1.0 (personal basketball fantasy tracker; local app)"

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def name_variants(name):
    base = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", name.strip())
    out = {name.strip(), base}
    m = re.match(r"^([A-Z])\.?\s?([A-Z])\.?\s+(.+)$", base)  # TJ / T.J. / T. J.
    if m:
        a, b, rest = m.groups()
        out |= {f"{a}{b} {rest}", f"{a}.{b}. {rest}", f"{a}. {b}. {rest}"}
    out |= {unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode() for v in list(out)}
    return sorted(out)


_wikidata_clock = threading.Lock()

_wikidata_last = [0.0]


def _wikidata_get(url, data=None):
    """Polite Wikidata request: at most one per second (their API rate-limits bursts)."""
    with _wikidata_clock:
        wait = 1.0 - (time.time() - _wikidata_last[0])
        if wait > 0:
            time.sleep(wait)
        _wikidata_last[0] = time.time()
    headers = {"User-Agent": WIKIDATA_UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
        return json.load(resp)


def wikidata_search(text, limit=10):
    """Full-text Wikidata search (ignores diacritics, covers aliases) among items with a Proballers ID."""
    api = "https://www.wikidata.org/w/api.php?"
    hits = _wikidata_get(api + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": f"{text} haswbstatement:P8548",
        "srlimit": limit, "format": "json"}))["query"]["search"]
    ids = [h["title"] for h in hits]
    if not ids:
        return []
    ents = _wikidata_get(api + urllib.parse.urlencode({
        "action": "wbgetentities", "ids": "|".join(ids), "props": "labels|claims", "languages": "en",
        "format": "json"}))["entities"]
    out = []
    for qid in ids:
        claims = ents.get(qid, {}).get("claims", {})
        pb = ((claims.get("P8548") or [{}])[0].get("mainsnak", {}).get("datavalue") or {}).get("value")
        dob = (((claims.get("P569") or [{}])[0].get("mainsnak", {}).get("datavalue") or {}).get("value") or {}).get("time", "")
        label = ents.get(qid, {}).get("labels", {}).get("en", {}).get("value") or text
        if pb:
            out.append({"pb": pb, "dob": dob[1:11] or None, "label": label})
    return out


def wikidata_candidates(name):
    values = " ".join(f'"{v}"@{lang}' for v in name_variants(name) for lang in ("en", "mul")).replace("\\", "")
    query = f"""SELECT DISTINCT ?pb ?dob ?label WHERE {{
      VALUES ?name {{ {values} }}
      {{ ?item rdfs:label ?name }} UNION {{ ?item skos:altLabel ?name }}
      ?item wdt:P8548 ?pb.
      OPTIONAL {{ ?item wdt:P569 ?dob }}
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
    }}"""
    rows = _wikidata_get(WIKIDATA_SPARQL, urllib.parse.urlencode({"query": query, "format": "json"}).encode()
                         )["results"]["bindings"]
    found = {}
    for r in rows:
        pb = r["pb"]["value"]
        found.setdefault(pb, {"pb": pb, "dob": (r.get("dob") or {}).get("value", "")[:10] or None,
                              "label": (r.get("label") or {}).get("value") or name})
    return list(found.values())


def basketnews_birth_date(bn_id, name):
    """'Age: 33 (1993 April 10)' on the BasketNews player page -> '1993-04-10'."""
    url = f"https://basketnews.com/players/{bn_id}-{ascii_slug(name)}.html"
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    m = re.search(r"\((\d{4})\s+([A-Za-z]+)\s+(\d{1,2})\)", page)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return f"{m.group(1)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(3)):02d}"
