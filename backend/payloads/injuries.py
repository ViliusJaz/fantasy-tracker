"""/api/league/<id>/injuries: injury-report changes as a news feed."""
from backend import history, pipeline
from backend.history import REMOVED_NOTE
from backend.i18n import L
from backend.injuries import health_label, injury_report, loc_comment, return_local
from backend.league import league_meta, owners, standings
from backend.players import player_brief, players


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
                    "comment": comment, "return": return_local(u.get("return")),
                    "player": brief, "owner": owner,
                })
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"league": meta, "events": events[:FEED_LIMIT], "total": len(events),
            "teams": [r["team"] for r in standings(meta)[1]], "reportUrl": meta["injuryReportUrl"]}
