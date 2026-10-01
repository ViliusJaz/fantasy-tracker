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
                           "draftAwards", "efficiency", "schedule", "partialLineups"}],
    "league/ID/free-agents": [{"league", "scope", "statsRound", "players", "totalPlayers", "rosteredPlayers",
                               "injuryReportUrl", "advanced"}],
    "league/ID/draft": [{"league", "picks", "teams"}],
    "league/ID/transfers": [{"league", "moves", "teams", "upcoming", "lock", "usesCredits", "startingCredits"}],
    "league/ID/injuries": [{"league", "events", "total", "teams", "reportUrl", "clubs"}],
    "league/ID/team/ID": [{"league", "team", "standing", "standingRound", "round", "roundState", "result", "after",
                           "lineup", "history"}],
    "league/ID/player/ID": [{"league", "player", "owner", "advanced", "leagueAvg", "proballers", "injury", "gameLog",
                             "nextGames", "shooting", "defense"}],
}
SCHEMA["league/ID/players"] = SCHEMA["league/ID/free-agents"]
SCHEMA["league/ID/defenses"] = [{"league", "teams", "top", "rows"}]
SCHEMA["league/ID/analytics"] = [{"league", "basedOn", "teams", "rivalries", "draft", "transfers", "records"}]
SCHEMA["health.json"] = [{"version", "status", "lastAttempt", "lastSuccessfulUpdate", "buildDurationSeconds",
                          "basketnewsRequests", "failedRequests", "playersProcessed", "pagesGenerated",
                          "validationWarnings", "validationErrors", "sourceStatus", "lastSuccess", "lastFailure"}]

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
                         "injuries", "analytics", "defenses"):
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


def test_player_games_carry_the_opponents_defensive_rank(replayed):
    out, _ = replayed
    files = api_files(out)
    doc = json.loads(next(p for rel, p in files.items() if rel.startswith(f"league/{HLA}/player/")).read_text())
    assert doc["defense"] == {"teams": 20, "top": 10}
    ranks = {g["opponent"]: g["oppDefRank"] for row in doc["gameLog"] for g in row["games"]}
    assert ranks and all(r is None or 1 <= r <= 20 for r in ranks.values())
    assert any(r is not None for r in ranks.values())


def test_player_lists_carry_advanced_stats(replayed):
    out, _ = replayed
    doc = json.loads(api_files(out)[f"league/{HLA}/players.en.json"].read_text())
    adv = doc["advanced"]
    keys = [c["key"] for c in adv["columns"]]
    assert len(keys) == 21 and {"usage_percentage", "ts_percentage", "turnover_percentage"} <= set(keys)
    assert {c["group"] for c in adv["columns"]} == {g["id"] for g in adv["groups"]}
    assert adv["context"]["ts_percentage"]["better"] == "higher"
    assert adv["context"]["turnover_percentage"]["better"] == "lower"
    regular = [p for p in doc["players"] if (p["season"] or {}).get("min", 0) >= adv["contextMinutes"]]
    assert sum(1 for p in regular if p["adv"]) > 0.9 * len(regular)
    assert all(p["adv"] is None or set(p["adv"]) == set(keys) for p in doc["players"])
    assert all(isinstance(p["dd"], int) and p["dd"] >= 0 for p in doc["players"])


def test_defense_ranking(replayed):
    out, _ = replayed
    doc = json.loads(api_files(out)[f"league/{HLA}/defenses.lt.json"].read_text())
    assert doc["teams"] == 20 and doc["top"] == 10
    assert [r["rank"] for r in doc["rows"]] == list(range(1, 21))
    assert all(r["abbr"] and r["name"] for r in doc["rows"])
    values = [r["value"] for r in doc["rows"]]
    assert values == sorted(values)  # rank 1 allows the fewest points


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
        assert "\n" not in text or rel == "health.json", rel  # health.json is meant to be read by people


# ---------------------------------------------------------------- health and failures

def health(out):
    return json.loads((out / "var" / "health.json").read_text())


def test_health_of_a_good_build(replayed):
    out, _ = replayed
    doc = json.loads((out / "site" / "api" / "health.json").read_text())
    assert doc == health(out)
    assert doc["status"] == "healthy" and doc["lastSuccessfulUpdate"] == doc["lastAttempt"]
    assert doc["lastAttempt"].startswith("2026-09-30T12:59:55")  # the recording's (frozen) time
    assert doc["pagesGenerated"] == len(api_files(out)) - 2        # all but meta.json and health.json
    assert doc["playersProcessed"] > 250 and doc["basketnewsRequests"] > 50 and doc["failedRequests"] == 0
    assert doc["sourceStatus"] == {"basketnews": "ok", "injuries": "ok", "advancedStats": "ok", "euroleague": "ok"}
    assert doc["lastFailure"] is None
    metrics = doc["lastSuccess"]["metrics"]
    assert set(metrics["leagues"]) == {HLA, CLASSIC} and metrics["pages"] == doc["pagesGenerated"]
    assert "token" not in json.dumps(doc).lower()


def open_episodes(text):
    players = json.loads(text)["players"]
    return sorted(bn for bn, p in players.items() if p["episodes"] and p["episodes"][-1]["end"] is None)


def test_injury_report_down_leaves_history_untouched_and_shows_the_last_known_list(tmp_path, recording):
    out = tmp_path / "out"
    report = run_replay(out, "--fail", "injury-report")
    assert report["exitCode"] == 0  # the site can still be built without it
    after = (out / "state" / "data" / "injuries.json").read_text()
    assert after == recording["files"]["data/injuries.json"]
    injured = [p for p in json.loads((out / "site" / "api" / f"league/{HLA}/players.en.json").read_text())["players"]
               if p["injury"]]
    assert len(injured) >= len(open_episodes(after)) * 0.8  # not "nobody is injured"
    doc = health(out)
    assert doc["status"] == "degraded" and doc["sourceStatus"]["injuries"] == "stale"


def test_advanced_stats_down_still_builds_every_page(tmp_path):
    out = tmp_path / "out"
    report = run_replay(out, "--fail", "advanced-stats")
    assert report["exitCode"] == 0
    assert len(api_files(out)) > 1600
    assert health(out)["sourceStatus"]["advancedStats"] == "failed"


def test_failed_league_is_not_published(tmp_path, recording):
    out = tmp_path / "out"
    report = run_replay(out, "--fail", HLA)
    assert report["exitCode"] != 0
    assert not (out / "site").exists()                       # nothing to publish
    for name in ("data/lineups/6aa6bddec90ec6ddaf50d152.json", "data/lineups/6aa7fe67c95ed14589bf170d.json",
                 "data/injuries.json"):
        assert (out / "state" / name).read_text() == recording["files"][name]  # no history from a failed build
    doc = health(out)
    assert doc["status"] == "failed" and doc["lastFailure"]["validation"]["fatal"] >= 1
    assert "unavailable" in json.dumps(doc["lastFailure"]["validation"]["issues"])


def test_a_failed_build_keeps_the_previous_site(tmp_path):
    out = tmp_path / "out"
    run_replay(out)
    before = {p: p.read_bytes() for p in (out / "site").rglob("*") if p.is_file()}
    good = health(out)
    report = run_replay(out, "--keep", "--fail", "playersSearchRecordsFromClient")
    assert report["exitCode"] != 0
    assert {p: p.read_bytes() for p in (out / "site").rglob("*") if p.is_file()} == before
    doc = health(out)
    assert doc["status"] == "failed"
    assert doc["lastSuccessfulUpdate"] == good["lastSuccessfulUpdate"]   # the last good build is remembered
    assert doc["lastSuccess"] == good["lastSuccess"] and doc["lastFailure"]["status"] == "failed"


def test_unreadable_injury_page_does_not_close_injuries(tmp_path, recording):
    out = tmp_path / "out"
    report = run_replay(out, "--blank", "injury-report")
    assert report["exitCode"] == 0
    before = open_episodes(recording["files"]["data/injuries.json"])
    after = open_episodes((out / "state" / "data" / "injuries.json").read_text())
    assert before and after == before
    doc = health(out)
    assert doc["status"] == "degraded" and doc["validationErrors"] >= 1
