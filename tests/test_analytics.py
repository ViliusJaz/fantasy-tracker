"""Season metrics: formulas on small hand-made seasons, and the same numbers from both sources."""
import json

import pytest

from backend.analytics import metrics
from conftest import CLASSIC, HLA


def season(rounds, teams=("A", "B", "C", "D"), **extra):
    ds = {"league": {"id": "L", "title": "L", "format": "head_to_head"},
          "teams": {t: {"id": t, "title": t} for t in teams}, "rounds": rounds, "future": {}, "transfers": [],
          "draft": [], "fp": {}, "rosters": {}}
    ds.update(extra)
    return ds


def rd(rnd, scores, *games, lineups=None):
    return {"round": rnd, "scores": scores, "lineups": lineups or {},
            "matchups": [{"team1": a, "team2": b, "score1": scores[a], "score2": scores[b]} for a, b in games]}


TWO_ROUNDS = season([
    rd(0, {"A": 100, "B": 80, "C": 90, "D": 80}, ("A", "B"), ("C", "D")),
    rd(1, {"A": 70, "B": 120, "C": 60, "D": 95}, ("A", "C"), ("B", "D")),
])


def test_all_play():
    ap = metrics.all_play(TWO_ROUNDS)
    # round 0: A beats 3; C beats B, D; B and D tie. round 1: B beats 3; D beats A, C; A beats C.
    assert ap["A"] == {"wins": 4, "losses": 2, "ties": 0, "pct": round(4 / 6, 3)}
    assert ap["B"] == {"wins": 3, "losses": 2, "ties": 1, "pct": round(3.5 / 6, 3)}
    assert ap["C"] == {"wins": 2, "losses": 4, "ties": 0, "pct": round(2 / 6, 3)}
    assert sum(r["wins"] for r in ap.values()) == sum(r["losses"] for r in ap.values())


def test_expected_wins_and_luck():
    exp = metrics.expected_wins(TWO_ROUNDS)
    assert exp == {"A": round(3 / 3 + 1 / 3, 2), "B": round(0.5 / 3 + 3 / 3, 2), "C": round(2 / 3 + 0 / 3, 2),
                   "D": round(0.5 / 3 + 2 / 3, 2)}
    luck = metrics.luck(TWO_ROUNDS)
    # A beat B and C: 2 wins; D lost to C (90) and B (120) despite outscoring half the league
    assert luck["A"] == {"wins": 2, "expected": exp["A"], "luck": round(2 - exp["A"], 2)}
    assert luck["D"] == {"wins": 0, "expected": exp["D"], "luck": round(-exp["D"], 2)}
    assert min(luck, key=lambda t: luck[t]["luck"]) == "D"  # the unluckiest
    assert round(sum(v["wins"] for v in luck.values()), 2) == 4


def test_results_streaks_and_rivalries():
    res = metrics.results(TWO_ROUNDS)
    assert [x["result"] for x in res["A"]] == ["W", "W"] and [x["result"] for x in res["D"]] == ["L", "L"]
    assert metrics.streaks(["W", "W", "L", "W", "W", "W"]) == (3, 1, ("W", 3))
    assert metrics.streaks(["T", "L", "L"]) == (0, 2, ("L", 2))
    riv = metrics.rivalries(season([rd(0, {"A": 10, "B": 20}, ("A", "B")), rd(5, {"A": 30, "B": 30}, ("B", "A"))],
                                   teams=("A", "B")))
    assert riv == {("A", "B"): {"games": 2, "wins": {"A": 0, "B": 1}, "ties": 1, "points": {"A": 40.0, "B": 50.0}}}


def test_records():
    rec = metrics.records(TWO_ROUNDS)
    assert rec["highestScore"] == {"team": "B", "round": 1, "points": 120}
    assert rec["lowestScore"] == {"team": "C", "round": 1, "points": 60}
    assert rec["biggestWin"]["team"] == "B" and rec["biggestWin"]["margin"] == 25
    assert rec["closestWin"]["margin"] == 10
    assert rec["mostPointsInLoss"]["for"] == 95
    assert rec["fewestPointsInWin"] == {"team": "A", "opponent": "C", "round": 1, "for": 70, "against": 60,
                                        "margin": 10}


def slot(pid, card, fp, captain=False, pos="guard"):
    return {"id": pid, "card": card, "captain": captain, "fp": fp, "positions": [pos]}


LINEUP = [slot("c", "c-1", 10, pos="center"), slot("f1", "f-1", 8, pos="forward"), slot("f2", "f-2", 6, pos="forward"),
          slot("g1", "g-1", 30, pos="guard"), slot("g2", "g-2", 4, captain=True, pos="guard"),
          slot("b1", "b-1", 5), slot("b2", "b-2", 20), slot("i1", "i-1", 50)]


def test_efficiency_and_captain_loss():
    ds = season([rd(0, {"A": 0}, lineups={"A": LINEUP})], teams=("A",))
    real = 10 + 8 + 6 + 30 + 2 * 4 + 5 + 0.5 * 20            # 77
    eff = metrics.efficiency(ds)["A"]
    assert eff["points"] == real
    # best: court c, f1, f2, g1, b2 (as a guard) with g1 captain (+30), 6th man g2 or b1 (5)
    best = 10 + 8 + 6 + 30 + 20 + 30 + 5 + 0.5 * 4
    assert eff["optimal"] == best and eff["lost"] == best - real and eff["rounds"] == 1
    assert eff["efficiency"] == round(100 * real / best, 1)
    cap = metrics.captain_loss(ds)["A"]
    assert cap == {"lost": 26.0, "rounds": 1, "worst": {"round": 0, "lost": 26.0}}  # g1 (30) instead of g2 (4)


def test_transfer_net():
    fp = {1: {"new": 20, "old": 5}, 2: {"new": 10, "old": 15}, 3: {"new": 30, "old": 0}}
    moves = [{"id": "t", "round": 2, "kind": "free_agent", "offer": {"team": "A", "players": ["old"]},
              "request": {"team": None, "players": ["new"]}}]
    rosters = {3: {"A": {"x"}}}  # dropped the new player again before round 3
    net = metrics.transfer_net(moves, fp, [1, 2, 3], rosters)
    assert net == {"t": {"A": {"inFp": 10, "outFp": 15, "net": -5, "rounds": 2}}}
    trade = [{"id": "x", "round": 1, "kind": "trade", "offer": {"team": "A", "players": ["old"]},
              "request": {"team": "B", "players": ["new"]}}]
    both = metrics.transfer_net(trade, fp, [1, 2, 3])["x"]
    assert both["A"]["net"] == 60 - 20 and both["B"]["net"] == 20 - 60


def test_draft_value():
    ds = season([], teams=("A", "B"), draft=[{"pick": 1, "team": "A", "player": "p1"},
                                            {"pick": 2, "team": "B", "player": "p2"},
                                            {"pick": 3, "team": "A", "player": "p3"}],
                fp={0: {"p1": 5, "p2": 10, "p3": 30}})
    dv = metrics.draft_value(ds)
    assert [(p["player"], p["rank"], p["value"]) for p in dv["picks"]] == [("p1", 3, -2), ("p2", 2, 0), ("p3", 1, 2)]
    assert dv["teams"]["A"]["value"] == 0 and dv["teams"]["A"]["best"]["player"] == "p3"
    assert sum(t["value"] for t in dv["teams"].values()) == 0


def test_schedule_strength():
    ds = dict(TWO_ROUNDS, future={2: [{"team1": "A", "team2": "B"}], 3: [{"team1": "A", "team2": "D"}]})
    st = metrics.schedule_strength(ds)
    assert st["A"]["faced"] == [100.0, 75.0]      # B averaged 100, C 75
    assert st["A"]["against"] == [80, 60]
    assert st["A"]["next5"] == [100.0, 87.5]      # B then D
    assert st["C"]["next5"] == []


def test_no_lineups_means_no_lineup_metrics():
    assert metrics.efficiency(TWO_ROUNDS) == {} and metrics.captain_loss(TWO_ROUNDS) == {}


# ---------------------------------------------------------------- the real (recorded) season

@pytest.fixture
def pages(replayed):
    out, _ = replayed

    def page(fid, name):
        return json.loads((out / "site" / "api" / "league" / fid / f"{name}.en.json").read_text())
    return page


def test_analytics_files_are_exported(replayed, pages):
    out, _ = replayed
    for fid in (HLA, CLASSIC):
        lt = (out / "site" / "api" / "league" / fid / "analytics.lt.json").read_text()
        assert lt == (out / "site" / "api" / "league" / fid / "analytics.en.json").read_text()  # numbers only
        doc = json.loads(lt)
        assert set(doc) == {"league", "basedOn", "teams", "rivalries", "draft", "transfers", "records"}
        assert doc["basedOn"]["rounds"] == [0] and doc["basedOn"]["missingLineups"] == []
        assert len(doc["teams"]) == 8 and doc["draft"]["picks"]
    classic = pages(CLASSIC, "analytics")
    assert "luck" not in classic["teams"][0] and classic["rivalries"] == []   # head-to-head only


def test_history_and_live_pages_agree(pages):
    """The records page (live breakdowns) and analytics.json (stored history) use the same
    formulas: on the same round they give the same numbers."""
    for fid in (HLA, CLASSIC):
        live = {r["team"]["id"]: {k: r[k] for k in ("points", "optimal", "lost", "efficiency", "rounds")}
                for r in pages(fid, "records")["efficiency"]}
        stored = {t["team"]["id"]: t["efficiency"] for t in pages(fid, "analytics")["teams"] if t["efficiency"]}
        assert live and live == stored
    live = {r["team"]["id"]: r["faced"] for r in pages(HLA, "records")["schedule"]}
    stored = {t["team"]["id"]: t["schedule"]["faced"] for t in pages(HLA, "analytics")["teams"]}
    assert live == stored
