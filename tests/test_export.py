"""The static export: which files it writes, their JSON shape, and what happens when a
source is down. Runs the real export offline on the recorded answers (tools/replay_export.py)."""
import json
import re

import pytest

from conftest import CLASSIC, HLA, run_replay

LANGS = ("lt", "en")

# Top-level keys of every API answer the page reads. Changing one breaks the frontend.
SCHEMA = {
    "leagues": [{"leagues"}],
    "meta.json": [{"generatedAt"}],
    "league/ID/standings": [{"league", "round", "live", "rows", "hasTies"}],
    "league/ID/rounds": [{"league", "round", "live", "rows"}, {"league", "round", "live", "matchups"}],
    "league/ID/games": [{"league", "round", "state", "games"}],
    "league/ID/records": [{"league", "finished", "round", "roundAwards", "oscars", "records", "form", "missingLineups",
                           "draftAwards", "recap", "efficiency", "schedule", "partialLineups"}],
    "league/ID/free-agents": [{"league", "scope", "statsRound", "players", "totalPlayers", "rosteredPlayers",
                               "injuryReportUrl"}],
    "league/ID/draft": [{"league", "picks", "teams"}],
    "league/ID/transfers": [{"league", "moves", "teams", "upcoming", "lock", "usesCredits", "startingCredits"}],
    "league/ID/injuries": [{"league", "events", "total", "teams", "reportUrl"}],
    "league/ID/team/ID": [{"league", "team", "standing", "standingRound", "round", "roundState", "result", "after",
                           "lineup", "history"}],
    "league/ID/player/ID": [{"league", "player", "owner", "advanced", "leagueAvg", "proballers", "injury", "gameLog",
                             "nextGames", "shooting"}],
}
SCHEMA["league/ID/players"] = SCHEMA["league/ID/free-agents"]

META_KEYS = {"id", "title", "format", "leagueId", "pointCalcSystem", "teamsCount", "commissioner", "competition",
             "currentRound", "roundStarted", "firstRound", "latestRound", "totalRounds", "injuryReportUrl",
             "transferLock", "draft", "bnLeagueId", "seasonYear", "url"}


def kind(rel):
    k = re.sub(r"[0-9a-f]{24}", "ID", rel)
    k = re.sub(r"\.r\d+\.", ".", k)
    return re.sub(r"\.(lt|en)\.json$", "", k)


def api_files(out):
    api = out / "site" / "api"
    return {str(p.relative_to(api)): p for p in api.rglob("*.json")}


def test_replay_ran_clean(replayed):
    _, report = replayed
    assert report["exitCode"] == 0, report["log"][-2000:]
    assert report["missingCount"] == 0, report["missing"]


def test_every_page_the_site_needs_is_there(replayed):
    out, _ = replayed
    files = api_files(out)
    assert "meta.json" in files
    for lang in LANGS:
        assert f"leagues.{lang}.json" in files
        for fid in (HLA, CLASSIC):
            for name in ("standings", "rounds", "games", "records", "free-agents", "players", "draft", "transfers",
                         "injuries"):
                assert f"league/{fid}/{name}.{lang}.json" in files
            league = json.loads(files[f"league/{fid}/standings.{lang}.json"].read_text())["league"]
            for r in range(league["firstRound"], league["totalRounds"]):
                assert f"league/{fid}/games.r{r}.{lang}.json" in files       # the whole schedule
            for r in range(league["firstRound"], league["currentRound"]):
                assert f"league/{fid}/records.r{r}.{lang}.json" in files     # finished rounds
            teams = json.loads(files[f"league/{fid}/standings.{lang}.json"].read_text())["rows"]
            assert len(teams) == league["teamsCount"]
            for row in teams:
                tid = row["team"]["id"]
                assert f"league/{fid}/team/{tid}.{lang}.json" in files
                for r in range(league["firstRound"], league["currentRound"] + 1):
                    assert f"league/{fid}/team/{tid}.r{r}.{lang}.json" in files
            players = [f for f in files if f.startswith(f"league/{fid}/player/") and f.endswith(f".{lang}.json")]
            assert len(players) > 250


def test_api_schema(replayed):
    out, _ = replayed
    for rel, path in api_files(out).items():
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert set(doc) in SCHEMA[kind(rel)], rel
        if "league" in doc and kind(rel) not in ("league/ID/player/ID",):
            assert set(doc["league"]) == META_KEYS, rel


def test_nested_shapes(replayed):
    out, _ = replayed
    files = api_files(out)
    h2h = json.loads(files[f"league/{HLA}/standings.lt.json"].read_text())
    assert set(h2h["rows"][0]) == {"team", "position", "positionGained", "wins", "losses", "ties", "pointsTotal",
                                   "pointsRound"}
    classic = json.loads(files[f"league/{CLASSIC}/standings.lt.json"].read_text())
    assert set(classic["rows"][0]) == {"team", "position", "positionGained", "roundPosition", "pointsTotal",
                                       "pointsRound"}
    player = json.loads(files[f"league/{HLA}/players.en.json"].read_text())["players"][0]
    assert {"id", "bnId", "name", "club", "games", "avgPts", "season", "injury", "owner"} <= set(player)
    team = json.loads(files[f"league/{HLA}/team/{h2h['rows'][0]['team']['id']}.lt.json"].read_text())
    assert set(team["lineup"]) == {"source", "note", "formation", "players", "scoring"}
    assert {"card", "slot", "captain", "slotLabel", "mult", "contrib"} <= set(team["lineup"]["players"][0])


def test_index_page_is_static_and_cache_busted(replayed):
    out, _ = replayed
    html = (out / "site" / "index.html").read_text()
    assert '<meta name="ft-static" content="1">' in html
    assert re.search(r'"app\.js\?v=[0-9a-f]{10}"', html) and re.search(r'"style\.css\?v=[0-9a-f]{10}"', html)
    assert (out / "site" / ".nojekyll").exists()


def test_all_json_is_compact_and_valid(replayed):
    out, _ = replayed
    for rel, path in api_files(out).items():
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        assert "\n" not in text, rel


# ---------------------------------------------------------------- failures

def open_episodes(text):
    players = json.loads(text)["players"]
    return sorted(bn for bn, p in players.items() if p["episodes"] and p["episodes"][-1]["end"] is None)


def test_injury_report_down_leaves_history_untouched(tmp_path, recording):
    report = run_replay(tmp_path / "out", "--fail", "injury-report")
    assert report["exitCode"] == 0  # the site can still be built without it
    after = (tmp_path / "out" / "state" / "data" / "injuries.json").read_text()
    assert after == recording["files"]["data/injuries.json"]


def test_advanced_stats_down_still_builds_every_page(tmp_path):
    report = run_replay(tmp_path / "out", "--fail", "advanced-stats")
    assert report["exitCode"] == 0
    assert len(api_files(tmp_path / "out")) > 1600


@pytest.mark.xfail(strict=True, reason="fixed by validation (phase 3): a failed league is published as missing")
def test_failed_league_is_not_published(tmp_path):
    report = run_replay(tmp_path / "out", "--fail", HLA)
    assert report["exitCode"] != 0


@pytest.mark.xfail(strict=True, reason="fixed by validation (phase 3): an unreadable report closes every injury")
def test_unreadable_injury_page_does_not_close_injuries(tmp_path, recording):
    run_replay(tmp_path / "out", "--blank", "injury-report")
    before = open_episodes(recording["files"]["data/injuries.json"])
    after = open_episodes((tmp_path / "out" / "state" / "data" / "injuries.json").read_text())
    assert before and after == before
