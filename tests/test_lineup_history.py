"""Lineup history: the API only shows the current lineup, so every fetch is saved per round."""
import copy
import json

from conftest import HLA


def meta(current=1, started=False, fid=HLA):
    return {"id": fid, "currentRound": current, "roundStarted": started}


def team(*cards, captain=None):
    return {"formation": "1-2-2",
            "players": [{"id": f"p-{c}", "card": c, "slot": "starter" if c[0] in "gfc" else "bench",
                         "captain": c == captain} for c in cards]}


def lineup_by_team(rnd, teams):
    return {tid: {"round": rnd, **t} for tid, t in teams.items()}


def store(ft, fid=HLA):
    return json.loads((ft.data_dir / "lineups" / f"{fid}.json").read_text())


def test_first_save_writes_the_round(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(), lineup_by_team(1, {"t1": team("c-1", "g-1", captain="g-1")}))
    rounds = store(ft)["rounds"]
    assert list(rounds) == ["1"]
    assert rounds["1"]["locked"] is False  # round not started: the lineup can still change
    assert rounds["1"]["savedAt"] == "2026-09-30T10:00:00"
    assert rounds["1"]["teams"]["t1"]["players"][1]["captain"] is True


def test_unchanged_lineup_is_not_rewritten(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    lineups = lineup_by_team(1, {"t1": team("c-1", "g-1")})
    ft.save_lineup_snapshot(meta(), lineups)
    path = ft.data_dir / "lineups" / f"{HLA}.json"
    before = path.read_bytes()
    clock.set("2026-09-30T10:15:00+03:00")
    ft.save_lineup_snapshot(meta(), copy.deepcopy(lineups))
    assert path.read_bytes() == before  # no new savedAt, no git change, no duplicate entry


def test_changed_open_lineup_replaces_the_old_one(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(), lineup_by_team(1, {"t1": team("c-1", "g-1")}))
    clock.set("2026-09-30T11:00:00+03:00")
    ft.save_lineup_snapshot(meta(), lineup_by_team(1, {"t1": team("c-1", "g-2")}))
    r = store(ft)["rounds"]["1"]
    assert [p["card"] for p in r["teams"]["t1"]["players"]] == ["c-1", "g-2"]
    assert r["savedAt"] == "2026-09-30T11:00:00"


def test_round_locks_when_it_starts_and_stays_locked(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(started=True), lineup_by_team(1, {"t1": team("c-1", "g-1")}))
    assert store(ft)["rounds"]["1"]["locked"] is True
    # an unlocked view of the same round never replaces the locked one
    ft.save_lineup_snapshot(meta(started=False), lineup_by_team(1, {"t1": team("c-1", "g-9")}))
    r = store(ft)["rounds"]["1"]
    assert r["locked"] is True
    assert [p["card"] for p in r["teams"]["t1"]["players"]] == ["c-1", "g-1"]


def test_past_rounds_are_locked_and_rounds_are_kept_separately(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(current=2), lineup_by_team(1, {"t1": team("c-1")}))
    ft.save_lineup_snapshot(meta(current=2), lineup_by_team(2, {"t1": team("f-1")}))
    rounds = store(ft)["rounds"]
    assert rounds["1"]["locked"] is True and rounds["2"]["locked"] is False
    assert set(rounds) == {"1", "2"}


def test_lineup_snapshot_lookup(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(started=True), lineup_by_team(1, {"t1": team("c-1", captain="c-1")}))
    snap = ft.lineup_snapshot(meta(), 1, "t1")
    assert snap["locked"] is True and snap["savedAt"] == "2026-09-30T10:00:00"
    assert snap["players"][0]["captain"] is True
    assert ft.lineup_snapshot(meta(), 1, "nobody") is None
    assert ft.lineup_snapshot(meta(), 5, "t1") is None


def test_leagues_are_stored_in_separate_files(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.save_lineup_snapshot(meta(fid="a" * 24), lineup_by_team(1, {"t1": team("c-1")}))
    ft.save_lineup_snapshot(meta(fid="b" * 24), lineup_by_team(1, {"t2": team("g-1")}))
    assert set(store(ft, "a" * 24)["rounds"]["1"]["teams"]) == {"t1"}
    assert set(store(ft, "b" * 24)["rounds"]["1"]["teams"]) == {"t2"}


def test_lineups_parses_the_api_answer_and_records_it_on_flush(ft, clock, graphql, monkeypatch):
    clock.set("2026-09-30T10:00:00+03:00")
    answer = graphql("draftLeagueFantasyTeamLineupsFromClient", HLA)[0]
    monkeypatch.setattr(ft, "gql", lambda query, variables, ttl=None: answer)
    result = ft.lineups(meta(started=True))
    assert not (ft.data_dir / "lineups").exists()  # fetching alone writes nothing
    assert ft.flush() == {"lineups": 1, "injuries": 0}
    raw = answer["draftLeagueFantasyTeamLineupsFromClient"]
    assert set(result) == {lu["fantasyTeamId"] for lu in raw}
    for lu in result.values():
        slots = [p["slot"] for p in lu["players"]]
        assert slots == sorted(slots, key=["starter", "bench", "inactive"].index)  # starters first
        assert sum(p["captain"] for p in lu["players"]) <= 1
        assert all({"id", "card", "slot", "captain"} == set(p) for p in lu["players"])
    saved = store(ft)["rounds"][str(raw[0]["fantasyRound"])]
    assert saved["locked"] is True and set(saved["teams"]) == set(result)


def test_lineups_skip_empty_slots(ft, clock, monkeypatch):
    clock.set("2026-09-30T10:00:00+03:00")
    answer = {"draftLeagueFantasyTeamLineupsFromClient": [{
        "fantasyTeamId": "t1", "fantasyRound": 1, "formation": None,
        "players": [{"cardIdentifier": "g-1", "captain": None, "player": {"id": "p1"}},
                    {"cardIdentifier": "b-2", "captain": False, "player": None}]}]}
    monkeypatch.setattr(ft, "gql", lambda query, variables, ttl=None: answer)
    result = ft.lineups(meta())
    assert result["t1"]["players"] == [{"id": "p1", "card": "g-1", "slot": "starter", "captain": False}]


def test_discard_drops_what_was_fetched(ft, clock, monkeypatch):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.observe_lineups(meta(), lineup_by_team(1, {"t1": team("c-1")}))
    ft.observe_injury_report([{"bnId": "1", "name": "A", "club": "C", "status": "out", "return": "", "comment": "Knee"}])
    assert set(ft.pending()["lineups"]) == {HLA} and len(ft.pending()["injuries"]) == 1
    ft.discard()
    assert ft.flush() == {"lineups": 0, "injuries": 0}
    assert not (ft.data_dir / "lineups").exists() and not (ft.data_dir / "injuries.json").exists()


def test_only_the_latest_lineup_fetch_of_a_league_is_kept(ft, clock):
    clock.set("2026-09-30T10:00:00+03:00")
    ft.observe_lineups(meta(), lineup_by_team(1, {"t1": team("c-1")}))
    ft.observe_lineups(meta(), lineup_by_team(1, {"t1": team("g-1")}))
    ft.flush()
    assert [p["card"] for p in store(ft)["rounds"]["1"]["teams"]["t1"]["players"]] == ["g-1"]
