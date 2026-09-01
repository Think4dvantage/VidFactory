-- Manual pilot metadata for flights not tracked in Flightlog (e.g. footage handed over by someone
-- else). A non-null pilot_name flags the project as flown by someone other than the owning user.
ALTER TABLE projects ADD COLUMN pilot_name TEXT;
