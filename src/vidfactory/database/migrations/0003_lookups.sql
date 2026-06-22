-- Managed dropdown values for the outing form (category / glider / harness / launch_type).
CREATE TABLE IF NOT EXISTS lookups (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(kind, value)
);

-- Normalise the legacy launch-type codes to readable words.
UPDATE outings SET launch_type = 'forward' WHERE launch_type IN ('f', 'F');
UPDATE outings SET launch_type = 'reverse' WHERE launch_type IN ('r', 'R');

-- Seed the dropdowns from the values already present in the flight log.
INSERT OR IGNORE INTO lookups (kind, value)
    SELECT 'category', category FROM outings WHERE category IS NOT NULL AND category <> '';
INSERT OR IGNORE INTO lookups (kind, value)
    SELECT 'glider', glider FROM outings WHERE glider IS NOT NULL AND glider <> '';
INSERT OR IGNORE INTO lookups (kind, value)
    SELECT 'harness', harness FROM outings WHERE harness IS NOT NULL AND harness <> '';
INSERT OR IGNORE INTO lookups (kind, value) VALUES ('launch_type', 'forward');
INSERT OR IGNORE INTO lookups (kind, value) VALUES ('launch_type', 'reverse');
