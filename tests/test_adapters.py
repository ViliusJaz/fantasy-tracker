"""The BasketNews adapter: raw answers in, the tracker's own records (backend/models.py) out."""
from backend import models
from conftest import HLA
from test_parsing import fake_gql


def side(team=None, players=(), credits=None):
    return {"fantasyTeam": team, "players": list(players), "credits": credits}


def person(pid, first="Ann", last="Lee", traded=None):
    return {"player": {"id": pid, "firstName": first, "lastName": last}, "traded": traded}


def test_transfers_are_normalised(ft, monkeypatch):
    raw = [
        {"id": "t1", "type": "free_agent", "updatedAt": "2026-09-29T15:00:00Z",
         "offer": side({"id": "A", "title": "Team A"}, [person("p1")], 120),
         "request": side(None, [person("p2", last=None)], None)},
        {"id": "t2", "type": "team", "updatedAt": None,
         "offer": side({"id": "A", "title": "Team A"}, [person("p3", traded=True), person("p4", traded=False)], 0),
         "request": side({"id": "B", "title": "Team B"}, [person("p5", traded=True), {"player": None}], 30)},
        {"id": "t3", "type": "free_agent", "offer": None, "request": None},
    ]
    fake_gql(monkeypatch, ft, {"draftTransfersFromClient": {"draftTransfersFromClient": raw}})
    signing, trade, empty = ft.fetch_transfers(HLA, 1, 60)
    assert signing == {"id": "t1", "kind": "free_agent", "at": "2026-09-29T15:00:00Z",
                       "offer": {"team": {"id": "A", "title": "Team A"}, "players": [{"id": "p1", "name": "Ann Lee"}],
                                 "listed": ["p1"], "credits": 120},
                       "request": {"team": None, "players": [{"id": "p2", "name": "Ann"}], "listed": ["p2"],
                                   "credits": 0}}
    assert trade["kind"] == "trade"
    assert [p["id"] for p in trade["offer"]["players"]] == ["p3"]      # only the flagged players moved
    assert trade["offer"]["listed"] == ["p3", "p4"]
    assert [p["id"] for p in trade["request"]["players"]] == ["p5"]
    assert empty["offer"] == {"team": None, "players": [], "listed": [], "credits": 0}
    for t in (signing, trade, empty):
        assert models.required(models.Transfer) <= set(t)
        assert models.required(models.TransferSide) <= set(t["offer"])


def test_transfers_with_no_answer(ft, monkeypatch):
    fake_gql(monkeypatch, ft, {"draftTransfersFromClient": {"draftTransfersFromClient": None}})
    assert ft.fetch_transfers(HLA, 1, 60) == []


def test_draft_picks_in_pick_order(ft, monkeypatch):
    raw = {"fantasyLeagueRecordFromClient": {"draft": {"picks": [
        {"id": "03", "fantasyTeamId": "B", "player": {"id": "p3"}},
        {"id": "01", "fantasyTeamId": "A", "player": {"id": "p1"}},
        {"id": "02", "fantasyTeamId": "B", "player": None}]}}}
    fake_gql(monkeypatch, ft, {"draft { picks": raw})
    assert ft.fetch_draft_picks(HLA) == [{"id": "01", "teamId": "A", "playerId": "p1"},
                                         {"id": "02", "teamId": "B", "playerId": None},
                                         {"id": "03", "teamId": "B", "playerId": "p3"}]


def test_draft_picks_of_a_league_without_draft(ft, monkeypatch):
    fake_gql(monkeypatch, ft, {"draft { picks": {"fantasyLeagueRecordFromClient": None}})
    assert ft.fetch_draft_picks(HLA) == []


def test_bids(ft, monkeypatch):
    fake_gql(monkeypatch, ft, {"draftFreeAgentBidsSummaryFromClient": {"draftFreeAgentBidsSummaryFromClient": [
        {"player": {"id": "p1"}, "highestBid": 50, "totalBids": 3}, {"player": None, "highestBid": 1}]}})
    assert ft.fetch_bids(HLA, 2) == [{"playerId": "p1", "highestBid": 50, "totalBids": 3}]


def test_competitions(ft, monkeypatch):
    from test_rounds import competition
    comp = competition()
    comp["en"] = None  # no English translation
    fake_gql(monkeypatch, ft, {"allLeagueRecordsFromClient": {"allLeagueRecordsFromClient": [comp]}})
    c = ft.fetch_competitions()["comp"]
    assert c["name"] == {"lt": "Eurolyga", "en": "Eurolyga"}
    assert c["injuryReportUrl"] == "https://lt/traumos.html"  # falls back to the Lithuanian report
    assert (c["currentRound"], c["firstRound"], c["totalRounds"]) == (1, 0, 38)


def test_recorded_lineups(ft, graphql, monkeypatch):
    answer = graphql("draftLeagueFantasyTeamLineupsFromClient", HLA)[0]
    monkeypatch.setattr(ft, "gql", lambda *a, **k: answer)
    lineups = ft.fetch_lineups(HLA)
    assert len(lineups) == 8
    for lu in lineups:
        assert set(lu) == {"teamId", "round", "formation", "players"}
        assert all(set(p) == {"id", "card", "captain"} for p in lu["players"])


def test_recorded_players_have_the_model_keys(ft, graphql, monkeypatch):
    answer = graphql("playersSearchRecordsFromClient")[0]
    monkeypatch.setattr(ft, "gql", lambda *a, **k: answer)
    meta = {"leagueId": "x", "pointCalcSystem": "modern"}
    players = ft.fetch_players(meta, 1, 1, 60)
    assert players and all(set(p) == models.required(models.Player) for p in players.values())


def test_player_rounds(ft, monkeypatch):
    rec = {"id": "p1", "firstName": "A", "lastName": "B", "team": None, "roundStats": None, "season": None,
           "r0_pts": 12.5, "r0_st": {"s_gp": 1, "s_pts": 10}, "r0_tm": {"team": {"id": "C", "abbreviation": "CCC",
                                                                                 "games": []}},
           "r1_pts": None, "r1_st": None, "r1_tm": None}
    fake_gql(monkeypatch, ft, {"playerRecordFromClient": {"playerRecordFromClient": rec}})
    meta = {"leagueId": "x", "pointCalcSystem": "modern", "latestRound": 1, "currentRound": 1}
    info, rounds = ft.fetch_player_rounds(meta, "p1", [0, 1])
    assert info["name"] == "A B"
    assert rounds[0]["club"] == "CCC" and rounds[0]["fp"] == 12.5 and rounds[0]["line"]["pts"] == 10
    assert rounds[1] == {"round": 1, "club": None, "games": [], "fp": None, "line": None}
    fake_gql(monkeypatch, ft, {"playerRecordFromClient": {"playerRecordFromClient": None}})
    assert ft.fetch_player_rounds(meta, "nobody", [0]) is None
