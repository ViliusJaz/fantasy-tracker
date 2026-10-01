"""/api/league/<id>/games: every real game of a round with box scores and previews."""
from backend.league import league_meta, owners
from backend.players import players
from backend.predictions import predict_games
from backend.previews import game_preview, preview_context
from backend.rounds import round_state


def games_payload(fid, rnd=None):
    """Every real game of a round with each player's box score and fantasy owner; future
    rounds list their scheduled games with previews."""
    meta = league_meta(fid)
    last = (meta.get("totalRounds") or meta["currentRound"] + 1) - 1
    # the tab opens on the current round (live, or the next one to be played)
    rnd = meta["currentRound"] if rnd is None else max(meta["firstRound"], min(rnd, last))
    pmap = players(meta, rnd, rnd)
    own = owners(meta, rnd)
    clubs, games = {}, {}
    for p in pmap.values():
        club = p["club"]
        if not club:
            continue
        c = clubs.setdefault(club["abbr"], {"abbr": club["abbr"], "name": club.get("fullName") or club.get("name"),
                                            "logo": club["logo"], "players": [], "gameCount": len(p["games"])})
        if p["roundLine"]:
            c["players"].append({"id": p["id"], "name": p["name"], "photo": p["photo"], "fp": p["roundPts"],
                                 "line": p["roundLine"], "owner": own.get(p["id"]), "position": p["position"],
                                 **({"live": True} if p.get("roundLive") else {})})
        for g in p["games"]:
            home, away = (club["abbr"], g["opponent"]) if g["home"] else (g["opponent"], club["abbr"])
            key = (g["at"], home, away)
            if key in games:
                continue
            score = g["score"]
            games[key] = {
                "id": f"{home}-{away}-{g['at'][:10]}", "at": g["at"], "home": home, "away": away,
                "homeScore": (score[0] if g["home"] else score[1]) if score else None,
                "awayScore": (score[1] if g["home"] else score[0]) if score else None,
                "live": g["live"], "completed": g["completed"], "canceled": g["canceled"],
                "opponentLogo": {away if g["home"] else home: g.get("opponentLogo")},
                **({"period": g["period"]} if g.get("period") else {}),  # quarter and clock of a live game
            }
    out = []
    for key in sorted(games, key=lambda k: (k[0], k[1])):
        g = games[key]
        sides = {}
        for side in ("home", "away"):
            c = clubs.get(g[side]) or {"abbr": g[side], "name": g[side], "logo": g["opponentLogo"].get(g[side]),
                                       "players": [], "gameCount": 1}
            plist = sorted(c["players"], key=lambda x: -(x["fp"] if x["fp"] is not None else -99))
            sides[side] = {"abbr": c["abbr"], "name": c["name"], "logo": c["logo"] or g["opponentLogo"].get(g[side]),
                           "players": plist, "combined": c["gameCount"] > 1}
        g.pop("opponentLogo")
        g.update(sides)
        g["owned"] = sum(1 for s_ in ("home", "away") for x in sides[s_]["players"] if x["owner"])
        top = max((x for s_ in ("home", "away") for x in sides[s_]["players"]),
                  key=lambda x: x["fp"] if x["fp"] is not None else -99, default=None)
        g["top"] = {"name": top["name"], "fp": top["fp"]} if top else None
        out.append(g)
    upcoming = [g for g in out if not g["completed"] and not g["live"] and not g["canceled"]]
    if upcoming:
        ctx = preview_context(meta, rnd, pmap, own)
        clubs = {p["club"]["abbr"]: p["club"] for p in pmap.values() if p["club"]}
        picks = predict_games(meta, [(g["id"], g["home"]["abbr"], g["away"]["abbr"], g["at"]) for g in upcoming], clubs)
        for g in upcoming:
            g["preview"] = game_preview(g, ctx)
            g["preview"]["prediction"] = picks.get(g["id"])
    return {"league": meta, "round": rnd, "state": round_state(meta, rnd), "games": out}
