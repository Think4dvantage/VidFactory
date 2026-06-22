-- Flight buddies (multiselect per outing) + Hike & Fly metrics.

CREATE TABLE IF NOT EXISTS buddies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outing_buddies (
    outing_id INTEGER NOT NULL REFERENCES outings(id) ON DELETE CASCADE,
    buddy_id  INTEGER NOT NULL REFERENCES buddies(id) ON DELETE CASCADE,
    PRIMARY KEY (outing_id, buddy_id)
);

ALTER TABLE outings ADD COLUMN climb_m INTEGER;
ALTER TABLE outings ADD COLUMN hike_distance_km REAL;
ALTER TABLE outings ADD COLUMN hike_duration_min INTEGER;

-- Seed the buddy roster the user named.
INSERT OR IGNORE INTO buddies (name) VALUES ('Tom'), ('Ueli'), ('Päsci'), ('Simon'), ('Johannes');
