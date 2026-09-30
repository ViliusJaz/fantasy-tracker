"""Every player of the competition as the tracker shows them, with usage % attached."""
import re

from backend.config import LOCALE
from backend.rounds import round_ttl
from backend.sources.advanced import adv_value, advanced_stats, link_advanced_rows
from backend.sources.basketnews import PLAYER_FIELDS, Q_PLAYERS, gql, player_view


def players(meta, stats_round, games_round):
    """Every player of the competition: {playerId: view}. Points are for `stats_round`,
    games are those of `games_round`."""
    data = gql(Q_PLAYERS, {
        "leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
        "gamesRound": games_round, "pcs": meta["pointCalcSystem"],
    }, round_ttl(meta, min(stats_round, games_round)))
    views = {p["id"]: player_view(p) for p in data["playersSearchRecordsFromClient"]["records"]}
    attach_usage(meta, views, stats_round)
    mark_round_days(views)
    return views


def mark_round_days(views):
    """Tag each game with the day of the round it is on (1, 2, ...) when a round spans several days."""
    dates = sorted({g["at"][:10] for v in views.values() for g in v["games"] if g.get("at")})
    if len(dates) < 2:
        return
    day = {d: i + 1 for i, d in enumerate(dates)}
    for v in views.values():
        for g in v["games"]:
            if g.get("at"):
                g["day"] = day[g["at"][:10]]


def attach_usage(meta, views, stats_round):
    """Usage %: BasketNews' own numbers, or a box-score estimate for a round they have not published."""
    season_adv = advanced_stats(meta)
    round_adv = advanced_stats(meta, stats_round) if stats_round is not None else {}
    link_advanced_rows(season_adv, views, "season")
    link_advanced_rows(round_adv, views, "roundLine", games=lambda v: 1)
    teams = {}
    for v in views.values():
        line, club = v["roundLine"], (v["club"] or {}).get("abbr")
        if line and club:
            t = teams.setdefault(club, [0.0, 0.0, 0.0, 0.0])
            t[0] += line["sec"] / 60
            t[1] += line["p2a"] + line["p3a"]
            t[2] += line["fta"]
            t[3] += line["tov"]
    for v in views.values():
        if v["season"]:
            v["season"]["usg"] = adv_value(season_adv.get(v["bnId"]), "usage_percentage")
        line = v["roundLine"]
        if not line:
            continue
        usg = adv_value(round_adv.get(v["bnId"]), "usage_percentage")
        team = teams.get((v["club"] or {}).get("abbr"))
        mins = line["sec"] / 60
        if usg is None and team and mins > 0:
            team_poss = team[1] + 0.44 * team[2] + team[3]
            if team_poss:
                usg = round(100 * (line["p2a"] + line["p3a"] + 0.44 * line["fta"] + line["tov"]) * (team[0] / 5)
                            / (mins * team_poss), 1)
        line["usg"] = usg


def players_by_ids(meta, ids, stats_round, games_round):
    """Like players(), plus a direct lookup for rostered players the search list omits."""
    found = players(meta, stats_round, games_round)
    missing = [i for i in ids if i not in found]
    if missing:
        fields = "\n".join(f'p{n}: playerRecordFromClient(id: "{pid}") {{ {PLAYER_FIELDS} }}'
                           for n, pid in enumerate(missing) if re.fullmatch(r"[0-9a-f]{24}", pid))
        query = ("query($leagueId: String!, $locale: String!, $statsRound: Int, $gamesRound: Int, $pcs: String) {"
                 + fields + "}")
        data = gql(query, {"leagueId": meta["leagueId"], "locale": LOCALE, "statsRound": stats_round,
                           "gamesRound": games_round, "pcs": meta["pointCalcSystem"]})
        for rec in data.values():
            if rec:
                found[rec["id"]] = player_view(rec)
    if missing:
        mark_round_days(found)  # the directly fetched players need their round day too
    return found


def player_brief(p, pid=None, name=None):
    if not p:
        return {"id": pid, "name": name or "?", "photo": None, "position": None, "club": None, "avgPts": None}
    return {k: p.get(k) for k in ("id", "name", "photo", "position", "club", "avgPts", "gamesPlayed")}
