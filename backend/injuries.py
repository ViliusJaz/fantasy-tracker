"""Injury report: the cached current report, statuses and comments in the UI language, and each
player's season injury story from the recorded episodes."""
import http.client
import threading
import time
import urllib.error
import urllib.request
from datetime import date

from backend import clock, history, injury_lt, log
from backend.config import BROWSER_UA, INJURY_TTL
from backend.history import REMOVED_NOTE
from backend.i18n import L, LANG
from backend.net import read_body
from backend.sources.injury_report import DNP_RE, SITE_LABELS, _clean, dnp_reason, is_injury, parse_injury_report


LOG = log.get("fetch")

LT_LABELS = {"ready": "Pasiruošęs", "expected": "Tikėtina", "questionable": "Abejojama",
             "game-time": "Prieš rungtynes", "doubtful": "Mažai tikėtina", "out": "Nežaidžia",
             "uncertain": "Neaišku"}


def health_label(key):
    return L(LT_LABELS.get(key, key), SITE_LABELS.get(key, key))


_untranslated = set()


def _lt(text, translate):
    """Lithuanian wording from injury_lt, the first injury phrase in it, or the original."""
    lt = translate(text) or injury_lt.gist(text)
    if lt is None and text not in _untranslated:
        _untranslated.add(text)
        LOG.debug("injury comment not translated to Lithuanian: %s", text)
    return lt or text


def untranslated():
    """Report comments seen so far that injury_lt could not translate."""
    return sorted(_untranslated)


def loc_reason(text):
    """A short report reason ('knee injury', "coach's decision") in the UI language."""
    text = _clean(text).rstrip(".")
    if LANG.get() == "en" or not text:
        return text
    return _lt(text, lambda t: injury_lt.reason(t) or injury_lt.translate(t))


def loc_comment(text):
    """A whole report comment in the UI language."""
    if text == REMOVED_NOTE:
        return L(REMOVED_NOTE, "Removed from the injury report")
    text = _clean(text)
    if LANG.get() == "en" or not text:
        return text
    return _lt(text, injury_lt.translate)


_injury_cache = {}

_injury_lock = threading.Lock()


def injury_report(meta):
    """Current injury report of the league's competition: {bnPlayerId: entry}."""
    url = meta.get("injuryReportUrl")
    if not url:
        return {}
    with _injury_lock:
        hit = _injury_cache.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en,lt",
                                                   "Accept-Encoding": "gzip"})
        try:
            entries = parse_injury_report(read_body(req).decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            LOG.warning("injury report unavailable (%s): %s", url, exc)
            if hit:
                return hit[1]
            return {}
        by_player = {e["bnId"]: e for e in entries if e["bnId"]}
        history.observe_injury_report(entries)
        _injury_cache[url] = (time.time() + INJURY_TTL, by_player)
        return by_player


def injury_view(entry, health=None):
    """Compact current status for lists: None when the player is fine."""
    if entry and entry["status"] != "ready":
        key = entry["status"]
        return {
            "status": key,
            "label": L(LT_LABELS.get(key, key), entry.get("siteLabel") or SITE_LABELS.get(key, key)),
            "return": return_local(entry["return"]), "comment": loc_comment(entry["comment"]),
        }
    if health and health != "ready":
        return {"status": health, "label": health_label(health), "return": "", "comment": ""}
    return None


def return_local(text):
    t = _clean(text)
    return t if LANG.get() == "en" or not t else injury_lt.return_text(t)


def _day(iso):
    return iso[:10] if iso else None


def build_injury_history(bn_id, game_log):
    """Season injury story of one player from the recorded episodes and the rounds he missed."""
    rec = history.injury_log().get(bn_id) or {"episodes": []}
    round_dates = {g["round"]: g["date"] for g in game_log if g.get("date")}
    missed = [g for g in game_log if g["status"] == "dnp"]
    today = clock.today()
    episodes = []
    for ep in rec["episodes"]:
        comments = [u["comment"] for u in ep["updates"] if u.get("comment")]
        start = ep["start"]
        for c in comments:
            for m in DNP_RE.finditer(c):
                d = round_dates.get(int(m.group(1)) - 1)
                if d and d < start:
                    start = d
        # Rounds missed back-to-back right before the player showed up in the report
        # belong to the same absence.
        for g in sorted((g for g in game_log if g.get("date") and g["date"] < start),
                        key=lambda g: g["date"], reverse=True):
            if g["status"] == "played":
                break
            if g["status"] == "dnp":
                start = g["date"]
        end = ep["end"]
        last_real = next((u for u in reversed(ep["updates"]) if u["status"] != "ready"), ep["updates"][0])
        rnd_hint, reason = dnp_reason(last_real["comment"])
        kind = "injury" if any(is_injury(c) for c in comments) else "other"
        until = date.fromisoformat(end) if end else today
        ep_missed = [g["round"] for g in missed
                     if g.get("date") and start <= g["date"] <= (end or today.isoformat())]
        episodes.append({
            "kind": kind,
            "reason": loc_reason(reason),
            "start": start,
            "end": end,
            "days": max((until - date.fromisoformat(start)).days, 0),
            "ongoing": end is None,
            "status": last_real["status"],
            "statusLabel": health_label(last_real["status"]),
            "return": return_local(last_real.get("return")),
            "missedRounds": ep_missed,
            "updates": [
                {"date": u["date"], "statusLabel": health_label(u["status"]),
                 "return": return_local(u.get("return")), "comment": loc_comment(u.get("comment"))}
                for u in ep["updates"]
            ],
        })
    episodes.reverse()
    return {"episodes": episodes, "missed": [g["round"] for g in missed],
            "summary": injury_summary(episodes, missed, game_log)}


def _plural_rounds(n):
    if LANG.get() == "en":
        return f"{n} round" if n == 1 else f"{n} rounds"
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} turą"
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return f"{n} turus"
    return f"{n} turų"


def injury_summary(episodes, missed, game_log):
    played = [g for g in game_log if g["status"] == "played"]
    team_games = [g for g in game_log if g["status"] in ("played", "dnp")]
    parts = []
    injuries = [e for e in episodes if e["kind"] == "injury"]
    if not missed and not episodes:
        if team_games:
            return L(f"Šį sezoną nepraleido nė vieno turo: sužaidė {len(played)} iš {len(team_games)}.",
                     f"Has not missed a round this season: played {len(played)} of {len(team_games)}.")
        return L("Šį sezoną dar nežaidė ir traumų sąraše nebuvo.",
                 "Has not played yet this season and has not been on the injury report.")
    if missed:
        rounds = ", ".join(str(g["round"] + 1) for g in missed)
        parts.append(L(f"Šį sezoną praleido {_plural_rounds(len(missed))} (turai: {rounds}).",
                       f"Missed {_plural_rounds(len(missed))} this season (rounds: {rounds})."))
    else:
        parts.append(L("Šį sezoną nepraleido nė vieno turo.", "Has not missed a round this season."))
    for e in injuries:
        what = e["reason"] or L("trauma", "injury")
        if e["end"]:
            span = L(f"nuo {e['start'][5:]} iki {e['end'][5:]}", f"from {e['start'][5:]} to {e['end'][5:]}")
        else:
            span = L(f"nuo {e['start'][5:]}, tęsiasi", f"since {e['start'][5:]}, ongoing")
        parts.append(f"{what[:1].upper() + what[1:]}: {span} ({e['days']} {L('d.', 'days')}).")
    others = [e for e in episodes if e["kind"] == "other"]
    if others and not injuries:
        reason = others[0]["reason"]
        if reason and missed:
            parts.append(L(f"Traumų nebuvo. Priežastis: {reason}.", f"No injuries. Reason: {reason}."))
        else:
            parts.append(L("Traumų nebuvo.", "No injuries."))
    return " ".join(parts)
