"""Backtest of the EuroLeague game-winner model (backend/predict.py) on past seasons.

    python3 tools/el_data.py fetch            once: results and box scores into var/backtest/
    python3 tools/predict_research.py         baselines, tuning, the model, error analysis
    python3 tools/predict_research.py --save  also write backend/model/euroleague.json

Honest split: settings and features are chosen on two validation seasons (train on 2021-23,
judge 2023-24; train on 2021-24, judge 2024-25), and only then is the final model, trained on
2021-25, run on 2025-26 (test). Every prediction uses only games played before it; the ratings
of 2021-22 start from scratch, so that season only trains. The saved model is refitted on all.
"""
import argparse
import itertools
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from backend import predict  # noqa: E402
from backend.sources import euroleague  # noqa: E402
import el_data  # noqa: E402

SEASONS = ["E2021", "E2022", "E2023", "E2024", "E2025"]
FOLDS = [(["E2021", "E2022"], "E2023"), (["E2021", "E2022", "E2023"], "E2024")]
TEST = "E2025"
TRAIN_ALL = ["E2021", "E2022", "E2023", "E2024"]
CANDIDATES = ("form", "rest", "b2b", "absent", "matchup", "vs_type", "home_edge", "streak", "crowd", "roster",
              "recent", "travel", "srs")


# ---------------------------------------------------------------- data

def load_season(season):
    """A season's finished games in the model's shape, from the downloaded files (the same
    normalisation the site uses)."""
    games = []
    for g in el_data.load(el_data.games_path(season))["data"]:
        if not g.get("played"):
            continue
        path = el_data.box_path(season, g["gameCode"])
        games.append(euroleague.normalize(g, season, el_data.load(path) if path.exists() else None))
    games.sort(key=lambda g: g["date"])
    return games


# ---------------------------------------------------------------- walking the seasons

def walk(data, params, seasons=SEASONS):
    """Feature rows for every game in date order: [(season, game, features, home_won)]."""
    state = predict.State(params)
    rows = []
    for season in seasons:
        state.start_season(season)
        for game in data[season]:
            feats = state.features(game)
            rows.append((season, game, feats, 1 if game["hs"] > game["as"] else 0))
            state.update(game)
    return rows, state


def elo_prob(feats, params):
    diff = feats["elo"] * 100 + (params["elo_home"] if feats["home"] else 0)
    return 1 / (1 + 10 ** (-diff / 400))


# ---------------------------------------------------------------- logistic regression (plain Python)

def fit(rows, names, l2=1.0, iters=300):
    """Newton's method for L2-regularised logistic regression. Returns (bias, {name: weight})."""
    k = len(names) + 1
    w = [0.0] * k
    xs = [[1.0] + [f[n] for n in names] for f, _ in rows]
    ys = [y for _, y in rows]
    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for x, y in zip(xs, ys):
            z = sum(wi * xi for wi, xi in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            for i in range(k):
                grad[i] += (p - y) * x[i]
                for j in range(k):
                    hess[i][j] += p * (1 - p) * x[i] * x[j]
        for i in range(1, k):
            grad[i] += l2 * w[i]
            hess[i][i] += l2
        step = solve(hess, grad)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w[0], dict(zip(names, w[1:]))


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def logistic(bias, weights, feats):
    z = bias + sum(weights[k] * feats[k] for k in weights)
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


# ---------------------------------------------------------------- scoring

def score(pairs):
    """pairs: [(p_home, home_won)] -> accuracy, log loss, Brier, n."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    acc = sum(1 for p, y in pairs if (p > 0.5) == (y == 1) or (p == 0.5 and y == 1)) / n
    ll = -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    return {"n": n, "acc": round(100 * acc, 1), "logloss": round(ll, 4), "brier": round(brier, 4)}


def line(name, s):
    return f"  {name:44} {s['acc']:5.1f}%  logloss {s['logloss']:.4f}  brier {s['brier']:.4f}  (n={s['n']})"


DEFAULTS = {"elo_k": 20.0, "elo_home": 60.0, "elo_carry": 0.7, "elo_new_team": 1450.0, "elo_points": 28.0,
            "shrink_games": 4, "form_games": 5, "shrink_type": 3, "bias": 0.0, "weights": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    data = {s: load_season(s) for s in SEASONS}
    for s in SEASONS:
        with_box = sum(1 for g in data[s] if g["box"])
        print(f"{s}: {len(data[s])} games, {with_box} with box scores, home won "
              f"{100 * sum(g['hs'] > g['as'] for g in data[s]) / len(data[s]):.1f}%")
    run(data, args.save)


def folds_score(rows, names, l2=1.0):
    """Average validation log loss / accuracy over the folds for a feature set."""
    ll, acc = [], []
    for train_seasons, valid in FOLDS:
        train = [(f, y) for season, _, f, y in rows if season in train_seasons]
        b, w = fit(train, names, l2)
        sc = score([(logistic(b, w, f), y) for season, _, f, y in rows if season == valid])
        ll.append(sc["logloss"])
        acc.append(sc["acc"])
    return sum(ll) / len(ll), sum(acc) / len(acc)


def run(data, save):
    params = dict(DEFAULTS)
    test_games = data[TEST]
    print(f"\nBaselines (test season {TEST}):")
    print(line("home team always", score([(0.6, 1 if g["hs"] > g["as"] else 0) for g in test_games])))
    rows, _ = walk(data, params)
    rec, pairs = {}, []
    for season, g, f, y in rows:
        if season != TEST:
            continue
        wh, wa = rec.get(g["home"], [0, 0]), rec.get(g["away"], [0, 0])
        ph, pa = (wh[0] + 1) / (wh[1] + 2), (wa[0] + 1) / (wa[1] + 2)
        pairs.append((0.6 if ph == pa else 0.7 if ph > pa else 0.3, y))
        for code, won in ((g["home"], y), (g["away"], 1 - y)):
            r = rec.setdefault(code, [0, 0])
            r[0] += won
            r[1] += 1
    print(line("better record so far (tie: home)", score(pairs)))

    # 1. Elo settings, chosen on the validation folds (elo + home in the logistic model)
    best = None
    for k, home, carry in itertools.product((6, 8, 10, 14, 18, 24), (20, 40, 70), (0.65, 0.8, 0.9)):
        p = dict(params, elo_k=k, elo_home=home, elo_carry=carry)
        rows, _ = walk(data, p)
        ll, acc = folds_score(rows, ["elo", "home"])
        if best is None or ll < best[0]:
            best = (ll, acc, p)
    ll, acc, params = best
    print(f"\nElo chosen on the folds: K={params['elo_k']}, home={params['elo_home']}, carry-over={params['elo_carry']} "
          f"(validation {acc:.1f}%, logloss {ll:.4f})")
    for key, values in (("shrink_games", (2, 4, 8, 12)), ("form_games", (3, 5, 8)), ("shrink_type", (2, 3, 6))):
        scores = []
        for v in values:
            rows, _ = walk(data, dict(params, **{key: v}))
            scores.append((folds_score(rows, ["elo", "home", "matchup", "form", "vs_type", "home_edge"])[0], v))
        params[key] = min(scores)[1]
    print(f"  shrink_games={params['shrink_games']}, form_games={params['form_games']}, shrink_type={params['shrink_type']}")
    rows, _ = walk(data, params)

    # 2. Forward selection of features on the folds
    chosen = ["elo", "home"]
    base_ll, base_acc = folds_score(rows, chosen)
    print(f"\nFeatures (validation average of {len(FOLDS)} folds; start elo + home: {base_acc:.1f}%, logloss {base_ll:.4f}):")
    remaining = list(CANDIDATES)
    while remaining:
        trials = [(folds_score(rows, chosen + [n]), n) for n in remaining]
        (ll, acc), name = min(trials)
        for (l2, a2), n in sorted(trials):
            print(f"  + {n:9} {a2:5.1f}%  logloss {l2:.4f}{'   <- best' if n == name else ''}")
        if ll > base_ll - 0.0005:
            print("  (no feature improves it further)")
            break
        chosen.append(name)
        remaining.remove(name)
        base_ll, base_acc = ll, acc
        print(f"  kept {name}: {acc:.1f}%, logloss {ll:.4f}\n")

    # 3. The final model: trained on 2021-25, tested once on 2025-26
    fitted = [(f, y) for s, _, f, y in rows if s in TRAIN_ALL]
    b, w = fit(fitted, chosen)
    test = [(g, f, y) for s, g, f, y in rows if s == TEST]
    probs = [(logistic(b, w, f), y) for _, f, y in test]
    print(f"\nModel ({', '.join(chosen)}), trained 2021-25, test {TEST}:")
    print(line("model", score(probs)))
    be, we = fit(fitted, ["elo", "home"])
    print(line("rating + home court only", score([(logistic(be, we, f), y) for _, f, y in test])))
    if "absent" in w:
        b0, w0 = fit(fitted, [c for c in chosen if c != "absent"])
        print(line("same without the missing-players feature", score([(logistic(b0, w0, f), y) for _, f, y in test])))
    print("  weights: " + ", ".join(f"{k} {v:+.3f}" for k, v in w.items()) + f", bias {b:+.3f}")
    analyse(test, b, w)
    if save:
        save_model(data, params, chosen, score(probs))
    return data, params, chosen, rows


def analyse(test, b, w):
    """Where the test-season predictions go wrong."""
    rows = [(g, f, y, logistic(b, w, f)) for g, f, y in test]
    print("\nBy phase:")
    for phase in ("RS", "PI", "PO", "FF"):
        part = [(p, y) for g, f, y, p in rows if g["phase"] == phase]
        if part:
            print(line(phase, score(part)))
    print("By round (regular season):")
    for lo, hi in ((1, 5), (6, 12), (13, 24), (25, 38)):
        part = [(p, y) for g, f, y, p in rows if g["phase"] == "RS" and lo <= g["round"] <= hi]
        print(line(f"rounds {lo}-{hi}", score(part)))
    print("Calibration (how sure the model was -> how often the favourite won):")
    for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        part = [(max(p, 1 - p), (y == 1) == (p >= 0.5)) for g, f, y, p in rows if lo <= max(p, 1 - p) < hi]
        if part:
            print(f"  {100 * lo:3.0f}-{min(100, 100 * hi):3.0f}%: {len(part):3} games, favourite won "
                  f"{100 * sum(ok for _, ok in part) / len(part):5.1f}% (said {100 * sum(q for q, _ in part) / len(part):4.1f}%)")
    picks_home = [(p, y) for g, f, y, p in rows if p >= 0.5]
    picks_away = [(p, y) for g, f, y, p in rows if p < 0.5]
    print(f"Picked the home team {len(picks_home)} times (right {100 * sum(y for _, y in picks_home) / max(1, len(picks_home)):.1f}%), "
          f"the away team {len(picks_away)} times (right {100 * sum(1 - y for _, y in picks_away) / max(1, len(picks_away)):.1f}%)")
    misses = [(abs(p - 0.5), g, f, y, p) for g, f, y, p in rows if (p >= 0.5) != (y == 1)]
    close = sum(1 for _, g, *_ in misses if abs(g["hs"] - g["as"]) <= 5)
    print(f"Wrong picks: {len(misses)}; {close} of them lost by 5 points or less")
    print("Most confident misses:")
    for _, g, f, y, p in sorted(misses, key=lambda m: -m[0])[:8]:
        fav = g["home"] if p >= 0.5 else g["away"]
        why = ", ".join(f"{k} {w.get(k, 0) * v:+.2f}" for k, v in f.items() if k in w and abs(w[k] * v) > 0.15)
        print(f"  {g['date'][:10]} r{g['round']:>2} {g['home']} {g['hs']}-{g['as']} {g['away']}: picked {fav} "
              f"({100 * max(p, 1 - p):.0f}%)  [{why}]")


def save_model(data, params, chosen, test_score):
    """Refit on every season and store the settings, weights and end-of-season ratings."""
    rows, state = walk(data, params)
    b, w = fit([(f, y) for _, _, f, y in rows], chosen)
    last = SEASONS[-1]
    model = {
        "trained": f"{SEASONS[0]}-{last}", **{k: params[k] for k in DEFAULTS if k not in ("bias", "weights")},
        "backtest": {"season": TEST, "accuracy": test_score["acc"], "logloss": test_score["logloss"],
                     "games": test_score["n"]},
        "bias": round(b, 4), "weights": {k: round(v, 4) for k, v in w.items()},
        "ratings": {"after": last, "teams": {code: round(t.rating, 1) for code, t in sorted(state.teams.items())}},
        # what the next season's offense / defense numbers start from (see State.start_season)
        "priors": {code: {"off": round(state.rating100(t, "off"), 2), "deff": round(state.rating100(t, "deff"), 2),
                          "home_edge": round(state.home_edge(t), 2)}
                   for code, t in sorted(state.teams.items()) if t.games},
    }
    predict.MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    predict.MODEL_FILE.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")
    print(f"\nsaved {predict.MODEL_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
