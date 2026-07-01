-- ACC/Ivy Predictor schema. SQLite-compatible (works on Postgres with minor
-- type tweaks). Every fact row carries source_url so outputs can cite TFRRS.
-- is_synthetic flags demo rows so they can never masquerade as real data.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conference (
    conf_id      TEXT PRIMARY KEY,          -- 'acc', 'ivy'
    display_name TEXT NOT NULL,
    league_id    INTEGER NOT NULL           -- TFRRS /leagues/{id}.html
);

CREATE TABLE IF NOT EXISTS team (
    team_id    INTEGER PRIMARY KEY,
    conf_id    TEXT REFERENCES conference(conf_id),
    name       TEXT NOT NULL,
    gender     TEXT NOT NULL CHECK (gender IN ('m','f')),
    tfrrs_slug TEXT,                         -- team-page slug
    source_url TEXT,
    UNIQUE (name, gender)
);

CREATE TABLE IF NOT EXISTS athlete (
    athlete_id     INTEGER PRIMARY KEY,
    tfrrs_athlete_id INTEGER,               -- from /athletes/{id} when available
    name           TEXT NOT NULL,
    team_id        INTEGER REFERENCES team(team_id),
    grad_year      INTEGER,                 -- class year if parseable
    source_url     TEXT,
    UNIQUE (tfrrs_athlete_id)
);

CREATE TABLE IF NOT EXISTS meet (
    meet_id     INTEGER PRIMARY KEY,
    tfrrs_meet_id INTEGER UNIQUE,           -- the {id} in /results/{id}/...
    name        TEXT NOT NULL,
    sport       TEXT NOT NULL CHECK (sport IN ('indoor','outdoor','xc')),
    season_year INTEGER NOT NULL,
    start_date  TEXT,
    is_conf_champ INTEGER NOT NULL DEFAULT 0,
    conf_id     TEXT REFERENCES conference(conf_id),
    source_url  TEXT
);

-- One row per athlete-performance in a meet (the atomic scraped fact).
CREATE TABLE IF NOT EXISTS performance (
    perf_id     INTEGER PRIMARY KEY,
    meet_id     INTEGER REFERENCES meet(meet_id),
    athlete_id  INTEGER REFERENCES athlete(athlete_id),
    team_id     INTEGER REFERENCES team(team_id),
    event_raw   TEXT NOT NULL,              -- raw TFRRS event label
    event_norm  TEXT NOT NULL,              -- normalized (see event_groups)
    event_group TEXT NOT NULL,              -- sprints/distance/jumps/throws/multis
    gender      TEXT NOT NULL CHECK (gender IN ('m','f')),
    mark_raw    TEXT,                        -- '13.42', '1:48.55', '7.65m'
    mark_seconds REAL,                       -- track events -> seconds
    mark_metric  REAL,                       -- field events -> meters, multis->points
    is_field    INTEGER NOT NULL DEFAULT 0,
    wind        REAL,
    place       INTEGER,                     -- finishing place at this meet
    points      REAL DEFAULT 0,              -- points scored at this meet (observed)
    round       TEXT,                        -- 'prelim'/'final'/'section'
    season_year_idx INTEGER,                  -- denormalized from meet for fast filtering
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    source_url  TEXT
);

-- Denormalized team score per championship meet (also derivable from performance).
CREATE TABLE IF NOT EXISTS team_meet_score (
    meet_id   INTEGER REFERENCES meet(meet_id),
    team_id   INTEGER REFERENCES team(team_id),
    gender    TEXT NOT NULL CHECK (gender IN ('m','f')),
    place     INTEGER,
    points    REAL,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    PRIMARY KEY (meet_id, team_id, gender)
);

-- Season-best "seed" mark per athlete/event going INTO a championship — used to
-- measure over/under-performance vs seed and to feed the predictor.
CREATE TABLE IF NOT EXISTS season_best (
    athlete_id  INTEGER REFERENCES athlete(athlete_id),
    team_id     INTEGER REFERENCES team(team_id),
    season_year INTEGER NOT NULL,
    sport       TEXT NOT NULL,
    event_norm  TEXT NOT NULL,
    event_group TEXT NOT NULL,
    gender      TEXT NOT NULL,
    mark_seconds REAL,
    mark_metric  REAL,
    class_year   TEXT,                       -- FR/SO/JR/SR/GR if known
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    source_url   TEXT,
    PRIMARY KEY (athlete_id, season_year, sport, event_norm)
);

-- Roster status flags for recruiting-class-impact analysis.
CREATE TABLE IF NOT EXISTS roster_status (
    athlete_id  INTEGER REFERENCES athlete(athlete_id),
    season_year INTEGER NOT NULL,
    class_year  TEXT,                        -- FR/SO/JR/SR/GR
    is_freshman INTEGER DEFAULT 0,
    is_transfer INTEGER DEFAULT 0,
    is_redshirt INTEGER DEFAULT 0,
    PRIMARY KEY (athlete_id, season_year)
);

CREATE INDEX IF NOT EXISTS ix_perf_meet   ON performance(meet_id);
CREATE INDEX IF NOT EXISTS ix_perf_team   ON performance(team_id, event_group);
CREATE INDEX IF NOT EXISTS ix_perf_evt    ON performance(event_norm, gender, season_year_idx);
CREATE INDEX IF NOT EXISTS ix_sb_team     ON season_best(team_id, season_year, gender);
CREATE INDEX IF NOT EXISTS ix_meet_champ  ON meet(conf_id, sport, season_year, is_conf_champ);
