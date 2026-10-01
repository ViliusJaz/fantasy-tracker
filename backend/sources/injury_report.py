"""The BasketNews injury report page: an HTML table read into plain entries."""
import re
from html.parser import HTMLParser


# The report marks each status with a class id; the ids are the same on the .com and .lt sites.
STATUS_BY_ID = {"1": "ready", "2": "expected", "3": "questionable", "4": "game-time",
                "5": "doubtful", "6": "out", "7": "uncertain"}

# Wording of the BasketNews.com injury report (English) and of BasketNews.lt (Lithuanian).
SITE_LABELS = {"ready": "Ready", "expected": "Expected", "questionable": "Questionable", "game-time": "Game-time",
               "doubtful": "Doubtful", "out": "Out", "uncertain": "Uncertain"}

# Report entries that are about availability rather than health.
NOT_INJURY = re.compile(
    r"coach'?s?'? decision|not included|roster|personal|family|suspen|national team|domestic league|"
    r"rest\b|load management|visa|contract|transfer|left the (team|club)|released|trade|paternity|"
    r"new signing|signed|registered|not injured|announced",
    re.I,
)

# "DNP in Round 2 (reason)" or "DNP in Rounds 1-2 (reason)": the first round missed and the reason
DNP_RE = re.compile(r"DNP in Rounds?\s*(\d+)(?:\s*[-–]\s*\d+)?\s*(?:\(([^)]*)\))?", re.I)


def is_injury(text):
    """Health reason unless the report clearly gives another one. Uses the reason in
    'DNP in Round N (...)' when present, so 'coach's decision. Played in the domestic
    league' is not counted as an injury."""
    rnd, reason = dnp_reason(text)
    t = reason if rnd and reason else (text or "")
    return bool(t.strip()) and not NOT_INJURY.search(t)


class _InjuryTableParser(HTMLParser):
    """Reads BasketNews' injury_reports widget (same markup on .com and .lt)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and a.get("id") == "injury-reports-table":
            self.inside = True
        if not self.inside:
            return
        if tag == "tr":
            self._row = {"cls": a.get("class") or "", "cells": []}
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "href": None, "status": None}
        elif self._cell is not None:
            if tag == "a" and not self._cell["href"]:
                self._cell["href"] = a.get("href")
            m = re.search(r"player-status__id-(\d+)", a.get("class") or "")
            if m:
                self._cell["status"] = m.group(1)

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self.inside = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _clean(text):
    return " ".join((text or "").split())


def parse_injury_report(page):
    parser = _InjuryTableParser()
    parser.feed(page)
    entries, club = [], None
    for row in parser.rows:
        cells = row["cells"]
        if "team-row" in row["cls"]:
            club = _clean(cells[0]["text"]) if cells else club
            continue
        if len(cells) < 5:
            continue
        m = re.search(r"/(\d+)-[^/]*\.html", cells[1]["href"] or "")
        key = STATUS_BY_ID.get(cells[2]["status"], "other")
        entries.append({
            "bnId": m.group(1) if m else None,
            "name": _clean(cells[1]["text"]),
            "club": club,
            "pos": _clean(cells[0]["text"]),
            "status": key,
            "siteLabel": _clean(cells[2]["text"]) or SITE_LABELS.get(key, key),  # "Out", "Game-time", ...
            "return": _clean(cells[3]["text"]),
            "comment": _clean(cells[4]["text"]),
        })
    return entries


def dnp_reason(comment):
    """('DNP in Round 1 (knee injury)') -> (1, 'knee injury'); otherwise (None, comment)."""
    m = DNP_RE.search(comment or "")
    if m:
        return int(m.group(1)), _clean(m.group(2) or "")
    return None, _clean(comment).rstrip(".")
