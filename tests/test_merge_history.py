"""The git merge driver for data/: two devices' history combines, nothing is dropped."""
import json
import subprocess
import sys

from conftest import TOOLS

import merge_history as m


def snap(saved, locked, cards):
    return {"savedAt": saved, "locked": locked, "teams": {"t1": {"formation": "1-2-2", "players": cards}}}


def test_lineups_keep_every_round_and_prefer_locked_then_later():
    ours = {"rounds": {"1": snap("2026-09-30T10:00:00", False, ["a"]), "2": snap("2026-10-07T10:00:00", False, ["b"])}}
    theirs = {"rounds": {"1": snap("2026-09-30T09:00:00", True, ["c"]), "2": snap("2026-10-06T10:00:00", False, ["d"]),
                         "3": snap("2026-10-14T10:00:00", False, ["e"])}}
    out = m.merge_lineups(ours, theirs)
    assert out["rounds"]["1"]["teams"]["t1"]["players"] == ["c"]   # locked beats a later unlocked save
    assert out["rounds"]["2"]["teams"]["t1"]["players"] == ["b"]   # both open: the later save
    assert set(out["rounds"]) == {"1", "2", "3"}


def ep(*updates, end=None):
    return {"start": updates[0][0], "end": end, "lastSeen": updates[-1][0],
            "updates": [{"date": d, "at": at, "status": s} for d, at, s in updates]}


def test_injuries_keep_every_player_and_the_most_recent_record():
    ours = {"updatedAt": "2026-09-30T10:00:00", "players": {
        "1": {"episodes": [ep(("2026-09-28", "2026-09-28T09:00+03:00", "out"))]},
        "2": {"episodes": [ep(("2026-09-28", None, "out"), ("2026-09-30", "2026-09-30T08:00+03:00", "ready"),
                              end="2026-09-30")]}}}
    theirs = {"updatedAt": "2026-09-30T11:00:00", "players": {
        "1": {"episodes": [ep(("2026-09-28", "2026-09-28T09:00+03:00", "out"),
                              ("2026-09-29", "2026-09-29T18:00+03:00", "questionable"))]},
        "2": {"episodes": [ep(("2026-09-28", None, "out"))]},
        "3": {"episodes": [ep(("2026-09-30", "2026-09-30T09:00+03:00", "out"))]}}}
    out = m.merge_injuries(ours, theirs)
    assert [u["status"] for u in out["players"]["1"]["episodes"][0]["updates"]] == ["out", "questionable"]
    assert out["players"]["2"]["episodes"][0]["end"] == "2026-09-30"   # ours saw the recovery
    assert set(out["players"]) == {"1", "2", "3"}
    assert out["updatedAt"] == "2026-09-30T11:00:00"


def test_proballers_prefer_a_found_link_then_the_later_check():
    ours = {"1": {"url": None, "checked": "2026-09-30"}, "2": {"url": "x", "checked": "2026-09-01"}}
    theirs = {"1": {"url": "y", "checked": "2026-09-01"}, "2": {"url": None, "checked": "2026-09-30"},
              "3": {"url": None, "checked": "2026-09-30"}}
    out = m.merge_proballers(ours, theirs)
    assert out["1"]["url"] == "y" and out["2"]["url"] == "x" and set(out) == {"1", "2", "3"}


def test_driver_command_line(tmp_path):
    base, ours, theirs = (tmp_path / n for n in ("base", "ours", "theirs"))
    base.write_text('{"players": {}}')
    ours.write_text(json.dumps({"players": {"1": {"episodes": []}}}))
    theirs.write_text(json.dumps({"players": {"2": {"episodes": []}}}))
    run = subprocess.run([sys.executable, str(TOOLS / "merge_history.py"), str(base), str(ours), str(theirs),
                          "data/injuries.json"])
    assert run.returncode == 0
    assert set(json.loads(ours.read_text())["players"]) == {"1", "2"}
    ours.write_text("not json")
    run = subprocess.run([sys.executable, str(TOOLS / "merge_history.py"), str(base), str(ours), str(theirs),
                          "data/injuries.json"])
    assert run.returncode == 1  # leave it to git as a normal conflict
