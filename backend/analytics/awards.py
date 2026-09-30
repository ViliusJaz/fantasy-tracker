"""Award cards for the season records page: round awards, season "Oscars", draft awards, records."""
from backend.i18n import L, LANG
from backend.league import lineups, moved_players, owners, raw_transfers
from backend.players import players, players_by_ids
from backend.sources import basketnews as bn
from backend.util import num


def rnd_word(r):
    """'3 turas' / 'Round 3' for a 0-based round."""
    return L(f"{r + 1} turas", f"Round {r + 1}")


def teams_word(n):
    if LANG.get() == "en":
        return f"{n} teams"
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} komanda"
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return f"{n} komandos"
    return f"{n} komandų"


def award(title, name, value, sub="", icon="", team_id=None, player_id=None, info=None):
    """One award card; `info` explains how a less obvious award is worked out."""
    return {"title": title, "icon": icon, "name": name, "value": value, "sub": sub,
            "teamId": team_id, "playerId": player_id, "info": info}


def no_award(title, sub, icon="", info=None):
    return award(title, L("Dar nėra", "None yet"), "", sub, icon, info=info)


def round_awards(meta, bd):
    teams, scores, rnd = bd["teams"], bd["scores"], bd["round"]
    cards = []
    if scores:
        hi = max(scores, key=scores.get)
        lo = min(scores, key=scores.get)
        cards.append(award(L("Daugiausiai surinko", "Most points"), teams[hi]["title"], num(scores[hi]), team_id=hi))
        cards.append(award(L("Mažiausiai surinko", "Fewest points"), teams[lo]["title"], num(scores[lo]), team_id=lo))
    decided = [g for g in bd["games"] if not g["tie"]]
    if decided:
        big = max(decided, key=lambda g: g["margin"])
        small = min(decided, key=lambda g: g["margin"])
        unlucky = max(decided, key=lambda g: g["ls"])
        lucky = min(decided, key=lambda g: g["ws"])
        cards += [
            award(L("Didžiausia pergalė", "Biggest win"), big["winner"]["title"], f"+{num(big['margin'])}",
                  f"{num(big['ws'])} : {num(big['ls'])} {L('prieš', 'vs')} {big['loser']['title']}",
                  team_id=big["winner"]["id"]),
            award(L("Mažiausias skirtumas", "Closest win"), small["winner"]["title"], f"+{num(small['margin'])}",
                  f"{num(small['ws'])} : {num(small['ls'])} {L('prieš', 'vs')} {small['loser']['title']}",
                  team_id=small["winner"]["id"]),
            award(L("Nelaimingiausias pralaimėjimas", "Unluckiest loss"), unlucky["loser"]["title"], num(unlucky["ls"]),
                  L(f"Daugiausia taškų pralaimėjus (prieš {unlucky['winner']['title']})",
                    f"Most points in a loss (vs {unlucky['winner']['title']})"), team_id=unlucky["loser"]["id"]),
            award(L("Laimingiausia pergalė", "Luckiest win"), lucky["winner"]["title"], num(lucky["ws"]),
                  L(f"Mažiausiai taškų laimėjus (prieš {lucky['loser']['title']})",
                    f"Fewest points in a win (vs {lucky['loser']['title']})"), team_id=lucky["winner"]["id"]),
        ]
    lus = bd["lineups"]
    if not lus:
        cards.append(award(L("Sudėčių apdovanojimai", "Lineup awards"), L("Nėra duomenų", "No data"), "",
                           L(f"{rnd + 1} turo sudėtys nebuvo išsaugotos, todėl MVP, kapitonų ir prarastų taškų "
                             "apdovanojimų apskaičiuoti negalima.",
                             f"Round {rnd + 1} lineups were not saved, so MVP, captain and points-lost awards "
                             "cannot be calculated.")))
        return cards
    active = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["slot"] != "inactive"]
    mvp_tid, mvp = max(active, key=lambda x: x[1].get("roundPts") or -999)
    caps = [(tid, p) for tid, lu in lus.items() for p in lu["players"] if p["captain"]]
    lost_tid = max(lus, key=lambda t: lus[t]["lost"])
    cards.append(award(L("Turo MVP žaidėjas", "Round MVP"), mvp["name"], num(mvp.get("roundPts")),
                       teams.get(mvp_tid, {}).get("title", ""), player_id=mvp["id"]))
    if caps:
        best_tid, best_cap = max(caps, key=lambda x: x[1]["contrib"])
        worst_tid, worst_cap = min(caps, key=lambda x: x[1]["contrib"])
        cards.append(award(L("Geriausias kapitonas", "Best captain"), best_cap["name"], num(best_cap["contrib"]),
                           teams.get(best_tid, {}).get("title", ""), player_id=best_cap["id"]))
        cards.append(award(L("Nesėkmingiausias kapitonas", "Worst captain"), worst_cap["name"], num(worst_cap["contrib"]),
                           teams.get(worst_tid, {}).get("title", ""), player_id=worst_cap["id"]))
    cards.append(award(L("Daugiausiai prarado dėl sudėties", "Most points lost to lineup"), teams.get(lost_tid, {}).get("title", ""),
                       f"−{num(lus[lost_tid]['lost'])}" if lus[lost_tid]["lost"] else "0",
                       L(f"Su optimalia sudėtimi būtų {num(lus[lost_tid]['optimal'])}", f"Optimal lineup: {num(lus[lost_tid]['optimal'])}"), team_id=lost_tid))
    return cards


def season_oscars(meta, breakdowns, team_names):
    with_lineups = [bd for bd in breakdowns if bd["lineups"]]
    name = lambda tid: team_names.get(tid, "")  # noqa: E731
    cards = []
    if with_lineups:
        cap_total, lost_total, contrib = {}, {}, {}
        best_pick = None
        for bd in with_lineups:
            for tid, lu in bd["lineups"].items():
                lost_total[tid] = lost_total.get(tid, 0) + lu["lost"]
                for p in lu["players"]:
                    if p["captain"]:
                        cap_total[tid] = cap_total.get(tid, 0) + p["contrib"]
                        if best_pick is None or p["contrib"] > best_pick[2]["contrib"]:
                            best_pick = (bd["round"], tid, p)
                    if p["contrib"]:
                        key = (p["id"], tid)
                        pname, total, rounds = contrib.get(key, (p["name"], 0, set()))
                        rounds.add(bd["round"])
                        contrib[key] = (pname, total + p["contrib"], rounds)
        if cap_total:
            king = max(cap_total, key=cap_total.get)
            flop = min(cap_total, key=cap_total.get)
            cards.append(award(L("Kapitonų karalius", "Captain king"), name(king), num(cap_total[king]),
                               L("daugiausia kapitonų taškų per sezoną", "most captain points this season"), "🏆", team_id=king))
        if best_pick:
            r, tid, p = best_pick
            cards.append(award(L("Geriausias kapitono pasirinkimas", "Best captain pick"), p["name"], num(p["contrib"]),
                               f"{name(tid)}, {rnd_word(r)}", "🎯", player_id=p["id"]))
        if cap_total:
            cards.append(award(L("Kapitonų nesėkmė", "Captain flop"), name(flop), num(cap_total[flop]),
                               L("mažiausiai kapitonų taškų", "fewest captain points"), "🙈", team_id=flop))
        bench = max(lost_total, key=lost_total.get)
        sharp = min(lost_total, key=lost_total.get)
        cards.append(award(L("Suolo karalius", "Bench king"), name(bench), f"−{num(lost_total[bench])}" if lost_total[bench] else "0",
                           L("daugiausia taškų paliko ant suolo", "most points left on the bench"), "🪑", team_id=bench))
        cards.append(award(L("Tiksliausias treneris", "Sharpest coach"), name(sharp), num(lost_total[sharp]),
                           L("mažiausiai prarado dėl sudėties", "fewest points lost to lineup"), "🧠", team_id=sharp))
        if contrib:
            (pid, tid), (pname, total, rounds) = max(contrib.items(), key=lambda kv: kv[1][1])
            cards.append(award(L("Sezono MVP", "Season MVP"), pname, num(total), f"{name(tid)} · {len(rounds)} {L('tur.', 'rd' if len(rounds) == 1 else 'rds')}", "⭐",
                               player_id=pid))
        cards.append(best_transfer(with_lineups, team_names))
    else:
        cards.append(award(L("Sudėčių apdovanojimai", "Lineup awards"), L("Nėra duomenų", "No data"), "",
                           L("Nėra išsaugotų turų sudėčių.", "No saved round lineups.")))
    best_round = max(((bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]), default=None)
    if best_round:
        pts, tid, r = best_round
        cards.append(award(L("Sezono turas", "Round of the season"), name(tid), num(pts), rnd_word(r), "🔥", team_id=tid))
    return cards


def best_transfer(with_lineups, team_names):
    """Player added after the first recorded round who has given his new team the most points."""
    initial = {tid: {p["id"] for p in lu["players"]} for tid, lu in with_lineups[0]["lineups"].items()}
    gained = {}
    for bd in with_lineups[1:]:
        for tid, lu in bd["lineups"].items():
            for p in lu["players"]:
                if p["id"] in initial.get(tid, set()):
                    continue
                key = (p["id"], tid)
                pname, total = gained.get(key, (p["name"], 0))
                gained[key] = (pname, total + p["contrib"])
    first = rnd_word(with_lineups[0]["round"])
    info = L(
        f"Žaidėjas, kurio komanda neturėjo {first.replace('turas', 'ture')}, t. y. vėliau paimtas iš laisvųjų "
        "agentų arba gautas mainais. Skaičiuojami tik taškai, kuriuos jis atnešė naujajai komandai nuo "
        "įsigijimo, su sudėties koeficientais: startinis penketas ir 6-as žaidėjas ×1, kapitonas ×2, "
        "atsarginiai ×0,5, neregistruoti 0. Sumuojami tik turai, kurių sudėtys išsaugotos. "
        "Laimi daugiausia taškų atnešęs įsigijimas.",
        f"A player who was not on the team's roster in {first}, i.e. picked up later from free agency or "
        "via a trade. Only the points earned for the new team since the move count, with lineup "
        "multipliers: starters and 6th man ×1, captain ×2, bench ×0.5, not registered 0. Only rounds "
        "with a saved lineup are added up. The acquisition with the most points wins.")
    if not gained:
        return no_award(L("Sezono sandoris", "Deal of the season"), L("atsiras po pirmųjų perėjimų", "appears after the first transfers"),
                        "🤝", info=info)
    (pid, tid), (pname, total) = max(gained.items(), key=lambda kv: kv[1][1])
    return award(L("Sezono sandoris", "Deal of the season"), pname, num(total),
                 f"{team_names.get(tid, '')} · {L('taškai nuo įsigijimo', 'points since acquired')}", "🤝",
                 player_id=pid, info=info)


def draft_awards(meta, finished, team_names):
    """Awards about the draft and the moves since: busts, steals, drops and roster value."""
    picks = [p for p in bn.fetch_draft_picks(meta["id"]) if p["playerId"]]
    if not picks or not finished:
        return []
    per_round = len(team_names) or 8
    drafted = {p["playerId"]: (i + 1, p["teamId"]) for i, p in enumerate(picks)}
    pmap = players_by_ids(meta, list(drafted), meta["latestRound"], meta["currentRound"])
    avg = lambda pid: (pmap.get(pid) or {}).get("avgPts") or 0  # noqa: E731
    games = lambda pid: (pmap.get(pid) or {}).get("gamesPlayed") or 0  # noqa: E731
    name = lambda pid: (pmap.get(pid) or {}).get("name") or "?"  # noqa: E731
    team = lambda tid: team_names.get(tid, "?")  # noqa: E731
    pick_word = lambda n: L(f"{n}-as pasirinkimas", f"pick #{n}")  # noqa: E731
    own = owners(meta, meta["currentRound"])
    holder = lambda pid: ((own.get(pid) or {}).get("team") or {}).get("title") or L("laisvasis agentas", "free agent")  # noqa: E731
    cards = []

    # Biggest bust: early pick furthest below the average of its own draft round.
    early = picks[:3 * per_round]
    round_avg = {}
    for i, p in enumerate(early):
        round_avg.setdefault(i // per_round, []).append(avg(p["playerId"]))
    busts = [(sum(round_avg[i // per_round]) / len(round_avg[i // per_round]) - avg(p["playerId"]), i, p)
             for i, p in enumerate(early)]
    gap, i, p = max(busts, key=lambda x: x[0])
    pid = p["playerId"]
    expected = gap + avg(pid)
    cards.append(award(
        L("Didžiausias nusivylimas", "Biggest bust"), name(pid), f"{num(avg(pid))} FP",
        f"{pick_word(i + 1)} ({team(p['teamId'])}) · {L('rato vidurkis', 'round average')} {num(round(expected, 1))}",
        "📉", player_id=pid,
        info=L("Iš pirmųjų trijų drafto ratų: žaidėjas, kurio vidutiniai FP labiausiai atsilieka nuo to paties "
               "drafto rato pasirinkimų vidurkio. Dar nežaidę skaičiuojami kaip 0.",
               "From the first three draft rounds: the player whose average FP is furthest below the "
               "average of the picks in the same draft round. Players who have not played count as 0.")))

    # Best undrafted player (a regular: at least half of the finished rounds played).
    min_games = max(1, (len(finished) + 1) // 2)
    pool = players(meta, meta["latestRound"], meta["currentRound"])
    undrafted = [v for v in pool.values() if v["id"] not in drafted and (v["gamesPlayed"] or 0) >= min_games and v["avgPts"]]
    info = L(f"Žaidėjas, kurio drafte niekas nepasirinko. Didžiausi vidutiniai FP tarp sužaidusių bent {min_games} rungt.",
             f"A player nobody picked in the draft. Highest average FP among players with at least {min_games} games.")
    if undrafted:
        best = max(undrafted, key=lambda v: (v["avgPts"], v["gamesPlayed"]))
        cards.append(award(L("Geriausias nedraftuotas", "Best undrafted player"), best["name"], f"{num(best['avgPts'])} FP",
                           f"{L('dabar', 'now')}: {holder(best['id'])}", "💎", player_id=best["id"], info=info))
    else:
        cards.append(no_award(L("Geriausias nedraftuotas", "Best undrafted player"), L("dar nėra", "none yet"), "💎", info=info))

    # Drops: players a team let go in a free-agent move.
    drops = []
    for rnd, ts in raw_transfers(meta):
        for t in ts:
            tid = (t["offer"]["team"] or {}).get("id")
            if t["kind"] != "trade" and tid:
                drops += [(rnd, t["at"] or "", tid, pid) for pid in moved_players(t["offer"])]
    none_yet = L("atsiras po pirmųjų išmetimų", "appears after the first drops")
    before = lambda r: L(f"prieš {r + 1} turą", f"before round {r + 1}")  # noqa: E731
    drop_titles = (L("Geriausias išmestas", "Best player who was dropped"),
                   L("Labiausiai gailimas išmetimas", "Most regrettable drop"))
    drop_info = (
        L("Iš visų išmestų žaidėjų: didžiausi šio sezono vidutiniai FP.",
          "Of all dropped players: the highest average FP this season."),
        L("Kiek FP žaidėjas surinko baigtuose turuose po to, kai komanda jį išmetė. Laimi daugiausia surinkęs.",
          "FP the player scored in finished rounds after the team let the player go. The most wins."))
    if not drops:
        cards += [no_award(title, none_yet, icon, info=info) for title, icon, info in zip(drop_titles, ("🗑️", "😬"), drop_info)]
    else:
        rnd, _, tid, pid = max(drops, key=lambda d: avg(d[3]))
        cards.append(award(drop_titles[0], name(pid), f"{num(avg(pid))} FP",
                           f"{L('išmetė', 'dropped by')} {team(tid)} {before(rnd)} · {L('dabar', 'now')}: {holder(pid)}",
                           "🗑️", player_id=pid, info=drop_info[0]))
        after = {}
        for r in finished:
            round_pts = {v["id"]: v.get("roundPts") or 0 for v in players(meta, r, r).values()}
            for d in drops:
                if r >= d[0]:
                    after[d] = after.get(d, 0) + round_pts.get(d[3], 0)
        worst = max(drops, key=lambda d: after.get(d, 0))
        rnd, _, tid, pid = worst
        if after.get(worst, 0) > 0:
            cards.append(award(drop_titles[1], team(tid), f"{num(after[worst])} FP",
                               f"{L('išmetė', 'dropped')} {name(pid)} {before(rnd)}", "😬", team_id=tid, info=drop_info[1]))
        else:  # nobody dropped has played a finished round since
            cards.append(no_award(drop_titles[1], L("atsiras po pirmo sužaisto turo", "appears after the next finished round"),
                                  "😬", info=drop_info[1]))

    # Best draft-day roster: season FP of everyone a team drafted, wherever they play now.
    by_team = {}
    for pid, (_, tid) in drafted.items():
        by_team[tid] = by_team.get(tid, 0) + avg(pid) * games(pid)
    tid, total = max(by_team.items(), key=lambda kv: kv[1])
    cards.append(award(L("Geriausia drafto sudėtis", "Best draft-day roster"), team(tid), f"{num(round(total, 2))} FP",
                       L("drafte pasirinktų žaidėjų taškai šį sezoną", "season FP of the players drafted"), "📋",
                       team_id=tid,
                       info=L("Visų komandos drafte pasirinktų žaidėjų šio sezono fantasy taškų suma, nesvarbu, "
                              "ar jie vis dar komandoje.",
                              "Season fantasy points of every player the team drafted, whether or not they are "
                              "still on the team.")))

    # Best current roster versus the draft-day one (sum of average FP).
    current = {t: sum(avg(p["id"]) for p in lu["players"]) for t, lu in lineups(meta).items()}
    original = {t: sum(avg(pid) for pid, (_, owner) in drafted.items() if owner == t) for t in current}
    diff = {t: current[t] - original.get(t, 0) for t in current}
    info = L("Dabartinės sudėties žaidėjų vidutinių FP suma minus drafto sudėties suma: kiek komandai padėjo "
             "perėjimai ir mainai.",
             "Sum of average FP of the current roster minus that of the draft-day roster: how much the "
             "moves and trades helped.")
    if any(abs(v) > 0.01 for v in diff.values()):
        tid = max(diff, key=diff.get)
        cards.append(award(L("Labiausiai pagerėjusi sudėtis", "Best current vs original roster"), team(tid),
                           f"{'+' if diff[tid] >= 0 else ''}{num(round(diff[tid], 1))} FP",
                           L(f"dabar {num(round(current[tid], 1))}, drafte {num(round(original[tid], 1))}",
                             f"now {num(round(current[tid], 1))}, at the draft {num(round(original[tid], 1))}"),
                           "📈", team_id=tid, info=info))
    else:
        cards.append(no_award(L("Labiausiai pagerėjusi sudėtis", "Best current vs original roster"),
                              L("atsiras po pirmųjų perėjimų", "appears after the first transfers"), "📈", info=info))
    return cards


def season_records(meta, breakdowns, team_names, streaks, totals):
    cards = []
    rows = [(bd["scores"][t], t, bd["round"]) for bd in breakdowns for t in bd["scores"]]
    opponent = {}
    for bd in breakdowns:
        for g in bd["games"]:
            opponent[(g["winner"]["id"], bd["round"])] = g["loser"]["title"]
            opponent[(g["loser"]["id"], bd["round"])] = g["winner"]["title"]
    vs = lambda t, r: f", {L('prieš', 'vs')} {opponent[(t, r)]}" if (t, r) in opponent else ""  # noqa: E731
    if rows:
        hi, lo = max(rows), min(rows)
        cards.append(award(L("Daugiausia taškų per turą", "Most points in a round"), team_names.get(hi[1], ""), num(hi[0]),
                           f"{rnd_word(hi[2])}{vs(hi[1], hi[2])}", team_id=hi[1]))
        cards.append(award(L("Mažiausiai taškų per turą", "Fewest points in a round"), team_names.get(lo[1], ""), num(lo[0]),
                           f"{rnd_word(lo[2])}{vs(lo[1], lo[2])}", team_id=lo[1]))
    games = [(g, bd["round"]) for bd in breakdowns for g in bd["games"] if not g["tie"]]
    if games:
        big = max(games, key=lambda x: x[0]["margin"])
        small = min(games, key=lambda x: x[0]["margin"])
        unlucky = max(games, key=lambda x: x[0]["ls"])
        lucky = min(games, key=lambda x: x[0]["ws"])
        cards += [
            award(L("Didžiausia pergalė", "Biggest win"), big[0]["winner"]["title"], f"+{num(big[0]['margin'])}",
                  f"{num(big[0]['ws'])} : {num(big[0]['ls'])} {L('prieš', 'vs')} {big[0]['loser']['title']}, "
                  f"{rnd_word(big[1])}",
                  team_id=big[0]["winner"]["id"]),
            award(L("Mažiausias skirtumas", "Closest win"), small[0]["winner"]["title"], f"+{num(small[0]['margin'])}",
                  f"{num(small[0]['ws'])} : {num(small[0]['ls'])} {L('prieš', 'vs')} {small[0]['loser']['title']}, "
                  f"{rnd_word(small[1])}",
                  team_id=small[0]["winner"]["id"]),
            award(L("Nelaimingiausias pralaimėjimas", "Unluckiest loss"), unlucky[0]["loser"]["title"], num(unlucky[0]["ls"]),
                  L(f"Pralaimėjo {unlucky[0]['winner']['title']}", f"Lost to {unlucky[0]['winner']['title']}")
                  + f", {rnd_word(unlucky[1])}", team_id=unlucky[0]["loser"]["id"]),
            award(L("Laimingiausia pergalė", "Luckiest win"), lucky[0]["winner"]["title"], num(lucky[0]["ws"]),
                  L(f"Laimėjo prieš {lucky[0]['loser']['title']}", f"Beat {lucky[0]['loser']['title']}")
                  + f", {rnd_word(lucky[1])}", team_id=lucky[0]["winner"]["id"]),
        ]
        for idx, title, sub in ((0, L("Ilgiausia pergalių serija", "Longest winning streak"),
                                 L("pergalės iš eilės", "wins in a row")),
                                (1, L("Ilgiausia pralaimėjimų serija", "Longest losing streak"),
                                 L("pralaimėjimai iš eilės", "losses in a row"))):
            longest = {t: s[idx] for t, s in streaks.items()}
            top = max(longest.values(), default=0)
            holders = [t for t, v in longest.items() if v == top and top > 0]
            if not holders:
                cards.append(no_award(title, sub))
            elif len(holders) == 1:
                cards.append(award(title, team_names.get(holders[0], ""), str(top), sub, team_id=holders[0]))
            else:
                cards.append(award(title, teams_word(len(holders)), str(top), f"{sub} ({L('žr. lentelę', 'see table')})"))
    if totals:
        played = len(breakdowns)
        most = max(totals, key=totals.get)
        least = min(totals, key=totals.get)
        cards.append(award(L("Daugiausia taškų per sezoną", "Most season points"), team_names.get(most, ""), num(totals[most]),
                           L(f"Vidurkis {num(totals[most] / played)} per turą", f"Average {num(totals[most] / played)} per round"), team_id=most))
        cards.append(award(L("Mažiausiai taškų per sezoną", "Fewest season points"), team_names.get(least, ""), num(totals[least]),
                           L(f"Vidurkis {num(totals[least] / played)} per turą", f"Average {num(totals[least] / played)} per round"), team_id=least))
    return cards
