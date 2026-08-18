-- Multi-user accounts: opaque session-token auth, no signup UI (bootstrap user created by
-- core/auth.ensure_bootstrap_user from VF_BOOTSTRAP_USERNAME/VF_BOOTSTRAP_PASSWORD at startup,
-- additional users via scripts/create_user.py). Projects gain an owner.

CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    username           TEXT NOT NULL UNIQUE,
    password_hash      TEXT NOT NULL,
    flightlog_api_key  TEXT,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

ALTER TABLE projects ADD COLUMN owner_id INTEGER REFERENCES users(id);
