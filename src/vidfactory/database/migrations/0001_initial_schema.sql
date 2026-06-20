-- 0001 initial schema. Column names must match database/models.py.

CREATE TABLE IF NOT EXISTS sites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- 'launch' | 'landing'
    elevation_m INTEGER
);

CREATE TABLE IF NOT EXISTS outings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE NOT NULL,
    launch_site_id  INTEGER REFERENCES sites(id),
    landing_site_id INTEGER REFERENCES sites(id),
    glider          TEXT,
    harness         TEXT,
    flight_time_min INTEGER,
    distance_km     REAL,
    max_alt_m       INTEGER,
    category        TEXT,
    launch_type     TEXT,
    comment         TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    outing_id             INTEGER UNIQUE REFERENCES outings(id),
    flight_type           TEXT NOT NULL DEFAULT 'normal_flight',
    full_flight_file      TEXT,
    summary_file          TEXT,
    fullflight_music_file TEXT,
    youtube_metadata_file TEXT,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_parts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    file       TEXT NOT NULL,
    part_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hikes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL UNIQUE REFERENCES projects(id),
    sources      TEXT NOT NULL DEFAULT '[]',   -- JSON list of file paths
    speed_factor REAL NOT NULL DEFAULT 32.0
);

CREATE TABLE IF NOT EXISTS highlights (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id),
    name           TEXT NOT NULL,
    start_s        REAL NOT NULL,
    end_s          REAL NOT NULL,
    comment        TEXT,
    type           TEXT NOT NULL DEFAULT 'video',   -- 'video' | 'picture'
    role           TEXT NOT NULL DEFAULT 'normal',  -- 'normal' | 'launch' | 'landing'
    image_path     TEXT,
    duration       REAL,
    use_in_summary INTEGER NOT NULL DEFAULT 1,
    make_short     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pools (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind       TEXT NOT NULL,           -- 'flying' | 'hiking'
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shorts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    output_file         TEXT NOT NULL,
    short_type          TEXT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration            REAL,
    segments_used       TEXT NOT NULL DEFAULT '{}',   -- JSON
    source_highlight_id INTEGER REFERENCES highlights(id),
    title               TEXT
);

CREATE INDEX IF NOT EXISTS ix_outings_date ON outings(date);
CREATE INDEX IF NOT EXISTS ix_highlights_project ON highlights(project_id);
CREATE INDEX IF NOT EXISTS ix_pools_project ON pools(project_id);
CREATE INDEX IF NOT EXISTS ix_shorts_project ON shorts(project_id);
