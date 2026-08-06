-- Project no longer requires an Outing (flight-log moved to a separate project/service).
-- `outing_id` is left in place untouched (existing rows keep it) but is no longer mapped by the
-- ORM. These two new columns replace what the app reads/writes going forward.
ALTER TABLE projects ADD COLUMN date DATE;
ALTER TABLE projects ADD COLUMN external_flight_id INTEGER;
