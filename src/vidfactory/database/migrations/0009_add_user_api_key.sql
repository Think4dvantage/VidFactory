-- Per-user VidFactory API key, for external tools (e.g. the YouTube-management pipeline) to call
-- /api/integration/v1 without a browser session. Plaintext for the same reason as
-- flightlog_api_key: a bearer credential presented verbatim by the caller, nothing to hash it
-- against on receipt. SQLite treats each NULL as distinct in a unique index, so users who never
-- generate a key are unaffected.

ALTER TABLE users ADD COLUMN api_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_api_key ON users(api_key);
