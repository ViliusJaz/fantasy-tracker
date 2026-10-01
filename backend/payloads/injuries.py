"""/api/league/<id>/injuries: injury-report changes as a news feed, and the report by EuroLeague
club with the real basketball positions each club is missing."""
import collections
import re

from backend import history, pipeline
from backend.history import REMOVED_NOTE
from backend.i18n import L
from backend.injuries import health_label, injury_report, loc_comment, return_local
from backend.league import league_meta, owners, standings
from backend.players import player_brief, players
from backend.sources.advanced import player_positions
from backend.sources.injury_report import is_injury
from backend.sources.rosters import rosters
from backend.util import ascii_slug


# How a report status reads as news ("Name (CLUB) nežais.").
FEED_PHRASES = {
    "out": ("nežais.", "will not play."),
    "doubtful": ("dalyvavimas abejotinas.", "is doubtful."),
    "questionable": ("dalyvavimas abejotinas.", "is questionable."),
    "uncertain": ("būsena neaiški.", "status unclear."),
    "game-time": ("sprendimas bus priimtas prieš rungtynes.", "will be a game-time decision."),
    "expected": ("turėtų žaisti.", "is expected to play."),
    "ready": ("sveikas ir grįžta į rikiuotę.", "is healthy and back in the lineup."),
}

FEED_TONE = {"out": "bad", "ready": "good", "expected": "good"}

FEED_LIMIT = 300


def injuries_payload(fid):
    """Injury-report changes as a news feed, newest first, with each player's owner in this league."""
    meta = league_meta(fid)
    injury_report(meta)
    pipeline.store()  # the feed includes changes seen just now
    log = history.injury_log()
    pmap = players(meta, meta["latestRound"], meta["currentRound"])
    by_bn = {p["bnId"]: p for p in pmap.values() if p.get("bnId")}
    own = owners(meta, meta["currentRound"])
    events = []
    for bn_id, rec in log.items():
        p = by_bn.get(bn_id)
        owner = (own.get(p["id"]) or {}).get("team") if p else None
        brief = player_brief(p, None, rec.get("name"))
        if not p:
            brief["club"] = {"abbr": rec.get("club")} if rec.get("club") else None
        for ep in rec["episodes"]:
            for u in ep["updates"]:
                status = u["status"]
                comment = "" if u.get("comment") == REMOVED_NOTE else loc_comment(u.get("comment"))
                events.append({
                    "at": u.get("at") or u["date"], "hasTime": bool(u.get("at")),
                    "status": status, "label": health_label(status),
                    "phrase": L(*FEED_PHRASES.get(status, ("būsena pasikeitė.", "status changed."))),
                    "tone": FEED_TONE.get(status, "neutral"),
                    "comment": comment, "return": return_local(u.get("return"), status),
                    "player": brief, "owner": owner,
                })
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"league": meta, "events": events[:FEED_LIMIT], "total": len(events),
            "teams": [r["team"] for r in standings(meta)[1]], "reportUrl": meta["injuryReportUrl"],
            "clubs": club_injuries(meta, pmap, own)}


POSITIONS = ("PG", "SG", "SF", "PF", "C")
# Only for the few players BasketNews has no real position for yet (no games in two seasons).
FANTASY_POSITIONS = {"guard": ["PG", "SG"], "forward": ["SF", "PF"], "center": ["C"]}
SHORT_BELOW = 2  # injuries leave fewer healthy players than this at a position: the club may run short


def _report_positions(text):
    """The report's "P" column ("SG", "PF/C") as positions."""
    return [x for x in re.split(r"[/,\s-]+", (text or "").upper()) if x in POSITIONS]


def _name_tokens(name):
    return set(ascii_slug(name or "").split("-")) - {"", "fc", "bc", "basket", "basketball", "club"}


def club_injuries(meta, pmap, own):
    """The injury report by EuroLeague club: who is injured (real positions), who is listed for
    another reason (coach's decision, personal...), who is expected back, and per position how
    many players of the roster are healthy. "Expected" and non-injury entries do not count as injured.

    Rosters and positions come from BasketNews' rosters article (backend/sources/rosters.py);
    without it, a club's roster is its players in the fantasy game and positions come from the
    advanced stats, the injury report and, last, the fantasy position."""
    report = injury_report(meta)
    article = rosters(meta)
    positions = player_positions(meta)
    by_bn = {str(p["bnId"]): p for p in pmap.values() if p.get("bnId")}
    clubs = {}
    for p in pmap.values():
        if p.get("club") and p["club"].get("abbr"):
            clubs.setdefault(p["club"]["abbr"], {"club": p["club"], "roster": []})["roster"].append(p)

    def club_by_name(name):
        mine = _name_tokens(name)
        scored = [(len(mine & (_name_tokens(c["club"].get("nameEn")) | _name_tokens(c["club"].get("fullName")))), abbr)
                  for abbr, c in clubs.items()]
        best = max(scored, default=(0, None))
        return best[1] if best[0] else None

    # The article's clubs, matched on their players (most of them are in the game), else on the name
    article_pos, article_club = {}, {}
    for name, players_ in article.items():
        votes = collections.Counter((by_bn[x["bnId"]].get("club") or {}).get("abbr") for x in players_ if x["bnId"] in by_bn)
        abbr = votes.most_common(1)[0][0] if votes else club_by_name(name)
        if abbr not in clubs:
            continue
        clubs[abbr]["roster"] = [by_bn.get(x["bnId"]) or {"bnId": x["bnId"], "name": x["name"]} for x in players_]
        for x in players_:
            article_pos[x["bnId"]], article_club[x["bnId"]] = x["positions"], abbr

    def club_of(entry):
        bn = str(entry["bnId"])
        p = by_bn.get(bn)
        return article_club.get(bn) or (p["club"].get("abbr") if p and p.get("club") else None) or club_by_name(entry.get("club"))

    def player_positions_of(bn, p=None, entry=None):
        bn = str(bn)
        return (article_pos.get(bn) or positions.get(bn) or _report_positions((entry or {}).get("pos"))
                or FANTASY_POSITIONS.get((p or {}).get("position"), []))

    out = {abbr: {"injured": [], "other": [], "expected": []} for abbr in clubs}
    injured_ids = set()
    for bn, entry in report.items():
        if entry["status"] == "ready":
            continue
        abbr = club_of(entry)
        if abbr not in out:
            continue
        p = by_bn.get(str(bn))
        kind = "expected" if entry["status"] == "expected" else "injured" if is_injury(entry["comment"]) else "other"
        if kind == "injured":
            injured_ids.add(str(bn))
        brief = player_brief(p, None, entry["name"])
        out[abbr][kind].append({
            "player": brief, "positions": player_positions_of(bn, p, entry),
            "status": entry["status"], "label": health_label(entry["status"]),
            "return": return_local(entry["return"], entry["status"]), "comment": loc_comment(entry["comment"]),
            "owner": (own.get(p["id"]) or {}).get("team") if p else None,
        })
    rows = []
    for abbr, c in clubs.items():
        depth = []
        for pos in POSITIONS:
            able = [p for p in c["roster"] if pos in player_positions_of(p.get("bnId"), p, report.get(str(p.get("bnId"))))]
            hurt = [p for p in able if str(p.get("bnId")) in injured_ids]
            depth.append({"pos": pos, "total": len(able), "injured": len(hurt), "healthy": len(able) - len(hurt)})
        lists = out[abbr]
        for items in lists.values():
            items.sort(key=lambda e: ([POSITIONS.index(x) for x in e["positions"]] or [9], e["player"]["name"]))
        rows.append({
            "abbr": abbr, "name": c["club"].get("fullName") or c["club"].get("name") or abbr,
            "logo": c["club"].get("logo"), **lists, "depth": depth,
            # only where injuries thin it out: a roster built without a second center is not news
            "short": [d["pos"] for d in depth if d["injured"] and d["healthy"] < SHORT_BELOW],
        })
    rows.sort(key=lambda r: (-len(r["short"]), -len(r["injured"]), r["name"]))
    return rows
