"""Advanced statistics in context: league averages, quartiles and percentiles for the player card."""
import bisect
import copy

from backend.i18n import L
from backend.players import players
from backend.sources.advanced import adv_value, advanced_stats, unique_rows


# (key, short, LT title, EN title, LT explanation, EN explanation)
ADV_GROUPS = [
    ("offense", ("Puolimas", "Offense"), [
        ("usage_percentage", "USG%", "Naudojimo dažnis", "Usage %",
         "Kokią dalį komandos atakų žaidėjas užbaigia pats (metimu, baudomis ar klaida), kol yra aikštėje. Kuo jis didesnis, tuo daugiau puolimo eina per jį.",
         "Share of team possessions a player finishes himself (shot, free throws or turnover) while on court. The higher, the more the offense runs through him."),
        ("ts_percentage", "TS%", "Tikrasis metimų taiklumas", "True shooting %",
         "Metimų efektyvumas, įskaitant tritaškių vertę ir baudas: TŠK ÷ (2 × (metimai + 0.44 × baudų metimai)). Geriau nei paprastas taiklumas.",
         "Shooting efficiency that credits threes and free throws: PTS ÷ (2 × (FGA + 0.44 × FTA)). Better than plain FG%."),
        ("offensive_rating_ind", "IORTG", "Individualus puolimo reitingas", "Individual offensive rating",
         "Kiek taškų žaidėjas sukuria per 100 atakų, kurias jis naudoja (savo taškai, rez. perdavimai, atkovoti kamuoliai puolime).",
         "Points a player produces per 100 possessions he uses (own scoring, assists, offensive rebounds)."),
        ("assist_percentage", "AST%", "Rez. perdavimų dalis", "Assist %",
         "Kokia dalis žaidėjo atakų baigiasi rezultatyviu perdavimu, o ne metimu ar klaida.",
         "Share of the player's plays that end with an assist rather than a shot or turnover."),
        ("turnover_percentage", "TOV%", "Klaidų dalis", "Turnover %",
         "Kokia dalis žaidėjo atakų baigiasi klaida. Mažiau yra geriau.",
         "Share of the player's plays that end in a turnover. Lower is better."),
        ("3p_attempted_rate", "3PAR", "Tritaškių dalis metimuose", "3PA rate",
         "Kiek procentų visų žaidėjo metimų iš žaidimo yra tritaškiai.",
         "Share of the player's field-goal attempts taken from three."),
        ("created_points", "PTS+AST", "Sukurti taškai", "Created points",
         "Paties pelnyti taškai plius taškai, kuriuos komandos draugai pelnė po jo rezultatyvių perdavimų.",
         "Points scored himself plus points teammates scored from his assists."),
        ("fouls_received", "FD", "Išprovokuotos pražangos", "Fouls drawn",
         "Vidutiniškai per rungtynes prieš jį padarytos pražangos. Duoda taškų ir naudingumo balui.",
         "Fouls drawn per game. They add to PIR."),
        ("offensive_rebound_percentage", "OREB%", "Atk. kamuolių puolime dalis", "Offensive rebound %",
         "Kokią dalį galimų atkovoti kamuolių po komandos nepataikytų metimų jis atkovoja, kol yra aikštėje.",
         "Share of available offensive rebounds (after team misses) he grabs while on court."),
    ]),
    ("defense", ("Gynyba", "Defense"), [
        ("defensive_rating_ind", "IDRTG", "Individualus gynybos reitingas", "Individual defensive rating",
         "Kiek taškų varžovai pelno per 100 atakų, vertinant žaidėjo indėlį gynyboje. Mažiau yra geriau.",
         "Points allowed per 100 opponent possessions, based on the player's defensive contribution. Lower is better."),
        ("defensive_rebound_percentage", "DREB%", "Atk. kamuolių gynyboje dalis", "Defensive rebound %",
         "Kokią dalį galimų atkovoti kamuolių po varžovų nepataikytų metimų jis atkovoja, kol yra aikštėje.",
         "Share of available defensive rebounds (after opponent misses) he grabs while on court."),
        ("steal_percentage", "STL%", "Perimtų kamuolių dalis", "Steal %",
         "Kokią dalį varžovų atakų, kol jis aikštėje, jis užbaigia perimdamas kamuolį.",
         "Share of opponent possessions, while he is on court, that end with his steal."),
        ("block_percentage", "BLK%", "Blokų dalis", "Block %",
         "Kokią dalį varžovų metimų, kol jis aikštėje, jis užblokuoja.",
         "Share of opponent shot attempts he blocks while on court."),
        ("stops", "STP", "Sustabdytos atakos", "Stops",
         "Vidutiniškai kiek varžovų atakų per rungtynes jis užbaigia gynyboje (perimtas, blokas, atkovotas kamuolys, priverstas nepataikymas).",
         "Opponent possessions per game he ends on defense (steal, block, defensive rebound, forced miss)."),
        ("stop_percentage", "STP%", "Sustabdytų atakų dalis", "Stop %",
         "Kokią dalį varžovų atakų, kol jis aikštėje, jis sustabdo.",
         "Share of opponent possessions he stops while on court."),
    ]),
    ("overall", ("Bendra", "Overall"), [
        ("net_rating_ind", "INRTG", "Individualus balansas", "Individual net rating",
         "Individualus puolimo reitingas minus gynybos reitingas: kiek taškų per 100 atakų žaidėjas „prideda“ komandai.",
         "Individual offensive minus defensive rating: points per 100 possessions the player adds."),
        ("offensive_rating_lineup", "ORTG", "Komandos puolimo reitingas jam žaidžiant", "Team ORtg with him on court",
         "Kiek taškų komanda pelno per 100 atakų, kai jis yra aikštėje.",
         "Team points scored per 100 possessions while he is on court."),
        ("defensive_rating_lineup", "DRTG", "Komandos gynybos reitingas jam žaidžiant", "Team DRtg with him on court",
         "Kiek taškų varžovai pelno per 100 atakų, kai jis yra aikštėje. Mažiau yra geriau.",
         "Opponent points per 100 possessions while he is on court. Lower is better."),
        ("assist_turnover_ratio", "AST/TO", "Rez. perdavimai / klaidos", "Assists / turnovers",
         "Kiek rezultatyvių perdavimų tenka vienai klaidai.",
         "Assists per turnover."),
        ("possessions", "POSS", "Atakos per rungtynes", "Possessions per game",
         "Kiek atakų vidutiniškai per rungtynes sužaidžiama, kol jis aikštėje. Rodo žaidimo laiką ir tempą.",
         "Possessions played per game while he is on court. Reflects minutes and pace."),
        ("pir", "PIR", "Naudingumo balas", "Performance index rating",
         "TŠK + atk. kamuoliai + rez. perdavimai + perimti + blokai + išprovokuotos pražangos − nepataikyti metimai − klaidos − gauti blokai − pražangos.",
         "PTS + REB + AST + STL + BLK + fouls drawn − missed shots − turnovers − blocks against − fouls."),
    ]),
]

# Metrics where a smaller number is the better one.
LOWER_IS_BETTER = {"turnover_percentage", "defensive_rating_ind", "defensive_rating_lineup"}

ADV_MIN_SECONDS = 600  # league context only counts players averaging 10+ minutes


def _quartiles(values):
    vals = sorted(values)
    def at(q):
        pos = (len(vals) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    return at(0.25), at(0.75)


_context_memo = {}  # "season" -> (table, context): the same table gives the same context


def advanced_context(table):
    """League average, quartiles and the sorted values per metric, from regular-rotation players."""
    hit = _context_memo.get("season")
    if hit and hit[0] is table:
        return hit[1]
    result = _advanced_context(table)
    _context_memo["season"] = (table, result)
    return result


def _advanced_context(table):
    rows = [r for r in unique_rows(table) if (adv_value(r, "time_played") or 0) >= ADV_MIN_SECONDS]
    out, dist = {}, {}
    for _, _, items in ADV_GROUPS:
        for key, *_ in items:
            vals = sorted(v for v in (adv_value(r, key) for r in rows) if v is not None)
            if len(vals) < 8:
                continue
            p25, p75 = _quartiles(vals)
            out[key] = {"avg": round(sum(vals) / len(vals), 1), "low": round(p25, 1), "high": round(p75, 1),
                        "n": len(vals), "better": "lower" if key in LOWER_IS_BETTER else "higher"}
            dist[key] = vals
    return out, dist


def _percentile(vals, v):
    """Share of the league with a smaller value (ties count half), 0-100."""
    below = bisect.bisect_left(vals, v)
    same = bisect.bisect_right(vals, v) - below
    return round(100 * (below + same / 2) / len(vals))


def advanced_table(meta, pmap):
    """Season advanced stats of every player in `pmap` for the player lists: the columns (in the
    player card's groups and order), the league context of each, and {playerId: {key: value}}.
    Players BasketNews has no row for get None. `pmap` must come from players(), which pairs the
    rows listed under another id."""
    table = advanced_stats(meta)
    context, _ = advanced_context(table)
    columns = [{"key": key, "short": short, "title": L(title_lt, title_en), "desc": L(desc_lt, desc_en), "group": gid,
                "better": "lower" if key in LOWER_IS_BETTER else "higher"}
               for gid, _, items in ADV_GROUPS for key, short, title_lt, title_en, desc_lt, desc_en in items]
    values = {}
    for pid, p in pmap.items():
        row = table.get(str(p["bnId"])) if p.get("bnId") else None
        values[pid] = {c["key"]: adv_value(row, c["key"]) for c in columns} if row else None
    return {"groups": [{"id": gid, "title": L(lt, en)} for gid, (lt, en), _ in ADV_GROUPS],
            "columns": columns, "context": {c["key"]: context.get(c["key"]) for c in columns},
            "contextMinutes": ADV_MIN_SECONDS // 60,
            "url": f"https://basketnews.com/advanced-stats/{meta['bnLeagueId']}/{meta['seasonYear']}"}, values


def advanced_profile(meta, bn_id):
    table = advanced_stats(meta)
    row = table.get(str(bn_id)) if bn_id else None
    if not row and bn_id:
        players(meta, meta["latestRound"], meta["currentRound"], shared=True)  # pairs rows listed under another id
        row = table.get(str(bn_id))
    if not row:
        return None
    context, dist = advanced_context(table)
    groups = []
    for gid, (lt, en), items in ADV_GROUPS:
        stats = []
        for key, short, title_lt, title_en, desc_lt, desc_en in items:
            cell = row.get(key)
            if not isinstance(cell, dict) or cell.get("value") is None:
                continue
            ctx = context.get(key)
            level, pct = None, _percentile(dist[key], cell["value"]) if key in dist else None
            if ctx and ctx["better"] == "lower":
                # a small value is the good one: it is the "high" level and the long bar,
                # and the thresholds swap (high = up to the lower quartile)
                ctx = {**ctx, "high": ctx["low"], "low": ctx["high"]}
                level = "high" if cell["value"] <= ctx["high"] else "low" if cell["value"] >= ctx["low"] else "avg"
                pct = None if pct is None else 100 - pct
            elif ctx:
                level = "high" if cell["value"] >= ctx["high"] else "low" if cell["value"] <= ctx["low"] else "avg"
            stats.append({"key": key, "short": short, "title": L(title_lt, title_en), "desc": L(desc_lt, desc_en),
                          # BasketNews' own "pct" runs in different directions per metric, so the
                          # bar uses the same league sample as the high/low level instead.
                          "value": cell["value"], "rank": cell.get("rank"), "pct": pct,
                          "context": ctx, "level": level})
        groups.append({"id": gid, "title": L(lt, en), "stats": stats})
    return {"groups": groups, "ranked": len(unique_rows(table)), "games": row.get("games_played"),
            "contextMinutes": ADV_MIN_SECONDS // 60,
            "url": f"https://basketnews.com/advanced-stats/{meta['bnLeagueId']}/{meta['seasonYear']}"}


AVG_KEYS = ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "fd", "ba", "eff", "usg")


_averages_memo = {}  # competition -> (player list, averages)


def league_averages(meta):
    """Per-game league averages of regular-rotation players (the same 10+ minute sample
    as the advanced-stats context), shown in the stat tooltips."""
    pmap = players(meta, meta["latestRound"], meta["currentRound"], shared=True)
    hit = _averages_memo.get(meta["leagueId"])
    if hit and hit[0] is pmap:
        return copy.deepcopy(hit[1])
    out = _league_averages(pmap)
    _averages_memo[meta["leagueId"]] = (pmap, out)
    return copy.deepcopy(out)


def _league_averages(pmap):
    regular = [p for p in pmap.values()
               if p["season"] and p["gamesPlayed"] and p["season"]["min"] >= ADV_MIN_SECONDS / 60]
    if not regular:
        return None
    mean = lambda vals: round(sum(vals) / len(vals), 1) if vals else None  # noqa: E731
    out = {"n": len(regular), "minutes": ADV_MIN_SECONDS // 60,
           "fp": mean([p["avgPts"] for p in regular if p.get("avgPts") is not None])}
    for key in AVG_KEYS:
        out[key] = mean([p["season"][key] for p in regular if p["season"].get(key) is not None])
    made_att = {}
    for key, made, att in (("p2", "p2m", "p2a"), ("p3", "p3m", "p3a"), ("ft", "ftm", "fta")):
        # season lines are per-game averages: times games played gives the totals
        m = sum(p["season"][made] * p["gamesPlayed"] for p in regular)
        a = sum(p["season"][att] * p["gamesPlayed"] for p in regular)
        made_att[key] = (m, a)
        out[key] = round(100 * m / a, 1) if a else None
    m, a = made_att["p2"][0] + made_att["p3"][0], made_att["p2"][1] + made_att["p3"][1]
    out["fg"] = round(100 * m / a, 1) if a else None
    return out
