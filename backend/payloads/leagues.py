"""/api/leagues: the tracked leagues, and adding / removing one."""
import re

from backend import config
from backend.config import _config_lock, load_config
from backend.errors import NotFound, UpstreamError
from backend.i18n import L
from backend.league import league_meta, standings
from backend.util import pool_map, write_json


def leagues_payload():
    def card(entry):
        try:
            meta = league_meta(entry["id"])
            shown, rows = standings(meta)
            return {"league": meta, "round": shown, "leader": rows[0] if rows else None, "teams": len(rows)}
        except (UpstreamError, NotFound) as exc:
            return {"league": {"id": entry["id"], "title": entry.get("title") or entry["id"]}, "error": str(exc)}
    return {"leagues": pool_map(card, load_config())}


LEAGUE_ID_RE = re.compile(r"([0-9a-f]{24})")


def add_league(text):
    m = LEAGUE_ID_RE.search(text or "")
    if not m:
        raise ValueError(L("Nuorodoje nerastas lygos ID", "No league ID found in the link"))
    fid = m.group(1)
    meta = league_meta(fid)  # validates the league exists
    with _config_lock:
        entries = load_config()
        if any(e["id"] == fid for e in entries):
            raise ValueError(L("Ši lyga jau pridėta", "This league is already added"))
        entries.append({"id": fid, "title": meta["title"]})
        write_json(config.LEAGUES_FILE, entries)
    return meta


def remove_league(fid):
    with _config_lock:
        write_json(config.LEAGUES_FILE, [e for e in load_config() if e["id"] != fid])
