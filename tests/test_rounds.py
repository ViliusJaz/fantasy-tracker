"""Round bookkeeping: which round is live, finished or upcoming, and the fallbacks around them."""
import pytest

from conftest import HLA
from test_parsing import fake_gql


def competition(current=1, started=True, first=0, total=38):
    return {"id": "comp", "currentFantasyRound": current, "roundStarted": started, "scoreRoundsAvailable": 2,
            "totalRounds": total, "startingFantasyRound": first, "basketnewsApiLeagueId": "25", "seasonYear": 2026,
            "injury_report_url": "", "activeDraftTradeLock": {"locked": True, "nextChange": None},
            "translation": {"name": "Eurolyga", "shortName": "Eurolyga", "injuryReportUrl": "https://lt/traumos.html"},
            "en": {"name": "Euroleague", "injuryReportUrl": "https://com/injury-report.html"}}


def league_record(fmt="head_to_head"):
    return {"id": HLA, "title": " HLA ", "type": "draft", "format": fmt, "leagueId": "comp", "pointCalcSystem": None,
            "fantasyTeamsCount": 8, "draftDate": None, "draftOrder": None, "pickOrder": None,
            "draftTradingMethod": "credits", "draftStartingCredits": 1000,
            "publicUser": {"firstName": "Vilius", "lastNameInitial": "J"}}


def meta_for(ft, monkeypatch, **comp):
    fake_gql(monkeypatch, ft, {
        "fantasyLeagueRecordFromClient": {"fantasyLeagueRecordFromClient": league_record()},
        "allLeagueRecordsFromClient": {"allLeagueRecordsFromClient": [competition(**comp)]},
    })
    return ft.league_meta(HLA)


def test_league_meta_live_round(ft, monkeypatch):
    meta = meta_for(ft, monkeypatch, current=3, started=True)
    assert meta["currentRound"] == 3 and meta["latestRound"] == 3 and meta["roundStarted"] is True
    assert meta["title"] == "HLA" and meta["pointCalcSystem"] == "modern" and meta["commissioner"] == "Vilius J."
    assert meta["injuryReportUrl"] == "https://com/injury-report.html"  # the English report is the one parsed
    assert meta["draft"]["startingCredits"] == 1000


def test_league_meta_between_rounds(ft, monkeypatch):
    meta = meta_for(ft, monkeypatch, current=3, started=False)
    assert meta["latestRound"] == 2  # results shown are the last finished round
    meta = meta_for(ft, monkeypatch, current=0, started=False)
    assert meta["latestRound"] == 0  # never before the first round


def test_league_meta_language(ft, monkeypatch):
    ft.LANG.set("en")
    assert meta_for(ft, monkeypatch)["competition"] == "Euroleague"
    ft.LANG.set("lt")
    assert meta_for(ft, monkeypatch)["competition"] == "Eurolyga"


def test_unknown_league(ft, monkeypatch):
    fake_gql(monkeypatch, ft, {"fantasyLeagueRecordFromClient": {"fantasyLeagueRecordFromClient": None}})
    with pytest.raises(ft.NotFound):
        ft.league_meta(HLA)


@pytest.mark.parametrize("current,started,rnd,state", [
    (3, True, 2, "finished"), (3, True, 3, "live"), (3, True, 4, "upcoming"),
    (3, False, 3, "upcoming"), (3, False, 2, "finished"),
])
def test_round_state(ft, current, started, rnd, state):
    meta = {"currentRound": current, "roundStarted": started}
    assert ft.round_state(meta, rnd) == state
    assert ft.is_live(meta, rnd) is (state == "live")


def test_round_ttl(ft):
    meta = {"currentRound": 5}
    assert ft.round_ttl(meta, 5) == ft.LIVE_TTL and ft.round_ttl(meta, 4) == ft.LIVE_TTL  # corrections still possible
    assert ft.round_ttl(meta, 3) == ft.SETTLED_TTL


def test_players_left_counts_starters_still_to_play(ft):
    def p(slot, *games):
        return {"slot": slot, "games": [{"completed": c, "canceled": x} for c, x in games]}
    lineup = [p("starter", (True, False)), p("starter", (False, False)), p("starter", (False, True)),
              p("starter", (True, False), (False, False)), p("bench", (False, False))]
    assert ft.players_left(lineup) == 2


def test_standings_fall_back_to_the_last_scored_round(ft, monkeypatch):
    meta = {"id": HLA, "format": "head_to_head", "currentRound": 3, "latestRound": 3, "firstRound": 0}
    team = {"id": "t1", "title": "T", "publicUser": None}
    row = {"fantasyTeam": team, "wins": 2, "losses": 0, "ties": 0, "position": 1,
           "fantasyTeamScore": {"pointsGained": 5, "pointsTotal": 9}}
    fake_gql(monkeypatch, ft, {"allHeadToHeadScoreRecordsFromClient": lambda v: {
        "allHeadToHeadScoreRecordsFromClient": {"records": [row] if v["round"] <= 1 else []}}})
    shown, rows = ft.standings(meta)
    assert shown == 1 and rows[0]["pointsTotal"] == 9


def test_standings_before_any_round(ft, monkeypatch):
    meta = {"id": HLA, "format": "head_to_head", "currentRound": 0, "latestRound": 0, "firstRound": 0}
    fake_gql(monkeypatch, ft, {
        "allHeadToHeadScoreRecordsFromClient": {"allHeadToHeadScoreRecordsFromClient": {"records": []}},
        "fantasyLeagueTeamsFromClient": {"fantasyLeagueTeamsFromClient": {"records": [
            {"id": "a", "title": "A", "publicUser": None}, {"id": "b", "title": "B", "publicUser": None}]}},
    })
    shown, rows = ft.standings(meta)
    assert shown is None
    assert [(r["team"]["id"], r["position"], r["pointsTotal"]) for r in rows] == [("a", 1, 0), ("b", 2, 0)]
