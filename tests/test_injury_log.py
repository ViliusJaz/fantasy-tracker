"""Injury history: report snapshots become per-player episodes with dated status changes."""
import json

import pytest


def entry(bn_id, status="out", comment="Knee injury", ret="2 weeks", name=None):
    return {"bnId": bn_id, "name": name or f"Player {bn_id}", "club": "Club", "pos": "G", "status": status,
            "siteLabel": status.title(), "return": ret, "comment": comment}


def log(ft):
    return json.loads((ft.data_dir / "injuries.json").read_text())["players"]


def at(clock, iso):
    clock.set(iso)


def test_new_injury_opens_an_episode(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1")])
    ep, = log(ft)["1"]["episodes"]
    assert ep["start"] == "2026-09-28" and ep["end"] is None and ep["lastSeen"] == "2026-09-28"
    assert ep["updates"] == [{"date": "2026-09-28", "at": "2026-09-28T09:00+03:00", "status": "out",
                              "return": "2 weeks", "comment": "Knee injury"}]


def test_same_report_adds_nothing_and_does_not_rewrite_the_file(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1"), entry("2", "questionable")])
    path = ft.data_dir / "injuries.json"
    before = path.read_bytes()
    at(clock, "2026-09-28T09:15:00+03:00")
    ft.update_injury_log([entry("1"), entry("2", "questionable")])
    assert path.read_bytes() == before


def test_status_change_is_appended(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1")])
    at(clock, "2026-09-29T18:30:00+03:00")
    ft.update_injury_log([entry("1", "questionable", ret="Game-time")])
    ep, = log(ft)["1"]["episodes"]
    assert [u["status"] for u in ep["updates"]] == ["out", "questionable"]
    assert ep["updates"][1]["at"] == "2026-09-29T18:30+03:00"
    assert ep["lastSeen"] == "2026-09-29" and ep["end"] is None


def test_ready_closes_the_episode(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1")])
    at(clock, "2026-10-02T09:00:00+03:00")
    ft.update_injury_log([entry("1", "ready", comment="Cleared to play")])
    ep, = log(ft)["1"]["episodes"]
    assert ep["end"] == "2026-10-02"
    assert ep["updates"][-1]["status"] == "ready"
    # a new injury later starts a second episode
    at(clock, "2026-10-20T09:00:00+03:00")
    ft.update_injury_log([entry("1", comment="Ankle")])
    assert [e["end"] for e in log(ft)["1"]["episodes"]] == ["2026-10-02", None]


def test_player_leaving_the_report_closes_the_episode(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1"), entry("2")])
    at(clock, "2026-09-29T09:00:00+03:00")
    ft.update_injury_log([entry("2")])
    ep, = log(ft)["1"]["episodes"]
    assert ep["end"] == "2026-09-29"
    assert ep["updates"][-1]["comment"] == ft.REMOVED_NOTE and ep["updates"][-1]["at"] == "2026-09-29T09:00+03:00"
    assert log(ft)["2"]["episodes"][0]["end"] is None


def test_long_gap_ends_the_episode_on_the_last_day_seen(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1"), entry("2")])
    at(clock, "2026-10-03T09:00:00+03:00")  # nothing recorded for 5 days (e.g. the Mac was off)
    ft.update_injury_log([entry("2")])
    ep = log(ft)["1"]["episodes"][0]
    assert ep["end"] == "2026-09-28" and ep["updates"][-1]["at"] is None


def test_first_sighting_already_recovered(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1", "ready", comment="Recovered from hamstring injury"),
                          entry("2", "ready", comment="Coach's decision")])
    players = log(ft)
    assert players["1"]["episodes"] == [{"start": "2026-09-28", "end": "2026-09-28", "lastSeen": "2026-09-28",
                                         "updates": [{"date": "2026-09-28", "at": "2026-09-28T09:00+03:00",
                                                      "status": "ready", "return": "2 weeks",
                                                      "comment": "Recovered from hamstring injury"}]}]
    assert players["2"]["episodes"] == []  # not an injury: nothing to keep


def test_entries_without_player_id_are_ignored(ft, clock):
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry(None)])
    assert log(ft) == {}


def test_empty_report_closes_every_open_episode(ft, clock):
    """What the log does with an empty report. The pipeline must therefore never pass it a
    report it could not read (see test_export: blank injury page)."""
    at(clock, "2026-09-28T09:00:00+03:00")
    ft.update_injury_log([entry("1"), entry("2")])
    at(clock, "2026-09-28T09:15:00+03:00")
    ft.update_injury_log([])
    assert all(p["episodes"][0]["end"] == "2026-09-28" for p in log(ft).values())


@pytest.mark.parametrize("comment,injury", [
    ("Knee injury", True),
    ("Ankle sprain", True),
    ("Coach's decision", False),
    ("Not included in 12-man roster", False),
    ("New signing, travels with team", False),
    ("DNP in Round 1 (coach's decision). Played in domestic league", False),
    ("DNP in Round 2 (ankle)", True),
    ("", False),
])
def test_is_injury(ft, comment, injury):
    assert ft.is_injury(comment) is injury


def test_dnp_reason(ft):
    assert ft.dnp_reason("DNP in Round 1 (knee injury)") == (1, "knee injury")
    assert ft.dnp_reason("DNP in Round 12") == (12, "")
    assert ft.dnp_reason("Knee.") == (None, "Knee")
    assert ft.dnp_reason(None) == (None, "")


def test_injury_view(ft):
    assert ft.injury_view(entry("1", "ready")) is None
    assert ft.injury_view(None) is None
    ft.LANG.set("en")
    view = ft.injury_view(entry("1", "out", comment="Knee injury", ret="2 weeks"))
    assert view == {"status": "out", "label": "Out", "return": "2 weeks", "comment": "Knee injury"}
    assert ft.injury_view(None, health="doubtful") == {"status": "doubtful", "label": "Doubtful", "return": "", "comment": ""}
    ft.LANG.set("lt")
    view = ft.injury_view(entry("1", "out", comment="Knee injury", ret="2 weeks"))
    assert view["label"] == "Nežaidžia" and view["comment"] != "Knee injury"  # translated
