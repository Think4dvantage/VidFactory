-- Aggregate stats derived from an outing's IGC track (one per outing).
CREATE TABLE IF NOT EXISTS igc_tracks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    outing_id        INTEGER NOT NULL UNIQUE REFERENCES outings(id) ON DELETE CASCADE,
    file             TEXT NOT NULL,                 -- path under the igc root
    analyzed_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    takeoff_at       DATETIME,
    landing_at       DATETIME,
    duration_s       INTEGER,
    max_alt_m        INTEGER,
    thermal_count    INTEGER NOT NULL DEFAULT 0,
    total_climb_m    INTEGER NOT NULL DEFAULT 0,
    best_climb_ms    REAL,
    avg_climb_ms     REAL,
    glide_count      INTEGER NOT NULL DEFAULT 0,
    total_glide_km   REAL,
    glide_ratio      REAL
);

CREATE INDEX IF NOT EXISTS ix_igc_tracks_outing ON igc_tracks(outing_id);
