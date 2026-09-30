"""Reading BasketNews answers: missing fields, nulls, player / team matching, the injury page."""
import json

import pytest

from conftest import HLA, CLASSIC


def fake_gql(monkeypatch, ft, answers):
    """Route gql() by the operation name in the query to canned answers."""
    def gql(query, variables, ttl=None, keep=0):
        for op, data in answers.items():
            if op in query:
                return data(variables) if callable(data) else data
        raise AssertionError(f"unexpected query: {query[:80]}")
    monkeypatch.setattr(ft, "gql", gql)


# ---------------------------------------------------------------- box scores

def test_stat_line_needs_a_game_played(ft):
    assert ft.stat_line(None) is None
    assert ft.stat_line({}) is None
    assert ft.stat_line({"s_gp": 0, "s_pts": 10}) is None


def test_stat_line_nulls_count_as_zero(ft):
    line = ft.stat_line({"s_gp": 1, "s_time": 1530, "s_pts": None, "s_orb": 2, "s_drb": None, "s_rbs": 3})
    assert line["min"] == 25.5 and line["sec"] == 1530
    assert line["pts"] == 0
    assert line["reb"] == 2 and line["oreb"] == 2 and line["dreb"] == 0
    assert line["ba"] == 3  # s_rbs is blocks against, not rebounds


def test_stat_line_keys(ft):
    line = ft.stat_line({"s_gp": 1})
    assert set(line) == {"min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "eff", "p2m",
                         "p2a", "p3m", "p3a", "ftm", "fta", "sec", "ba", "fd", "pm"}


def game(home_id="A", completed=False, live=False, p1=80, p2=70, logo="x.png"):
    return {"originalGameAt": "2026-10-01T18:00:00+00:00", "live": live, "completed": completed,
            "canceled": False, "delayed": False,
            "team1": {"points": p1, "team": {"id": home_id, "abbreviation": "HOM", "logo": None}},
            "team2": {"points": p2, "team": {"id": "B", "abbreviation": "AWY", "logo": logo}}}


def test_game_view_home_and_away(ft):
    home = ft.game_view(game(completed=True), "A")
    assert home["home"] is True and home["opponent"] == "AWY" and home["score"] == [80, 70]
    assert home["opponentLogo"] == ft.LOGO_URL + "x.png"
    away = ft.game_view(game(completed=True), "B")
    assert away["home"] is False and away["opponent"] == "HOM" and away["score"] == [70, 80]
    assert away["opponentLogo"] is None


def test_game_view_live_and_upcoming(ft):
    assert ft.game_view(game(), "A")["score"] is None                       # not started: no score
    live = ft.game_view(game(live=True), "A")
    assert live["live"] is True and live["score"] == [80, 70]
    done = ft.game_view(game(live=True, completed=True), "A")               # upstream leaves some finished games "live"
    assert done["live"] is False and done["completed"] is True


def test_player_view_on_recorded_players(ft, graphql):
    records = graphql("playersSearchRecordsFromClient")[0]["playersSearchRecordsFromClient"]["records"]
    assert len(records) > 250
    views = [ft.player_view(r) for r in records]
    keys = {"id", "bnId", "name", "photo", "health", "position", "positions", "number", "club", "games",
            "roundPts", "roundPlayed", "roundLine", "avgPts", "gamesPlayed", "season"}
    assert all(set(v) == keys for v in views)
    assert all(v["id"] and v["name"] for v in views)
    with_bn = sum(1 for v in views if v["bnId"])
    assert with_bn > 0.9 * len(views)
    assert sum(1 for v in views if v["club"]) > 0.9 * len(views)


def test_player_view_with_missing_parts(ft):
    v = ft.player_view({"id": "p1", "firstName": "Solo", "lastName": "", "team": None, "roundStats": None,
                        "season": None})
    assert v["name"] == "Solo" and v["club"] is None and v["games"] == [] and v["position"] is None
    assert v["season"] is None and v["gamesPlayed"] == 0 and v["roundPlayed"] is False and v["roundLine"] is None


def test_mark_round_days(ft):
    views = {"a": {"games": [{"at": "2026-10-01T18:00:00Z"}]}, "b": {"games": [{"at": "2026-10-02T18:00:00Z"}]},
             "c": {"games": [{"at": None}]}}
    ft.mark_round_days(views)
    assert views["a"]["games"][0]["day"] == 1 and views["b"]["games"][0]["day"] == 2
    assert "day" not in views["c"]["games"][0]
    one = {"a": {"games": [{"at": "2026-10-01T18:00:00Z"}]}}
    ft.mark_round_days(one)
    assert "day" not in one["a"]["games"][0]  # single-day round: no tag


# ---------------------------------------------------------------- standings and schedule

def test_h2h_standings_with_nulls(ft, monkeypatch):
    team = {"id": "t1", "title": "Team", "publicUser": {"firstName": " Ona ", "lastNameInitial": "K"}}
    fake_gql(monkeypatch, ft, {"allHeadToHeadScoreRecordsFromClient": {"allHeadToHeadScoreRecordsFromClient": {"records": [
        {"fantasyTeam": team, "wins": 1, "losses": 0, "ties": 0, "position": 1, "positionGained": None,
         "fantasyTeamScore": None}]}}})
    row, = ft.fetch_standings_round({"id": HLA, "format": "head_to_head", "currentRound": 1}, 0)
    assert row == {"team": {"id": "t1", "title": "Team", "owner": "Ona K."}, "position": 1, "positionGained": 0,
                   "wins": 1, "losses": 0, "ties": 0, "pointsTotal": 0, "pointsRound": 0}


def test_classic_standings_with_nulls(ft, monkeypatch):
    team = {"id": "t1", "title": "Team", "publicUser": None}
    fake_gql(monkeypatch, ft, {"allFantasyTeamScoreRecordsFromClient": {"allFantasyTeamScoreRecordsFromClient": {"records": [
        {"fantasyTeam": team, "position": 2, "pointsTotal": None, "pointsGained": 12.5}]}}})
    row, = ft.fetch_standings_round({"id": CLASSIC, "leagueId": "x", "format": "classic", "currentRound": 1}, 0)
    assert row["team"]["owner"] == "" and row["pointsTotal"] == 0 and row["pointsRound"] == 12.5
    assert row["roundPosition"] is None


def test_recorded_standings_are_consistent(ft, graphql):
    """Real H2H table: wins + losses + ties equal the rounds played, and totals add up."""
    for data in graphql("allHeadToHeadScoreRecordsFromClient"):
        recs = data["allHeadToHeadScoreRecordsFromClient"]["records"]
        played = {r["wins"] + r["losses"] + r["ties"] for r in recs}
        assert len(played) == 1
        assert sorted(r["position"] for r in recs) == list(range(1, len(recs) + 1))
        assert all(r["fantasyTeamScore"]["pointsTotal"] >= r["fantasyTeamScore"]["pointsGained"] for r in recs)


def test_schedule_with_missing_team(ft, monkeypatch):
    team = {"id": "t1", "title": "Team", "publicUser": None}
    fake_gql(monkeypatch, ft, {"allHeadToHeadScheduleRecordsFromClient": {"allHeadToHeadScheduleRecordsFromClient": {"records": [
        {"id": "m1", "fantasyTeam1": team, "fantasyTeam2": None, "fantasyTeam1ScorePoints": None,
         "fantasyTeam2ScorePoints": 3}]}}})
    m, = ft.schedule({"id": HLA, "currentRound": 1}, 0)
    assert m == {"id": "m1", "team1": {"id": "t1", "title": "Team", "owner": ""}, "team2": None, "score1": 0, "score2": 3}


# ---------------------------------------------------------------- injury report page

REPORT = """
<html><body>
<table><tr><td>not the report</td></tr></table>
<div id="injury-reports-table"><table>
  <tr class="team-row"><td>Anadolu Efes Istanbul</td></tr>
  <tr><td>G</td><td><a href="/players/123-some-one.html">Some One</a></td>
      <td><span class="player-status player-status__id-6">Out</span></td><td>Indefinitely</td><td> Knee   injury </td></tr>
  <tr><td>F</td><td>No Link</td><td><span class="player-status__id-3">Questionable</span></td><td></td><td>Illness</td></tr>
  <tr><td>C</td><td><a href="/players/456-x.html">X</a></td><td><span class="player-status__id-99">??</span></td><td></td><td></td></tr>
  <tr><td>too short</td></tr>
  <tr class="team-row"><td>Real Madrid</td></tr>
  <tr><td>C</td><td><a href="/players/789-big-man.html">Big Man</a></td><td><span class="player-status__id-1"></span></td><td></td><td>Cleared</td></tr>
</table></div>
<div><table><tr><td>G</td><td><a href="/players/999-outside.html">Outside</a></td><td></td><td></td><td></td></tr></table></div>
</body></html>"""


def test_parse_injury_report_synthetic(ft):
    entries = ft.parse_injury_report(REPORT)
    assert [e["bnId"] for e in entries] == ["123", None, "456", "789"]
    first = entries[0]
    assert first == {"bnId": "123", "name": "Some One", "club": "Anadolu Efes Istanbul", "pos": "G", "status": "out",
                     "siteLabel": "Out", "return": "Indefinitely", "comment": "Knee injury"}
    assert entries[2]["status"] == "other"
    assert entries[3]["club"] == "Real Madrid" and entries[3]["siteLabel"] == "Ready"


def test_parse_recorded_injury_report(ft, recording):
    page = next(e for e in recording["responses"].values() if "injury-report" in e["url"])
    entries = ft.parse_injury_report(page["text"])
    assert len(entries) > 30
    assert all(e["club"] for e in entries)
    assert sum(1 for e in entries if e["bnId"]) > 0.9 * len(entries)
    assert {e["status"] for e in entries} <= set(ft.STATUS_BY_ID.values()) | {"other"}


def test_parse_injury_report_without_the_table(ft):
    assert ft.parse_injury_report("<html><body>Just a moment...</body></html>") == []


# ---------------------------------------------------------------- advanced stats pairing

def adv_row(pid, pts, reb, ast, secs, games=None):
    return {"player_id": pid, "points": {"value": pts}, "rebounds": {"value": reb}, "assists": {"value": ast},
            "time_played": {"value": secs}, "games_played": games}


def test_link_advanced_rows_pairs_a_player_listed_under_another_id(ft):
    table = {"900": adv_row("900", 30, 10, 4, 3600), "1": adv_row("1", 5, 5, 5, 600)}
    views = {"p": {"bnId": "555", "gamesPlayed": 2, "season": {"pts": 15, "reb": 5, "ast": 2, "min": 30}},
             "q": {"bnId": "1", "gamesPlayed": 1, "season": {"pts": 5, "reb": 5, "ast": 5, "min": 10}}}
    ft.link_advanced_rows(table, views, "season")
    assert table["555"] is table["900"]
    assert len(ft.unique_rows(table)) == 2


def test_link_advanced_rows_leaves_ambiguous_matches_alone(ft):
    table = {"900": adv_row("900", 30, 10, 4, 3600), "901": adv_row("901", 30, 10, 4, 3600)}
    views = {"p": {"bnId": "555", "gamesPlayed": 2, "season": {"pts": 15, "reb": 5, "ast": 2, "min": 30}}}
    ft.link_advanced_rows(table, views, "season")
    assert "555" not in table


def test_advanced_stats_future_round_is_empty(ft, monkeypatch):
    monkeypatch.setattr(ft, "fetch", lambda *a, **k: pytest.fail("no request expected"))
    meta = {"bnLeagueId": "25", "seasonYear": 2026, "latestRound": 1, "currentRound": 1}
    assert ft.advanced_stats(meta, 5) == {}
    assert ft.advanced_stats({**meta, "bnLeagueId": None}) == {}


def test_advanced_stats_round_not_published_yet(ft, monkeypatch):
    body = {"data": {"extra": {"max_sequence": 1}, "stats": [{"player_id": 1}]}}
    monkeypatch.setattr(ft, "fetch", lambda *a, **k: body)
    monkeypatch.setattr(ft, "_adv_cache", {})
    meta = {"bnLeagueId": "25", "seasonYear": 2026, "latestRound": 1, "currentRound": 1}
    assert ft.advanced_stats(meta, 1) == {}          # BasketNews has only game 1: round 2 is not there
    assert ft.advanced_stats(meta, 0) == {"1": {"player_id": 1}}


def test_advanced_stats_network_failure_keeps_old_data(ft, monkeypatch):
    def boom(*a, **k):
        raise ft.FetchError("timed out")
    monkeypatch.setattr(ft, "fetch", boom)
    monkeypatch.setattr(ft, "_adv_cache", {})
    meta = {"bnLeagueId": "25", "seasonYear": 2026, "latestRound": 1, "currentRound": 1}
    assert ft.advanced_stats(meta) == {}


# ---------------------------------------------------------------- club matching

def test_club_team_ids_tells_namesakes_apart(ft):
    clubs = {"TEL": {"nameEn": "Maccabi Playtika Tel Aviv", "name": "Maccabi"},
             "HTA": {"nameEn": "Hapoel IBI Tel Aviv", "name": "Hapoel"},
             "RMB": {"nameEn": "Real Madrid", "name": "Real"}}
    teams = {1: {"name": "Maccabi Tel Aviv", "short": "Maccabi"}, 2: {"name": "Hapoel Tel Aviv", "short": "Hapoel"},
             3: {"name": "Real Madrid", "short": "Real Madrid"}}
    assert ft.club_team_ids(clubs, teams) == {"TEL": 1, "HTA": 2, "RMB": 3}


def test_ascii_slug(ft):
    assert ft.ascii_slug("Šarūnas Jasikevičius") == "sarunas-jasikevicius"
    assert ft.ascii_slug("  T.J. Warren ") == "t-j-warren"
