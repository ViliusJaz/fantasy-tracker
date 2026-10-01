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
    assert ft.dnp_reason("DNP in Rounds 1-2 (coach's decision)") == (1, "coach's decision")


def test_round_ranges_in_comments_are_translated():
    from backend import injury_lt
    assert injury_lt.translate("DNP in Round 1-2 (coach's decision)") == "Nežaidė 1–2 turuose (trenerio sprendimas)"
    assert injury_lt.translate("DNP in Round 2 (coach's decision)") == "Nežaidė 2 ture (trenerio sprendimas)"


def test_injury_view(ft):
    assert ft.injury_view(entry("1", "ready")) is None
    assert ft.injury_view(None) is None
    ft.LANG.set("en")
    view = ft.injury_view(entry("1", "out", comment="Knee injury", ret="2 weeks"))
    assert view == {"status": "out", "label": "Out", "return": "Out: 2 weeks", "comment": "Knee injury"}
    assert ft.injury_view(None, health="doubtful") == {"status": "doubtful", "label": "Doubtful", "return": "", "comment": ""}
    ft.LANG.set("lt")
    view = ft.injury_view(entry("1", "out", comment="Knee injury", ret="2 weeks"))
    assert view["label"] == "Nežaidžia" and view["comment"] != "Knee injury"  # translated


@pytest.mark.parametrize("raw, status, lt, en", [
    ("Round 3", "uncertain", "Neaišku, ar žais 3 ture", "Uncertain for round 3"),
    ("Round 3", "out", "Nežais 3 ture", "Out for round 3"),
    ("Round 2-4", "out", "Nežais 2–4 turuose", "Out for rounds 2–4"),
    ("Indefinitely", "out", "Nežais neribotą laiką", "Out indefinitely"),
    ("", "out", "", ""),
])
def test_report_round_column_names_the_rounds_missed(ft, raw, status, lt, en):
    """BasketNews' "Round" column is the rounds the status is for, not a return date."""
    ft.LANG.set("en")
    assert ft.return_local(raw, status) == en
    ft.LANG.set("lt")
    assert ft.return_local(raw, status) == lt


def test_injuries_by_club_count_real_positions(monkeypatch):
    from backend.payloads import injuries as pay

    def player(n, club, pos="guard"):
        return {"id": f"p{n}", "bnId": str(n), "name": f"Player {n}", "photo": None, "position": pos, "avgPts": 10,
                "gamesPlayed": 2, "club": {"abbr": club, "name": club, "fullName": f"{club} Full", "nameEn": f"{club} City",
                                           "logo": None}}

    def rep(n, status, comment, club, pos="C"):
        return {"bnId": str(n), "name": f"Player {n}", "club": club, "pos": pos, "status": status,
                "siteLabel": status.title(), "return": "Round 3", "comment": comment}

    pmap = {f"p{n}": player(n, club, pos) for n, club, pos in
            [(1, "AAA", "center"), (2, "AAA", "guard"), (3, "AAA", "guard"), (4, "AAA", "center"),
             (5, "AAA", "guard"), (6, "AAA", "forward"), (7, "BBB", "center")]}
    report = {"1": rep(1, "out", "Knee injury", "AAA City"), "2": rep(2, "uncertain", "DNP in Round 2 (coach's decision)", "AAA City", "PG"),
              "3": rep(3, "expected", "Ankle sprain", "AAA City", "SG"), "9": rep(9, "out", "Back injury", "BBB City", "SF")}
    monkeypatch.setattr(pay, "injury_report", lambda meta: report)
    monkeypatch.setattr(pay, "player_positions", lambda meta: {"1": ["C"], "4": ["C"], "5": ["PG", "SG"], "7": ["C"]})
    clubs = {c["abbr"]: c for c in pay.club_injuries({"id": "x"}, pmap, {})}
    a = clubs["AAA"]
    assert [e["player"]["name"] for e in a["injured"]] == ["Player 1"]
    assert [e["player"]["name"] for e in a["other"]] == ["Player 2"]       # coach's decision: not an injury
    assert [e["player"]["name"] for e in a["expected"]] == ["Player 3"]    # expected back: not counted
    depth = {d["pos"]: d for d in a["depth"]}
    assert depth["C"] == {"pos": "C", "total": 2, "injured": 1, "healthy": 1}
    assert depth["PG"]["total"] == 2 and depth["SG"]["total"] == 2  # p2 PG (report), p5 PG/SG, p3 SG (report)
    assert depth["SF"]["total"] == 1 and depth["PF"]["total"] == 1  # no real position known: the fantasy one
    assert a["short"] == ["C"]
    b = clubs["BBB"]
    assert [e["player"]["name"] for e in b["injured"]] == ["Player 9"]  # not in the game: matched on the club name
    assert b["short"] == []  # one center and nobody hurt there: a roster choice, not an injury problem
    assert list(clubs) == ["AAA", "BBB"]  # clubs that may run short first


ROSTER_PAGE = """<table id="layout"><tr><td>
<p>September 29: news</p>
<table style="border-collapse: collapse;" border="1"><tbody>
<tr class="title"><td><img src="/logo.jpg"></td><td colspan="2"><h2 id="aaa">AAA City Club</h2></td></tr>
<tr class="title"><td><strong>Position</strong></td><td><strong>Player</strong></td><td><strong>Status</strong></td></tr>
<tr><td>PG/SG</td><td><a href="https://basketnews.com/players/5-player-five.html"><strong>Player Five&nbsp;</strong></a>
 (<a href="https://basketnews.com/news-1-story.html">story</a>)</td><td>Signed until 2027</td></tr>
<tr><td>C</td><td><a href="https://basketnews.com/players/1-player-one.html">Player One</a></td><td>N/A</td></tr>
<tr><td>G</td><td><a href="https://basketnews.com/players/8-player-eight.html">Player Eight</a></td><td>N/A</td></tr>
<tr><td>F</td><td>Unlinked Player</td><td>N/A</td></tr>
</tbody></table>
</td></tr></table>"""


def test_rosters_article_is_parsed():
    from backend.sources import rosters
    clubs = rosters.parse_rosters(ROSTER_PAGE)
    assert clubs == {"AAA City Club": [
        {"bnId": "5", "name": "Player Five", "positions": ["PG", "SG"]},
        {"bnId": "1", "name": "Player One", "positions": ["C"]},
        {"bnId": "8", "name": "Player Eight", "positions": ["PG", "SG"]},
    ]}
    assert rosters.positions_of("PF/SF") == ["PF", "SF"] and rosters.positions_of("x") == []


def test_injuries_by_club_use_the_rosters_article(monkeypatch):
    from backend.payloads import injuries as pay

    def player(n, club, pos):
        return {"id": f"p{n}", "bnId": str(n), "name": f"Player {n}", "photo": None, "position": pos, "avgPts": 10,
                "gamesPlayed": 2, "club": {"abbr": club, "name": club, "fullName": f"{club} Full", "nameEn": f"{club} City",
                                           "logo": None}}

    pmap = {f"p{n}": player(n, "AAA", pos) for n, pos in [(1, "center"), (5, "guard"), (6, "guard")]}  # p6 has left
    report = {"1": {"bnId": "1", "name": "Player 1", "club": "AAA City", "pos": "PF", "status": "out",
                    "siteLabel": "Out", "return": "Indefinitely", "comment": "Knee injury"}}
    monkeypatch.setattr(pay, "injury_report", lambda meta: report)
    monkeypatch.setattr(pay, "player_positions", lambda meta: {"1": ["PF"], "5": ["SF"]})
    monkeypatch.setattr(pay, "rosters", lambda meta: {"AAA City Club": [
        {"bnId": "1", "name": "Player 1", "positions": ["C"]}, {"bnId": "5", "name": "Player 5", "positions": ["PG", "SG"]},
        {"bnId": "8", "name": "Player Eight", "positions": ["C"]}]})
    (a,) = pay.club_injuries({"id": "x"}, pmap, {})
    assert a["injured"][0]["positions"] == ["C"]  # the article's position wins
    depth = {d["pos"]: d for d in a["depth"]}
    assert depth["C"] == {"pos": "C", "total": 2, "injured": 1, "healthy": 1}  # Player Eight counts, though not in the game
    assert depth["PG"]["total"] == 1 and depth["SF"]["total"] == 0  # p6 left the club: not on the article's roster
    assert a["short"] == ["C"]
