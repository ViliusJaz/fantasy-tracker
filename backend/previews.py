"""Game previews: form, key players, injuries and generated notes for games not played yet."""
from backend.i18n import L
from backend.injuries import injury_report, injury_view
from backend.players import player_brief, players
from backend.sources.advanced import team_advanced
from backend.util import ascii_slug, num, pool_map


def club_results(meta, before_round):
    """Finished games of every club before a round: {abbr: [{at, opp, home, for, against}]} oldest first."""
    rounds = list(range(meta["firstRound"], before_round))
    results = {}
    for _, pm in pool_map(lambda r: (r, players(meta, r, r)), rounds):
        for p in pm.values():
            club = (p["club"] or {}).get("abbr")
            for g in (p["games"] if club else []):
                if not g["completed"] or not g["score"]:
                    continue
                games = results.setdefault(club, {})
                games.setdefault(g["at"], {"at": g["at"], "opp": g["opponent"], "home": g["home"],
                                           "for": g["score"][0], "against": g["score"][1]})
    return {club: sorted(games.values(), key=lambda x: x["at"]) for club, games in results.items()}


# Tokens every other club shares; they would pair e.g. the two Tel Aviv clubs.
_CLUB_NOISE = {"fc", "bc", "basket", "basketball", "tel", "aviv", "istanbul", "belgrade", "athens", "milan"}


def _club_tokens(name):
    return set(ascii_slug(name or "").split("-")) - _CLUB_NOISE - {""}


def club_team_ids(clubs, teams):
    """Fantasy club abbr -> BasketNews team id, matched on the English club name."""
    out = {}
    for abbr, club in clubs.items():
        mine = _club_tokens(club.get("nameEn"))
        best, score = None, 0
        for tid, team in teams.items():
            sc = len(mine & _club_tokens(team["name"])) + (2 if ascii_slug(club.get("name") or "") == ascii_slug(team["short"] or "") else 0)
            if sc > score:
                best, score = tid, sc
        if best is not None:
            out[abbr] = best
    return out


def defense_table(meta):
    """Every club by defense (defensive rating: points allowed per 100 possessions, BasketNews
    team stats; rank 1 = the best): ([{abbr, name, logo, rank, value}], clubs ranked)."""
    teams = team_advanced(meta)
    if not teams:
        return [], 0
    clubs = {v["club"]["abbr"]: v["club"] for v in players(meta, meta["latestRound"], meta["currentRound"]).values()
             if v["club"]}
    abbr_of = {tid: abbr for abbr, tid in club_team_ids(clubs, teams).items()}
    rows = []
    for tid, team in teams.items():
        d = team["stats"]["drtg"]
        if not d["rank"]:
            continue
        club = clubs.get(abbr_of.get(tid)) or {}
        rows.append({"abbr": abbr_of.get(tid), "name": L(club.get("fullName"), club.get("nameEn")) or team["name"],
                     "logo": club.get("logo"), "rank": d["rank"], "value": d["value"]})
    rows.sort(key=lambda r: r["rank"])
    return rows, len(teams)


def club_defense_ranks(meta):
    """{club abbr: defense rank} (see defense_table) and how many clubs are ranked."""
    rows, n = defense_table(meta)
    return {r["abbr"]: r["rank"] for r in rows if r["abbr"]}, n


def preview_context(meta, rnd, pmap, own):
    report = injury_report(meta)
    roster, clubs = {}, {}
    for p in pmap.values():
        if p["club"]:
            roster.setdefault(p["club"]["abbr"], []).append(p)
            clubs[p["club"]["abbr"]] = p["club"]
    # only rounds that can have results (future rounds have none yet)
    results = club_results(meta, min(rnd, meta["currentRound"] + 1))
    allowed = [g["against"] for games in results.values() for g in games]
    teams = team_advanced(meta)
    return {"roster": roster, "results": results, "own": own, "report": report,
            # a game two or more rounds away: today's injury list says little about it
            "farAhead": rnd - meta["currentRound"] >= 2,
            "leagueAllowed": sum(allowed) / len(allowed) if allowed else None,
            "teamStats": {abbr: teams[tid] for abbr, tid in club_team_ids(clubs, teams).items()},
            "teamCount": len(teams)}


def _preview_player(p, own, report):
    season = p["season"] or {}
    inj = injury_view(report.get(p["bnId"]), p["health"])
    return {**player_brief(p), "owner": (own.get(p["id"]) or {}).get("team"),
            "line": {k: season.get(k) for k in ("min", "pts", "reb", "ast")},
            "injury": inj}


def game_preview(g, ctx):
    """What to know before a game: form, key players, injuries and a few generated notes."""
    own, report = ctx["own"], ctx["report"]
    sides, notes = {}, []
    for side in ("home", "away"):
        abbr = g[side]["abbr"]
        roster = ctx["roster"].get(abbr, [])
        games = ctx["results"].get(abbr, [])
        views = [_preview_player(p, own, report) for p in roster]
        active = sorted((v for v in views if v["gamesPlayed"]), key=lambda v: -(v["avgPts"] or 0))
        injured = sorted((v for v in views if v["injury"]), key=lambda v: -(v["avgPts"] or 0))
        wins = sum(1 for x in games if x["for"] > x["against"])
        sides[side] = {
            "record": {"w": wins, "l": len(games) - wins} if games else None,
            "avgFor": round(sum(x["for"] for x in games) / len(games), 1) if games else None,
            "avgAgainst": round(sum(x["against"] for x in games) / len(games), 1) if games else None,
            "last": [{"won": x["for"] > x["against"], "score": [x["for"], x["against"]], "opp": x["opp"],
                      "home": x["home"]} for x in games[-5:]],
            "key": [v for v in active if not (v["injury"] and v["injury"]["status"] == "out")][:4],
            "injuries": injured,
            "team": ctx["teamStats"].get(abbr),
        }
    notes = preview_notes(g, sides, ctx)
    owned = [{"player": {k: v[k] for k in ("id", "name")}, "owner": v["owner"], "club": g[side]["abbr"]}
             for side in ("home", "away")
             for v in (_preview_player(p, own, report) for p in ctx["roster"].get(g[side]["abbr"], []))
             if v["owner"]]
    return {"home": sides["home"], "away": sides["away"], "notes": notes, "owned": owned,
            "teamCount": ctx["teamCount"]}


def team_notes(g, sides, ctx):
    """Notes from the BasketNews team ratings: attack against a weak defence, pace."""
    n = ctx["teamCount"] or 20
    home, away = sides["home"]["team"], sides["away"]["team"]
    if not home or not away or min(home["games"], away["games"]) < 1:
        return []
    notes = []
    top, bottom = max(3, n // 4), n - max(3, n // 4) + 1  # top / bottom quarter of the league
    for team, other, abbr, opp in ((home, away, g["home"]["abbr"], g["away"]["abbr"]),
                                   (away, home, g["away"]["abbr"], g["home"]["abbr"])):
        o, d = team["stats"]["ortg"], other["stats"]["drtg"]
        if o["rank"] and d["rank"] and o["rank"] <= top and d["rank"] >= bottom:
            notes.append(L(f"Palanku {abbr} žaidėjams: puolimas {o['rank']}-as lygoje, o {opp} gynyba tik {d['rank']}-a.",
                           f"Good for {abbr} players: their offense ranks {o['rank']}, {opp}'s defense only {d['rank']}."))
        elif o["rank"] and d["rank"] and o["rank"] >= bottom and d["rank"] <= top:
            notes.append(L(f"Sunkus vakaras {abbr} puolimui: {opp} gynyba {d['rank']}-a lygoje.",
                           f"Tough night for {abbr}'s offense: {opp}'s defense ranks {d['rank']}."))
    ph, pa = home["stats"]["pace"]["rank"], away["stats"]["pace"]["rank"]
    if ph and pa and ph <= top and pa <= top:
        notes.append(L("Abi komandos žaidžia greitu tempu: daugiau atakų, daugiau fantasy taškų.",
                       "Both teams play fast: more possessions, more fantasy points."))
    elif ph and pa and ph >= bottom and pa >= bottom:
        notes.append(L("Abi komandos žaidžia lėtai: mažiau atakų, mažiau taškų.",
                       "Both teams play slowly: fewer possessions, fewer points."))
    return notes


def preview_notes(g, sides, ctx):
    notes = []
    for side, other in (("home", "away"), ("away", "home")):
        abbr, opp = g[side]["abbr"], g[other]["abbr"]
        info = sides[side]
        for v in ([] if ctx["farAhead"] else info["injuries"]):
            status, avg = v["injury"]["status"], v["avgPts"] or 0
            if status == "out" and avg >= 8:
                notes.append(L(f"{abbr} žais be {v['name']} (vid. {num(avg)} FP).",
                               f"{abbr} will be without {v['name']} ({num(avg)} FP avg)."))
            elif status != "out" and avg >= 12:
                notes.append(L(f"{v['name']} ({abbr}) dalyvavimas neaiškus: {v['injury']['label'].lower()}.",
                               f"{v['name']} ({abbr}) is uncertain: {v['injury']['label'].lower()}."))
        if g[side].get("combined"):
            notes.append(L(f"{abbr} šį turą žaidžia daugiau nei vienas rungtynes: jų žaidėjai gali surinkti daugiau taškų.",
                           f"{abbr} play more than once this round, so their players can score more."))
        last = [x["for"] > x["against"] for x in ctx["results"].get(abbr, [])]
        streak = 0
        for won in reversed(last):
            if won != last[-1]:
                break
            streak += 1
        if streak >= 2:
            notes.append(L(f"{abbr} {'laimėjo' if last[-1] else 'pralaimėjo'} {streak} rungtynes iš eilės.",
                           f"{abbr} have {'won' if last[-1] else 'lost'} {streak} in a row."))
        allowed, league = info["avgAgainst"], ctx["leagueAllowed"]
        if allowed is not None and league and len(last) >= 2 and allowed - league >= 5:
            notes.append(L(f"Palanku {opp} puolėjams: {abbr} praleidžia vid. {num(allowed)} tšk. (lygos vid. {num(round(league, 1))}).",
                           f"Good for {opp} scorers: {abbr} allow {num(allowed)} points a game (league {num(round(league, 1))})."))
    notes += team_notes(g, sides, ctx)
    free = [v for side in ("home", "away") for v in sides[side]["key"] if not v["owner"]]
    if free:
        best = max(free, key=lambda v: v["avgPts"] or 0)
        club = next(g[s_]["abbr"] for s_ in ("home", "away") if best in sides[s_]["key"])
        notes.append(L(f"Geriausias laisvasis agentas šiose rungtynėse: {best['name']} ({club}, vid. {num(best['avgPts'])} FP).",
                       f"Best free agent in this game: {best['name']} ({club}, {num(best['avgPts'])} FP avg)."))
    counts = {}
    for side in ("home", "away"):
        for p in ctx["roster"].get(g[side]["abbr"], []):
            team = (ctx["own"].get(p["id"]) or {}).get("team")
            if team:
                counts.setdefault(team["id"], [team, 0])[1] += 1
    if counts:
        team, n = max(counts.values(), key=lambda x: x[1])
        if n >= 2:
            notes.append(L(f"Daugiausia žaidėjų šiose rungtynėse turi {team['title']} ({n}).",
                           f"{team['title']} have the most players in this game ({n})."))
    return notes
