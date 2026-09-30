"""The EuroLeague game-winner model: ratings, features, the saved model and the site's use of it."""
import json

import pytest

from backend import predict
from backend.sources import euroleague
from conftest import HLA

PARAMS = {"elo_k": 10, "elo_home": 20, "elo_carry": 0.8, "elo_new_team": 1450.0, "elo_points": 28.0,
          "shrink_games": 12, "form_games": 8, "shrink_type": 2, "bias": 0.4,
          "weights": {"elo": 0.18, "home": 0.22, "matchup": 1.4}}


def game(n, home, away, hs, as_, day, neutral=False):
    box = {c: {"pts": pts, "fga2": 35, "fga3": 25, "fta": 20, "oreb": 10, "tov": 12}
           for c, pts in ((home, hs), (away, as_))}
    return {"id": f"g{n}", "season": "S", "phase": "RS", "round": n, "date": f"2025-10-{day:02d}T18:00:00Z",
            "home": home, "away": away, "neutral": neutral, "hs": hs, "as": as_, "box": box, "players": None}


def season():
    """A strong team (STR) that wins everything and a weak one (WEA) that loses everything."""
    games, day = [], 1
    for i, (h, a, hs, as_) in enumerate([("STR", "MID", 90, 70), ("MID", "WEA", 85, 70), ("WEA", "STR", 60, 95),
                                         ("MID", "STR", 70, 88), ("STR", "WEA", 99, 60), ("WEA", "MID", 65, 80)] * 3):
        games.append(game(i, h, a, hs, as_, day))
        day += 1
    return games


def test_ratings_follow_results():
    state = predict.State(PARAMS)
    state.start_season("S")
    for g in season():
        state.update(g)
    r = {c: t.rating for c, t in state.teams.items()}
    assert r["STR"] > r["MID"] > r["WEA"]
    assert abs(sum(r.values()) - 3 * 1450) < 1e-6  # Elo only moves points around


def test_prediction_favours_the_better_team_and_home_court():
    state = predict.State(PARAMS)
    state.start_season("S")
    for g in season():
        state.update(g)
    upcoming = {"home": "WEA", "away": "STR", "date": "2025-11-01T18:00:00Z", "neutral": False}
    p_home = state.probability(state.features(upcoming))
    assert p_home < 0.2  # the weak team at home against the strong one
    even = {"home": "MID", "away": "MID2", "date": "2025-11-01T18:00:00Z", "neutral": False}
    assert state.probability(state.features(even)) > 0.5  # nothing known: home court decides
    neutral = dict(upcoming, neutral=True)
    assert state.probability(state.features(neutral)) < p_home


def test_features_are_all_there_and_finite():
    state = predict.State(PARAMS)
    state.start_season("S")
    games = season()
    for g in games[:8]:
        state.update(g)
    feats = state.features(games[8])
    assert set(feats) == set(predict.FEATURES)
    assert all(isinstance(v, (int, float)) and abs(v) < 100 for v in feats.values())


def test_new_season_regresses_ratings_and_keeps_priors():
    state = predict.State(PARAMS)
    state.start_season("S")
    for g in season():
        state.update(g)
    before = state.teams["STR"].rating
    state.start_season("T")
    assert 1500 < state.teams["STR"].rating < before
    assert state.teams["STR"].games == [] and state.teams["STR"].prior["off"] > state.teams["WEA"].prior["off"]


def test_explain_names_home_court():
    feats = {k: 0.0 for k in predict.FEATURES}
    feats.update(home=1.0, elo=2.0)
    parts = dict(predict.explain(feats, PARAMS, "A", "B"))
    assert parts["home"] == pytest.approx(0.4 + 0.22) and parts["elo"] == pytest.approx(0.36)


def test_saved_model_is_complete():
    params = predict.load_params()
    assert params and set(params["weights"]) <= set(predict.FEATURES)
    for key in ("elo_k", "elo_home", "elo_carry", "bias", "ratings", "priors", "backtest"):
        assert key in params
    assert params["backtest"]["games"] > 300 and 50 < params["backtest"]["accuracy"] < 90
    assert len(params["ratings"]["teams"]) >= 18


def test_normalize_reads_both_box_score_formats():
    raw = {"identifier": "E2025_1", "phaseType": {"code": "RS"}, "round": 1, "utcDate": "2025-10-01T18:00:00Z",
           "played": True, "isNeutralVenue": False, "audience": 9000,
           "local": {"club": {"code": "AAA", "name": "Home"}, "score": 80},
           "road": {"club": {"code": "BBB", "name": "Away"}, "score": 75}}
    total = {"points": 80, "fieldGoalsAttempted2": 30, "fieldGoalsAttempted3": 25, "freeThrowsAttempted": 20,
             "offensiveRebounds": 9, "turnovers": 11}
    v3 = {"local": {"total": total, "players": [{"player": {"person": {"code": "001"}}, "stats": {"timePlayed": 1500, "valuation": 12}}]},
          "road": {"total": dict(total, points=75), "players": []}}
    g = euroleague.normalize(raw, "E2025", v3)
    assert g["box"]["AAA"]["pts"] == 80 and g["box"]["BBB"]["pts"] == 75
    assert g["players"]["AAA"] == [{"id": "001", "min": 25.0, "pir": 12}] and g["audience"] == 9000
    live = {"Stats": [{"PlayersStats": [{"Team": "AAA", "Player_ID": "P001  ", "Minutes": "25:00", "Valuation": 12}],
                       "totr": {"Points": 80, "FieldGoalsAttempted2": 30, "FieldGoalsAttempted3": 25,
                                "FreeThrowsAttempted": 20, "OffensiveRebounds": 9, "Turnovers": 11}},
                      {"PlayersStats": [{"Team": "BBB", "Player_ID": "P002", "Minutes": None, "Valuation": 0}],
                       "totr": {"Points": 75, "FieldGoalsAttempted2": 30, "FieldGoalsAttempted3": 25,
                                "FreeThrowsAttempted": 20, "OffensiveRebounds": 9, "Turnovers": 11}}]}
    g2 = euroleague.normalize(raw, "E2025", live)
    assert g2["box"] == g["box"] and g2["players"]["AAA"] == [{"id": "001", "min": 25.0, "pir": 12}]
    unplayed = euroleague.normalize(dict(raw, played=False), "E2025")
    assert unplayed["hs"] is None and unplayed["box"] is None


def test_club_codes_match_on_names(ft):
    clubs = {"ŽAL": {"nameEn": "Zalgiris Kaunas"}, "MTA": {"nameEn": "Maccabi Rapyd Tel Aviv"},
             "HTA": {"nameEn": "Hapoel IBI Tel Aviv"}, "BAY": {"nameEn": "FC Bayern Munich"}}
    games = [{"names": {"ZAL": "Zalgiris Kaunas", "TEL": "Maccabi Rapyd Tel Aviv", "HTA": "Hapoel IBI Tel Aviv",
                        "MUN": "FC Bayern Munich"}}]
    assert ft.club_codes(clubs, games) == {"ŽAL": "ZAL", "MTA": "TEL", "HTA": "HTA", "BAY": "MUN"}


def test_previews_carry_a_prediction(replayed):
    out, _ = replayed
    doc = json.loads((out / "site" / "api" / "league" / HLA / "games.r5.lt.json").read_text())
    picks = [g["preview"]["prediction"] for g in doc["games"] if g.get("preview")]
    assert picks and all(p for p in picks)
    for p in picks:
        assert 0.5 <= p["prob"] <= 1 and p["winner"] and p["factors"]
        assert {"homeWin", "winner", "prob", "factors", "accuracy"} == set(p)
