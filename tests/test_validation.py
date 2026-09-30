"""Validation rules: what is a warning, what blocks a source, what stops a build."""
import pytest

from backend import validation as v
from conftest import HLA

FATAL, ERROR, WARNING = v.FATAL, v.ERROR, v.WARNING


def meta(**over):
    m = {"id": HLA, "title": "HLA", "format": "head_to_head", "firstRound": 0, "currentRound": 3, "latestRound": 3,
         "roundStarted": True, "totalRounds": 38, "teamsCount": 4, "seasonYear": 2026}
    m.update(over)
    return m


def rows(n=4, points=100, wins=2):
    return [{"team": {"id": f"t{i}", "title": f"T{i}"}, "position": i + 1, "wins": wins, "losses": 1, "ties": 0,
             "pointsTotal": points, "pointsRound": min(30, points)} for i in range(n)]


def players(n=300, bn=True, played=True, pts=10.0, clubs=18, games=5):
    return {f"p{i}": {"id": f"p{i}", "name": f"P {i}", "bnId": str(i) if bn else None, "gamesPlayed": games,
                      "club": {"abbr": f"C{i % clubs}"}, "roundPlayed": played, "roundPts": pts}
            for i in range(n)}


def levels(report):
    return [(i["level"], i["code"]) for i in report.issues]


def run(check, *args):
    report = v.Report()
    check(report, *args)
    return report


def test_good_data_passes():
    assert run(v.check_meta, meta()).issues == []
    assert run(v.check_standings, meta(), 3, rows()).issues == []
    assert run(v.check_players, meta(), players()).issues == []


@pytest.mark.parametrize("over,code", [
    ({"format": "weird"}, "format"),
    ({"totalRounds": 0}, "total-rounds"),
    ({"currentRound": 50}, "current-round"),
])
def test_impossible_league_settings_are_fatal(over, code):
    assert (FATAL, code) in levels(run(v.check_meta, meta(**over)))


def test_round_going_backwards_in_the_same_season_is_fatal():
    base = {"id": HLA, "seasonYear": 2026, "currentRound": 5}
    assert levels(run(v.check_meta, meta(currentRound=3), base)) == [(FATAL, "round-backwards")]
    new_season = {**base, "seasonYear": 2025}
    assert run(v.check_meta, meta(currentRound=0), new_season).issues == []  # a new season starts over


def test_standings_problems():
    assert levels(run(v.check_standings, meta(), 3, [])) == [(FATAL, "no-teams")]
    assert (FATAL, "team-count") in levels(run(v.check_standings, meta(), 3, rows(3)))
    uneven = rows()
    uneven[0]["wins"] = 5
    assert levels(run(v.check_standings, meta(), 3, uneven)) == [(WARNING, "games-played")]


def test_points_total_may_not_shrink_within_a_season():
    base = {"id": HLA, "seasonYear": 2026, "shownRound": 3, "pointsTotal": 400}
    assert run(v.check_standings, meta(), 3, rows(points=100), base).issues == []
    assert levels(run(v.check_standings, meta(), 3, rows(points=50), base)) == [(FATAL, "points-dropped")]
    assert run(v.check_standings, meta(seasonYear=2027), 0, rows(points=5), base).issues == []


def test_too_few_players_is_fatal():
    assert levels(run(v.check_players, meta(), players(40))) == [(FATAL, "player-count")]
    base = {"id": HLA, "seasonYear": 2026, "players": 330}
    assert levels(run(v.check_players, meta(), players(150), base)) == [(FATAL, "player-drop")]


def test_player_warnings():
    assert levels(run(v.check_players, meta(), players(bn=False))) == [(WARNING, "bn-id")]
    assert levels(run(v.check_players, meta(), players(clubs=4))) == [(WARNING, "clubs")]


def test_season_stats_vanishing_is_fatal():
    base = {"id": HLA, "seasonYear": 2026, "players": 300, "playersWithGames": 280}
    assert levels(run(v.check_players, meta(), players(games=0), base)) == [(FATAL, "season-stats")]


def test_finished_round_needs_box_scores():
    finished = meta(latestRound=2, currentRound=3, roundStarted=False)
    assert levels(run(v.check_players, finished, players(played=False))) == [(FATAL, "round-stats")]
    assert levels(run(v.check_players, finished, players(pts=None))) == [(FATAL, "round-points")]
    # a live round without box scores yet is normal
    assert run(v.check_players, meta(), players(played=False)).issues == []


def lineup(n=11, rnd=3):
    return {"round": rnd, "formation": "1-2-2", "players": [{"id": f"x{i}"} for i in range(n)]}


def test_lineup_checks():
    teams = ["t0", "t1", "t2", "t3"]
    assert run(v.check_lineups, meta(), {t: lineup() for t in teams}, teams).issues == []
    report = run(v.check_lineups, meta(), {"t0": lineup()}, teams)
    assert levels(report) == [(ERROR, "missing")] and report.blocked("lineups")
    assert levels(run(v.check_lineups, meta(), {t: lineup(2) for t in teams}, teams)) == [(WARNING, "roster-size")]
    assert levels(run(v.check_lineups, meta(), {t: lineup(rnd=1) for t in teams}, teams)) == [(WARNING, "old-round")]
    assert run(v.check_lineups, meta(), {t: lineup(rnd=2) for t in teams}, teams).issues == []  # just finished


def injury_log(open_players):
    return {str(i): {"episodes": [{"end": None}]} for i in range(open_players)}


def entry(i, status="out", bn=True):
    return {"bnId": str(i) if bn else None, "status": status}


def test_injury_report_checks():
    assert run(v.check_injury_report, [entry(i) for i in range(20)], injury_log(20)).issues == []
    report = run(v.check_injury_report, [], injury_log(20))
    assert levels(report) == [(ERROR, "empty")] and report.blocked("injuries")
    assert levels(run(v.check_injury_report, [entry(1)], injury_log(20))) == [(ERROR, "shrunk")]
    assert levels(run(v.check_injury_report, [entry(i, bn=False) for i in range(20)], injury_log(20))) == \
        [(ERROR, "unreadable")]
    assert levels(run(v.check_injury_report, [entry(i, "other") for i in range(20)], injury_log(20))) == \
        [(WARNING, "statuses")]
    assert run(v.check_injury_report, [], injury_log(2)).issues == []  # preseason: an empty report is believable


def test_page_checks():
    assert run(v.check_pages, 1700, 0, 1690).issues == []
    assert levels(run(v.check_pages, 1700, 3)) == [(WARNING, "failed-pages")]
    assert levels(run(v.check_pages, 1500, 200)) == [(FATAL, "failed-pages")]
    assert levels(run(v.check_pages, 900, 0, 1700)) == [(FATAL, "page-drop")]
    assert levels(run(v.check_pages, 0, 0)) == [(FATAL, "no-pages")]


def test_report_bookkeeping():
    report = v.Report()
    report.add(WARNING, "x", "a", "note")
    report.add(ERROR, "injuries", "b", "bad")
    assert report.summary() == {"warnings": 1, "errors": 1, "fatal": 0}
    assert report.blocked("injuries") and not report.blocked("x") and not report.fatal
    report.add(FATAL, "export", "c", "stop")
    assert report.fatal


def test_store_refuses_an_unreadable_injury_report(ft, clock):
    """Also the server's path: whoever fetched the report, store() checks it before writing."""
    clock.set("2026-09-28T09:00:00+03:00")
    injured = [{"bnId": str(i), "name": f"P{i}", "club": "C", "pos": "G", "status": "out", "siteLabel": "Out",
                "return": "", "comment": "Knee"} for i in range(8)]
    ft.update_injury_log(injured)
    before = (ft.data_dir / "injuries.json").read_bytes()
    ft.observe_injury_report([], key="https://report")
    changed = ft.store()
    assert changed == {"lineups": 0, "injuries": 0}
    assert (ft.data_dir / "injuries.json").read_bytes() == before
    assert ft.state() == "stale"
    assert len(ft.injury_report({"injuryReportUrl": "https://report"})) == 8  # served from the log
