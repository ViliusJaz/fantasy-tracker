"""Who should win a EuroLeague game: a small model fed only with what was known before tip-off.

The same code runs the backtests (tools/predict_research.py, on past seasons) and the game
previews on the site, so the accuracy measured there is the accuracy you get here.

A game: {"id", "season", "phase" (RS / PI / PO / FF), "round", "date" (UTC ISO), "home", "away"
(club codes), "neutral", "hs", "as" (scores; None before the game), "box": {code: team totals} or
None, "players": {code: [{"id", "min", "pir"}]} or None}.

Walk the games in date order: features() before a game, update() after it. The prediction is
a logistic model over these features (home minus away where it applies):

  elo        rating difference: an Elo rating updated after every game by the margin
  home       1 unless the venue is neutral (Final Four)
  form       how much better than its rating a team played in its last games
  rest       days since the last game (capped), and a flag for two games in three days
  absent     importance (PIR per game) of regular rotation players missing from the game
  matchup    expected margin from each side's offense against the other side's defense
             (points per 100 possessions), which is how "what kind of team beats whom" enters
  vs_type    how a team did, beyond its rating, against opponents of today's opponent's tier
  home_edge  how much better than its rating the home team plays at home than away
  streak     current run of wins (+) or losses (-)
  crowd      the home team's usual attendance
  roster     strength of who actually plays: PIR per game of the eight best players available
  recent     net rating (points per 100 possessions) over the last few games only
  travel     how far the away team came
  srs        strength ratings fitted to every margin this season (ridge regression: newer games
             weigh more, each opponent's strength is accounted for, last season is the prior)
The weights and Elo settings are fitted on past seasons and stored in backend/model/euroleague.json.
"""
import json
import math
from datetime import datetime
from pathlib import Path

MODEL_FILE = Path(__file__).resolve().parent / "model" / "euroleague.json"
FEATURES = ("elo", "home", "form", "rest", "b2b", "absent", "matchup", "vs_type", "home_edge", "streak", "crowd",
            "roster", "recent", "travel", "srs")
# Where the clubs play (latitude, longitude), for the travel distance of the away team.
CITIES = {
    "ASV": (45.77, 4.88), "BAR": (41.38, 2.12), "BAS": (42.85, -2.67), "BER": (52.52, 13.40), "CSK": (55.75, 37.62),
    "DUB": (25.20, 55.27), "DYR": (59.93, 30.34), "HTA": (32.08, 34.78), "IST": (41.01, 28.98), "MAD": (40.42, -3.70),
    "MCO": (43.73, 7.42), "MIL": (45.46, 9.19), "MUN": (48.14, 11.58), "OLY": (37.94, 23.65), "PAM": (39.47, -0.38),
    "PAN": (37.98, 23.73), "PAR": (44.79, 20.45), "PRS": (48.86, 2.35), "RED": (44.79, 20.45), "TEL": (32.08, 34.78),
    "ULK": (41.01, 28.98), "UNK": (55.80, 49.11), "VIR": (44.49, 11.34), "ZAL": (54.90, 23.90),
}


def distance_km(a, b):
    (la1, lo1), (la2, lo2) = CITIES[a], CITIES[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def load_params():
    try:
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _day(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def possessions(box):
    return box["fga2"] + box["fga3"] + 0.44 * box["fta"] - box["oreb"] + box["tov"]


class Team:
    def __init__(self, rating):
        self.rating = rating
        self.games = []          # [{date, opp, margin, resid, off, deff, poss, home, opp_tier}]
        self.players = {}        # player id -> [(date, minutes, pir)]
        self.prior = {}          # last season: off / deff per 100 possessions, home edge
        self.crowds = []         # attendance of its home games


class State:
    """Everything the model knows between games."""

    def __init__(self, params):
        self.p = params
        self.teams = {}
        self.season = None
        self.results = []        # this season: (date, home, away, margin, neutral)
        self.srs_prior = {}      # team -> rating carried over from last season
        self._srs_cache = (None, {})

    def team(self, code):
        if code not in self.teams:
            self.teams[code] = Team(self.p["elo_new_team"])
        return self.teams[code]

    def start_season(self, season):
        """Ratings regress toward the mean between seasons; this season's games start empty."""
        if self.season == season:
            return
        self.season = season
        if self.results:
            ratings = self.srs(None)
            keep = self.p.get("srs_carry", 0.5)
            self.srs_prior = {code: keep * r for code, r in ratings.items()}
        self.results = []
        self._srs_cache = (None, {})
        carry = self.p["elo_carry"]
        for t in self.teams.values():
            if t.games:
                t.prior = {"off": self.rating100(t, "off"), "deff": self.rating100(t, "deff"),
                           "home_edge": self.home_edge(t)}
            t.rating = 1500 + carry * (t.rating - 1500)
            t.games, t.players = [], {}
            t.crowds = t.crowds[-5:]  # last season's crowds say something until this season's come in

    # ------------------------------------------------------------------ features

    def tier(self, code):
        """0 / 1 / 2: bottom / middle / top third of the league by rating."""
        ranked = sorted(self.teams.values(), key=lambda t: t.rating)
        n = len(ranked)
        pos = ranked.index(self.teams[code]) if code in self.teams else n // 2
        return min(2, 3 * pos // max(n, 1))

    def league_ppp(self):
        pts = [g["off"] for t in self.teams.values() for g in t.games if g["off"] is not None]
        return sum(pts) / len(pts) if pts else 112.0

    def rating100(self, t, side):
        """Offensive / defensive points per 100 possessions this season, shrunk toward last
        season's number (or the league's, for a new team)."""
        vals = [g[side] for g in t.games if g[side] is not None]
        k = self.p["shrink_games"]
        prior = t.prior.get(side, self.league_ppp()) if self.p.get("prior_ratings", 1) else self.league_ppp()
        return (sum(vals) + k * prior) / (len(vals) + k)

    def home_edge(self, t):
        """Residual at home minus residual away (points), shrunk: how much its own court helps."""
        k = self.p["shrink_type"]
        home = [g["resid"] for g in t.games if g["home"]]
        away = [g["resid"] for g in t.games if not g["home"]]
        prior = t.prior.get("home_edge", 0.0) * 0.5
        return (sum(home) + k * prior) / (len(home) + k) - (sum(away) - k * prior) / (len(away) + k)

    def streak(self, t):
        run, kind = 0, None
        for g in reversed(t.games):
            won = g["margin"] > 0
            if kind is None:
                kind = won
            if won != kind:
                break
            run += 1
        return run if kind else -run

    def crowd(self, t):
        return sum(t.crowds[-8:]) / len(t.crowds[-8:]) if t.crowds else None

    def form(self, t):
        recent = [g["resid"] for g in t.games[-self.p["form_games"]:]]
        return sum(recent) / (len(recent) + 1) if recent else 0.0

    def rest(self, t, when):
        if not t.games:
            return 7.0, 0
        days = (when - t.games[-1]["date"]).total_seconds() / 86400
        return min(days, 7.0), int(days < 2.5)

    def vs_type(self, t, opp_tier):
        same = [g["resid"] for g in t.games if g["opp_tier"] == opp_tier]
        return sum(same) / (len(same) + self.p["shrink_type"]) if same else 0.0

    def absent(self, t, played_ids, when):
        """PIR per game of this team's current regulars (3+ of its last 5 games, 15+ minutes)
        who are not in this game: a fresh absence, not someone long gone."""
        if played_ids is None or len(t.games) < 3:
            return 0.0
        recent_dates = {g["date"] for g in t.games[-5:]}
        miss = 0.0
        for pid, hist in t.players.items():
            if pid in played_ids:
                continue
            recent = [h for h in hist if h[0] in recent_dates]
            if len(recent) >= 3 and sum(h[1] for h in recent) / len(recent) >= 15:
                miss += max(0.0, sum(h[2] for h in recent) / len(recent))
        return miss

    def srs(self, when):
        """Team ratings in points per game (home court fitted too): least squares over this
        season's margins weighted by recency, pulled toward last season's rating."""
        if self._srs_cache[0] == (when, len(self.results)):
            return self._srs_cache[1]
        codes = sorted(set(self.teams) | set(self.srs_prior))
        idx = {c: i for i, c in enumerate(codes)}
        n = len(codes) + 1  # + home court
        a = [[0.0] * n for _ in range(n)]
        b = [0.0] * n
        lam = self.p.get("srs_ridge", 3.0)
        half = self.p.get("srs_half_life", 60.0)
        for i, c in enumerate(codes):
            a[i][i] += lam
            b[i] += lam * self.srs_prior.get(c, 0.0)
        a[n - 1][n - 1] += 1.0
        b[n - 1] += 3.5  # home court prior (points)
        for date, home, away, margin, neutral in self.results:
            w = 0.5 ** (((when or date) - date).days / half) if when else 1.0
            x = {idx[home]: 1.0, idx[away]: -1.0}
            if not neutral:
                x[n - 1] = 1.0
            for i, xi in x.items():
                b[i] += w * xi * margin
                for j, xj in x.items():
                    a[i][j] += w * xi * xj
        sol = _solve(a, b)
        ratings = {c: sol[i] for c, i in idx.items()}
        ratings["_home"] = sol[n - 1]
        self._srs_cache = ((when, len(self.results)), ratings)
        return ratings

    def roster(self, t, played_ids):
        """Sum of this season's PIR per game of the eight best players taking part."""
        if played_ids is None:
            return None
        avgs = sorted((sum(h[2] for h in hist) / len(hist) for pid, hist in t.players.items()
                       if pid in played_ids and len(hist) >= 2), reverse=True)
        return sum(avgs[:8]) if avgs else None

    def recent(self, t):
        games = [g for g in t.games[-self.p.get("recent_games", 6):] if g["off"] is not None]
        k = 3
        season = self.rating100(t, "off") - self.rating100(t, "deff")
        return (sum(g["off"] - g["deff"] for g in games) + k * season) / (len(games) + k)

    def features(self, game, absent=None, roster=None):
        """Pre-game features of `game`. `absent` = {code: PIR missing} and `roster` = {code: PIR
        of the eight best available} override the box score (the site passes what the injury
        report says)."""
        h, a = self.team(game["home"]), self.team(game["away"])
        when = _day(game["date"])
        rh, ra = self.rest(h, when), self.rest(a, when)
        if absent is None:
            played = game.get("players") or {}
            absent = {c: self.absent(self.team(c), {p["id"] for p in played[c] if p["min"] > 0} if c in played else None,
                                     when) for c in (game["home"], game["away"])}
        if roster is None:
            played = game.get("players") or {}
            roster = {c: self.roster(self.team(c), {p["id"] for p in played[c] if p["min"] > 0} if c in played else None)
                      for c in (game["home"], game["away"])}
        rs_h, rs_a = roster.get(game["home"]), roster.get(game["away"])
        travel = 0.0
        if not game.get("neutral") and game["home"] in CITIES and game["away"] in CITIES:
            travel = distance_km(game["away"], game["home"]) / 1000
        srs = self.srs(when)
        off_h, def_h = self.rating100(h, "off"), self.rating100(h, "deff")
        off_a, def_a = self.rating100(a, "off"), self.rating100(a, "deff")
        return {
            "elo": (h.rating - a.rating) / 100,
            "home": 0.0 if game.get("neutral") else 1.0,
            "form": (self.form(h) - self.form(a)) / 10,
            "rest": (rh[0] - ra[0]) / 3,
            "b2b": rh[1] - ra[1],
            "absent": (absent.get(game["home"], 0) - absent.get(game["away"], 0)) / 10,
            "matchup": ((off_h + def_a) / 2 - (off_a + def_h) / 2) / 10,
            "vs_type": (self.vs_type(h, self.tier(game["away"])) - self.vs_type(a, self.tier(game["home"]))) / 10,
            "home_edge": 0.0 if game.get("neutral") else self.home_edge(h) / 10,
            "streak": (self.streak(h) - self.streak(a)) / 5,
            "crowd": 0.0 if game.get("neutral") or self.crowd(h) is None else (self.crowd(h) - 8000) / 5000,
            "roster": (rs_h - rs_a) / 10 if rs_h is not None and rs_a is not None else 0.0,
            "recent": (self.recent(h) - self.recent(a)) / 10,
            "travel": travel,
            "srs": (srs.get(game["home"], 0.0) - srs.get(game["away"], 0.0)) / 10,
        }

    def probability(self, feats):
        w = self.p["weights"]
        z = self.p["bias"] + sum(w.get(k, 0.0) * v for k, v in feats.items())
        return 1 / (1 + math.exp(-max(-30, min(30, z))))

    # ------------------------------------------------------------------ after the game

    def update(self, game):
        h, a = self.team(game["home"]), self.team(game["away"])
        when = _day(game["date"])
        margin = game["hs"] - game["as"]
        hfa = 0 if game.get("neutral") else self.p["elo_home"]
        diff = h.rating + hfa - a.rating
        expected = 1 / (1 + 10 ** (-diff / 400))
        won = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
        mov = math.log(abs(margin) + 1) * 2.2 / (diff * (1 if margin > 0 else -1) * 0.001 + 2.2)
        change = self.p["elo_k"] * mov * (won - expected)
        exp_margin = diff / self.p["elo_points"]
        tiers = {game["home"]: self.tier(game["away"]), game["away"]: self.tier(game["home"])}
        box = game.get("box") or {}
        for t, code, sign, is_home in ((h, game["home"], 1, True), (a, game["away"], -1, False)):
            opp = game["away"] if is_home else game["home"]
            own, other = box.get(code), box.get(opp)
            poss = (possessions(own) + possessions(other)) / 2 if own and other else None
            t.games.append({
                "date": when, "opp": opp, "margin": sign * margin, "resid": sign * (margin - exp_margin),
                "off": 100 * own["pts"] / poss if poss else None, "deff": 100 * other["pts"] / poss if poss else None,
                "poss": poss, "home": is_home, "opp_tier": tiers[code],
            })
            if is_home and game.get("audience") and not game.get("neutral"):
                t.crowds.append(game["audience"])
            for pl in (game.get("players") or {}).get(code, []):
                if pl["min"] > 0:
                    t.players.setdefault(pl["id"], []).append((when, pl["min"], pl["pir"]))
        h.rating += change
        a.rating -= change
        self.results.append((when, game["home"], game["away"], margin, bool(game.get("neutral"))))


def _solve(a, b):
    """Gaussian elimination (small, well-conditioned systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def explain(feats, params, home, away):
    """The factors that moved the prediction most: [(feature, log-odds toward the home team)].
    Home court is the constant plus the home weight (it is what the model gives any home side)."""
    w = params["weights"]
    parts = [(k, w.get(k, 0.0) * v) for k, v in feats.items() if k != "home" and w.get(k)]
    if feats.get("home"):
        parts.append(("home", params["bias"] + w.get("home", 0.0)))
    return sorted(parts, key=lambda kv: -abs(kv[1]))
