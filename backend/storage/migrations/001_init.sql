-- History index, version 1. Every row is derived from files in data/ (source_file says
-- which one), so the whole database can be rebuilt at any time: python3 -m backend.storage rebuild

CREATE TABLE ingested (
    path TEXT PRIMARY KEY,
    sha TEXT NOT NULL,
    rows INTEGER NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE leagues (
    league_id TEXT PRIMARY KEY,
    title TEXT,
    format TEXT,                      -- head_to_head | classic
    competition_id TEXT,
    season INTEGER,
    source_file TEXT
);

CREATE TABLE teams (                  -- fantasy teams (managers)
    league_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    title TEXT,
    owner TEXT,
    PRIMARY KEY (league_id, team_id)
);

CREATE TABLE players (                -- latest known name / position of every player seen
    player_id TEXT PRIMARY KEY,
    bn_id TEXT,
    name TEXT,
    position TEXT
);

CREATE TABLE player_rounds (          -- a player's round: fantasy points and box score
    season INTEGER NOT NULL,
    competition_id TEXT NOT NULL,
    round INTEGER NOT NULL,           -- 0-based, as in the code
    point_calc TEXT NOT NULL,
    player_id TEXT NOT NULL,
    club TEXT,
    fp REAL,
    played INTEGER,
    min REAL, pts INTEGER, reb INTEGER, oreb INTEGER, dreb INTEGER, ast INTEGER, stl INTEGER,
    blk INTEGER, tov INTEGER, pf INTEGER, eff INTEGER, p2m INTEGER, p2a INTEGER, p3m INTEGER,
    p3a INTEGER, ftm INTEGER, fta INTEGER, usg REAL,
    source_file TEXT NOT NULL,
    PRIMARY KEY (season, competition_id, round, point_calc, player_id)
);

CREATE TABLE games (                  -- real EuroLeague games
    season INTEGER NOT NULL,
    competition_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    at TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    completed INTEGER,
    canceled INTEGER,
    source_file TEXT NOT NULL,
    PRIMARY KEY (season, competition_id, round, at, home, away)
);

CREATE TABLE advanced_rounds (        -- BasketNews advanced stats of a round, one JSON per player
    season INTEGER NOT NULL,
    competition_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    bn_player_id TEXT NOT NULL,
    metrics TEXT NOT NULL,
    source_file TEXT NOT NULL,
    PRIMARY KEY (season, competition_id, round, bn_player_id)
);

CREATE TABLE standings (              -- a team's result and table place after a round
    league_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    position INTEGER,
    points_round REAL,
    points_total REAL,
    wins INTEGER, losses INTEGER, ties INTEGER,
    round_position INTEGER,
    source_file TEXT NOT NULL,
    PRIMARY KEY (league_id, round, team_id)
);

CREATE TABLE matchups (               -- head-to-head games
    league_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    match_id TEXT NOT NULL,
    team1_id TEXT,
    team2_id TEXT,
    score1 REAL,
    score2 REAL,
    source_file TEXT NOT NULL,
    PRIMARY KEY (league_id, match_id)
);

CREATE TABLE transfers (              -- processed signings and trades
    league_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    kind TEXT NOT NULL,               -- trade | free_agent
    at TEXT,
    offer_team_id TEXT,
    request_team_id TEXT,
    offer_credits REAL,
    request_credits REAL,
    source_file TEXT NOT NULL,
    PRIMARY KEY (league_id, transfer_id)
);

CREATE TABLE transfer_players (
    league_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    side TEXT NOT NULL,               -- offer | request
    player_id TEXT NOT NULL,
    name TEXT,
    source_file TEXT NOT NULL
);

CREATE TABLE draft_picks (
    league_id TEXT NOT NULL,
    pick_no INTEGER NOT NULL,         -- 1-based overall pick
    team_id TEXT,
    player_id TEXT,
    source_file TEXT NOT NULL,
    PRIMARY KEY (league_id, pick_no)
);

CREATE TABLE lineups (                -- a team's saved lineup for a round
    league_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    saved_at TEXT,
    locked INTEGER,
    source TEXT,                      -- NULL, or "import" for lineups entered by hand
    formation TEXT,
    source_file TEXT NOT NULL,
    PRIMARY KEY (league_id, round, team_id)
);

CREATE TABLE lineup_slots (
    league_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    card TEXT,                        -- g-1 / f-2 / c-1 court, b-1..b-5 bench, i-* inactive
    slot TEXT,                        -- starter | bench | inactive
    captain INTEGER,
    source_file TEXT NOT NULL
);

CREATE TABLE injury_episodes (
    bn_id TEXT NOT NULL,
    episode_no INTEGER NOT NULL,
    name TEXT,
    club TEXT,
    start TEXT,
    end TEXT,                         -- NULL while ongoing
    last_seen TEXT,
    source_file TEXT NOT NULL,
    PRIMARY KEY (bn_id, episode_no)
);

CREATE TABLE injury_updates (
    bn_id TEXT NOT NULL,
    episode_no INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    date TEXT,
    at TEXT,
    status TEXT,
    return_text TEXT,
    comment TEXT,
    source_file TEXT NOT NULL,
    PRIMARY KEY (bn_id, episode_no, seq)
);

CREATE INDEX player_rounds_player ON player_rounds (player_id, round);
CREATE INDEX lineup_slots_player ON lineup_slots (player_id, league_id, round);
CREATE INDEX lineup_slots_team ON lineup_slots (league_id, round, team_id);
CREATE INDEX transfer_players_player ON transfer_players (player_id);

-- Who held a player in each round (from the saved lineups).
CREATE VIEW ownership AS
    SELECT league_id, round, team_id, player_id, card, slot, captain FROM lineup_slots;

-- Every team's points and place per round, with the team name.
CREATE VIEW manager_rounds AS
    SELECT s.league_id, s.round, s.team_id, t.title, t.owner, s.points_round, s.points_total, s.position,
           s.wins, s.losses, s.ties
    FROM standings s LEFT JOIN teams t ON t.league_id = s.league_id AND t.team_id = s.team_id;
