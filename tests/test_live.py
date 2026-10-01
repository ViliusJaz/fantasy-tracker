"""Live rounds: the EuroLeague live feed, matching its players, live fantasy points and team totals."""
from backend import live
from backend.scoring import fantasy_points
from backend.sources import euroleague_live

HEADER = {"Live": True, "Quarter": "3", "RemainingPartialTime": "01:21", "GameTime": "28:00", "ScoreA": "72",
          "ScoreB": "70", "CodeTeamA": "HTA", "CodeTeamB": "MAD"}


def row(team, number, name, minutes="20:00", **stats):
    base = {"Team": team, "Dorsal": number, "Player": name, "Minutes": minutes, "Points": 0, "FieldGoalsMade2": 0,
            "FieldGoalsAttempted2": 0, "FieldGoalsMade3": 0, "FieldGoalsAttempted3": 0, "FreeThrowsMade": 0,
            "FreeThrowsAttempted": 0, "OffensiveRebounds": 0, "DefensiveRebounds": 0, "Assistances": 0, "Steals": 0,
            "Turnovers": 0, "BlocksFavour": 0, "BlocksAgainst": 0, "FoulsCommited": 0, "FoulsReceived": 0,
            "Valuation": 0, "Plusminus": 0}
    return {**base, **stats}


BOX = {"Live": True, "Stats": [
    {"Team": "HAPOEL", "PlayersStats": [row("HTA", "12", "WAINRIGHT, ISH", Points=10, FieldGoalsMade2=5, FieldGoalsAttempted2=7),
                                        row("HTA", "1", "GERMAN, EUGENE", minutes="DNP")]},
    {"Team": "REAL MADRID", "PlayersStats": [row("MAD", "5", "SMITH JR, NICK", Points=3, FieldGoalsMade3=1, FieldGoalsAttempted3=2)]},
]}


def test_feed_states():
    g = euroleague_live.parse(HEADER, BOX)
    assert g["live"] and not g["final"] and (g["quarter"], g["clock"]) == ("3", "01:21")
    assert g["score"] == {"HTA": 72, "MAD": 70}
    assert [r["name"] for r in g["players"]["HTA"]] == ["WAINRIGHT, ISH"]  # DNP has no line
    line = g["players"]["HTA"][0]["line"]
    assert (line["pts"], line["p2m"], line["p2a"], line["sec"], line["min"]) == (10, 5, 7, 1200, 20.0)
    final = euroleague_live.parse({**HEADER, "Live": False, "Quarter": "", "GameTime": "40:00"}, BOX)
    assert final["final"] and not final["live"]
    assert euroleague_live.parse(None, None) is None  # before tip-off the feed is empty


def test_formula():
    line = {"pts": 20, "oreb": 2, "dreb": 8, "reb": 10, "ast": 3, "stl": 1, "blk": 0, "tov": 2, "fd": 4, "ba": 1,
            "p2m": 7, "p2a": 10, "p3m": 1, "p3a": 4, "ftm": 3, "fta": 4, "pf": 2}
    # 20 + 3 + 8 + 4.5 + 1.5 - 3 + 4 - 0.5, misses 3 + 3 + 1 = -7, double-double +10, win +1.5
    assert fantasy_points(line, True) == 42.0
    assert fantasy_points(line, False) == 39.0 and fantasy_points(line, None) == 40.5
    assert fantasy_points({**line, "pf": 5}, True) == 37.0  # fouled out
    assert fantasy_points(line, True, "classic") is None


def test_rows_pair_with_players_one_to_one():
    rows = [{"number": "12", "name": "WAINRIGHT, ISH"}, {"number": "5", "name": "SMITH JR, NICK"},
            {"number": "7", "name": "JONES, TYUS"}]
    roster = [{"name": "Ishmail Wainright", "number": 24}, {"name": "Nick Smith Jr.", "number": 5},
              {"name": "Tyus Jones", "number": 7}, {"name": "Tyler Jones", "number": 9}]
    pairs = live.match_rows(rows, roster)
    by_name = {v["name"]: pairs.get(id(v), {}).get("name") for v in roster}
    assert by_name == {"Ishmail Wainright": "WAINRIGHT, ISH", "Nick Smith Jr.": "SMITH JR, NICK",
                       "Tyus Jones": "JONES, TYUS", "Tyler Jones": None}


def view(pid, name, club, number, home, opp, line=None, pts=None):
    return {"id": pid, "name": name, "number": number, "club": {"abbr": club}, "roundLine": line, "roundPts": pts,
            "roundPlayed": bool(line), "games": [{"at": "2026-10-01T16:00:00.000Z", "home": home, "opponent": opp,
                                                  "score": None, "live": True, "completed": False, "canceled": False}]}


def test_overlay_fills_only_players_basketnews_has_not_scored():
    state = {**euroleague_live.parse(HEADER, BOX), "codes": {"HTA": "HTA", "RMB": "MAD"}}
    games = {("HTA", "RMB", "2026-10-01T16:00:00.000Z"): state}
    official = {"pts": 30}
    views = {"a": view("a", "Ishmail Wainright", "HTA", 24, True, "RMB"),
             "b": view("b", "Nick Smith Jr.", "RMB", 5, False, "HTA", line=official, pts=12.0)}
    live.overlay({"pointCalcSystem": "modern"}, views, games)
    a, b = views["a"], views["b"]
    assert a["roundLive"] and a["roundPlayed"] and a["roundLine"]["pts"] == 10
    assert a["roundPts"] == fantasy_points(state["players"]["HTA"][0]["line"], True)  # HTA leads 72-70
    assert b["roundLine"] is official and b["roundPts"] == 12.0 and "roundLive" not in b  # BasketNews' own numbers
    assert a["games"][0]["score"] == [72, 70] and b["games"][0]["score"] == [70, 72]
    assert a["games"][0]["period"] == {"quarter": "3", "clock": "01:21"}


def test_live_tables(monkeypatch):
    monkeypatch.setattr(live, "team_totals", lambda meta, rnd: {"t1": 50, "t2": 61.5})
    rows = [{"team": {"id": "t1"}, "position": 1, "positionGained": 0, "pointsTotal": 300, "pointsRound": 0},
            {"team": {"id": "t2"}, "position": 2, "positionGained": 0, "pointsTotal": 280, "pointsRound": 0},
            {"team": {"id": "t3"}, "position": 3, "positionGained": 0, "pointsTotal": 200, "pointsRound": 0}]
    table = live.live_table({}, 3, rows, before=rows)
    assert [(r["team"]["id"], r["pointsTotal"], r["position"], r["positionGained"]) for r in table] == [
        ("t1", 350, 1, 0), ("t2", 341.5, 2, 0), ("t3", 200, 3, 0)]
    m = live.live_matchups({}, 3, [{"team1": {"id": "t1"}, "team2": {"id": "t2"}, "score1": 0, "score2": 0}])
    assert (m[0]["score1"], m[0]["score2"]) == (50, 61.5)


def test_an_unscored_round_falls_back_to_the_last_table(monkeypatch):
    from backend import league
    scored = [{"team": {"id": "t1"}, "position": 1, "positionGained": 0, "pointsTotal": 120, "pointsRound": 60}]
    zeros = [{"team": {"id": "t1"}, "position": 1, "positionGained": 0, "pointsTotal": 0, "pointsRound": 0}]
    monkeypatch.setattr(league, "fetch_standings_round", lambda meta, r: scored if r == 1 else zeros)
    meta = {"latestRound": 2, "firstRound": 0, "currentRound": 2, "roundStarted": True}
    assert league.standings(meta) == (1, scored)  # BasketNews lists the live round with zero totals
    monkeypatch.setattr(live, "team_totals", lambda meta_, rnd: {"t1": 30})
    (row,) = live.live_round_rows(meta, 2)
    assert (row["pointsTotal"], row["pointsRound"], row["position"]) == (150, 30, 1)
