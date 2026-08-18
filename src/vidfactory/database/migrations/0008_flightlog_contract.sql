-- The Flightlog integration API's flight id is a string (its own docs: "id": "string", a UUID-
-- like value), not the INTEGER this column was declared as before that contract existed. No
-- project has ever had this field set (core/flightlog_client.py was a NotImplementedError stub
-- until now), so this is a type fix with zero real data at risk, not a backfill.

ALTER TABLE projects RENAME COLUMN external_flight_id TO external_flight_id_old_int;
ALTER TABLE projects ADD COLUMN external_flight_id TEXT;
UPDATE projects SET external_flight_id = CAST(external_flight_id_old_int AS TEXT)
    WHERE external_flight_id_old_int IS NOT NULL;
ALTER TABLE projects DROP COLUMN external_flight_id_old_int;
