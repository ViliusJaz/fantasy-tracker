"""Draft-league scoring: slots, multipliers, formations and the best possible lineup."""
import itertools
import random

import pytest

POS = {"c": "center", "f": "forward", "g": "guard"}


def player(ft, pid, card, pts, positions=None, captain=False):
    prefix = card.split("-")[0]
    return {"id": pid, "name": pid, "card": card, "slot": ft.slot_of(card), "captain": captain,
            "roundPts": pts, "positions": positions if positions is not None else [POS.get(prefix, "guard")]}


def full_lineup(ft, pts, captain_card="g-1"):
    """Five starters (c, f, f, g, g), 6th man, four bench players and one inactive."""
    cards = ["c-1", "f-1", "f-2", "g-1", "g-2", "b-1", "b-2", "b-3", "b-4", "b-5", "i-1"]
    return [player(ft, f"p{i}", card, pts[i], captain=card == captain_card) for i, card in enumerate(cards)]


def test_slot_of(ft):
    assert [ft.slot_of(c) for c in ("g-1", "f-2", "c-1")] == ["starter"] * 3
    assert [ft.slot_of(c) for c in ("b-1", "b-5")] == ["bench"] * 2
    assert [ft.slot_of(c) for c in ("i-1", "", None)] == ["inactive"] * 3


def test_slot_label(ft):
    assert ft.slot_label("g-1") == "G"
    assert ft.slot_label("c-1") == "C"
    assert ft.slot_label("b-1") == "6th"
    assert ft.slot_label("b-3") == "B3"
    assert ft.slot_label("i-2") == "Out"
    assert ft.slot_label(None) == "Out"


@pytest.mark.parametrize("card,captain,mult", [
    ("g-1", False, 1), ("f-2", False, 1), ("c-1", False, 1),
    ("g-1", True, 2), ("c-1", True, 2),          # captain doubles
    ("b-1", False, 1),                          # 6th man counts in full
    ("b-2", False, 0.5), ("b-5", False, 0.5),   # bench counts half
    ("b-2", True, 0.5),                         # the captain flag only matters on court
    ("i-1", False, 0), ("i-1", True, 0),        # inactive / out: nothing
])
def test_multiplier(ft, card, captain, mult):
    assert ft.multiplier(card, captain) == mult


def test_score_lineup_totals_and_contributions(ft):
    pts = [10, 8, 6, 20, 4, 12, 10, 2, 0, 5, 30]
    lineup = full_lineup(ft, pts, captain_card="g-1")
    result = ft.score_lineup(lineup)
    contrib = {p["card"]: p["contrib"] for p in lineup}
    assert contrib["g-1"] == 40          # captain x2
    assert contrib["b-1"] == 12          # 6th man x1
    assert contrib["b-2"] == 5           # bench x0.5
    assert contrib["i-1"] == 0           # out: 30 points wasted
    assert {p["card"]: p["slotLabel"] for p in lineup}["b-1"] == "6th"
    expected = 10 + 8 + 6 + 40 + 4 + 12 + 0.5 * (10 + 2 + 0 + 5)
    assert result["total"] == expected
    assert result["lost"] == max(result["optimal"] - result["total"], 0)


def test_missing_points_count_as_zero(ft):
    lineup = full_lineup(ft, [None] * 11)
    assert ft.score_lineup(lineup) == {"total": 0, "optimal": 0, "lost": 0}


def test_out_player_is_never_in_the_optimal_lineup(ft):
    pts = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 100]
    lineup = full_lineup(ft, pts)
    result = ft.score_lineup(lineup)
    # 100 points on the inactive slot cannot be recovered by any legal lineup
    assert result["optimal"] < 20


@pytest.mark.parametrize("positions,fits", [
    (["center", "forward", "forward", "guard", "guard"], True),     # 1-2-2
    (["center", "center", "forward", "guard", "guard"], True),      # 2-1-2
    (["center", "forward", "forward", "forward", "guard"], True),   # 1-3-1
    (["center", "forward", "guard", "guard", "guard"], True),       # 1-1-3
    (["guard"] * 5, False),
    (["center", "center", "center", "forward", "guard"], False),
    (["forward"] * 4 + ["guard"], False),                           # no center
])
def test_formations(ft, positions, fits):
    group = [{"positions": [p]} for p in positions]
    assert ft._fits_formation(group) is fits


def test_multi_position_and_unknown_position_players_are_flexible(ft):
    group = [{"positions": ["guard", "center"]}, {"positions": ["forward"]}, {"positions": ["forward"]},
             {"positions": ["guard"]}, {"positions": ["guard"]}]
    assert ft._fits_formation(group)
    group = [{"positions": []}] + [{"positions": ["guard"]}] * 2 + [{"positions": ["forward"]}] * 2
    assert ft._fits_formation(group)  # no position known: may play anywhere


def reference_optimal(ft, lineup):
    """Brute force straight from the rules: 5 legal starters, one of them captain (x2),
    a 6th man (x1) and the rest of the active players on the bench (x0.5)."""
    active = [p for p in lineup if p["slot"] != "inactive"]
    fp = lambda p: p["roundPts"] or 0  # noqa: E731
    best = None  # fantasy points can be negative
    for court in itertools.combinations(active, 5):
        if not ft._fits_formation(court):
            continue
        rest = [p for p in active if p not in court]
        for sixth in rest or [None]:
            bench = [p for p in rest if p is not sixth]
            total = sum(fp(p) for p in court) + max(fp(p) for p in court) \
                + (fp(sixth) if sixth else 0) + 0.5 * sum(fp(p) for p in bench)
            best = total if best is None else max(best, total)
    return round(best or 0, 2)


def test_optimal_score_matches_brute_force_on_random_rosters(ft):
    rng = random.Random(7)
    for _ in range(60):
        cards = ["c-1", "f-1", "f-2", "g-1", "g-2", "b-1", "b-2", "b-3", "b-4", "b-5", "i-1"]
        lineup = []
        for i, card in enumerate(cards):
            positions = rng.sample(["center", "forward", "guard"], rng.choice([1, 1, 1, 2]))
            lineup.append(player(ft, f"p{i}", card, rng.choice([None, 0, rng.randint(-5, 45)]), positions))
        assert ft.optimal_score(lineup) == reference_optimal(ft, lineup)


def test_optimal_respects_formations(ft):
    # six great guards and weak bigs: at most three guards may start
    lineup = [player(ft, f"g{i}", f"b-{i}" if i else "g-1", 30, ["guard"]) for i in range(6)]
    lineup += [player(ft, "c", "c-1", 1, ["center"]), player(ft, "f", "f-1", 1, ["forward"])]
    court_best = 30 * 3 + 1 + 1 + 30  # three guards + two bigs + captain bonus
    expected = court_best + 30 + 0.5 * 30 * 2  # 6th man + two guards on the bench
    assert ft.optimal_score(lineup) == expected
