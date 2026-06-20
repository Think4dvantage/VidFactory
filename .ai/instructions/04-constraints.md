# Constraints — What NOT to Do

## AI Files

**All AI-related content lives exclusively in `.ai/`.** Never create tool-specific instruction files such as `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`, or any equivalent — not even as thin pointers. Instructions, context, prompts, and plans all go in `.ai/` and nowhere else.

---

## Production

**Never touch prod directly.** All production changes go through the IaC repo. No direct SSH, no direct `docker-compose` on the prod host.

---

## Frontend

**Never add npm or a build step.** The frontend is intentionally dependency-free. No webpack, vite, rollup, parcel, or any bundler. No `package.json`.

---

## Secrets

**Never commit secrets.** `config.yml` and `.env` are gitignored. Only `config.yml.example` (with placeholder values) is committed.

---

## Database Migrations

**Never skip creating a migration script** when adding tables or columns. SQLAlchemy's `create_all` does not handle schema drift — all changes require a new `.sql` file in `src/vidfactory/database/migrations/` with a sequential prefix (e.g., `0002_add_widgets.sql`). Migrations are tracked via the `_migrations` table in SQLite. Always make SQL statements idempotent using `IF NOT EXISTS` where possible.

---

## Code Quality

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't use feature flags or backwards-compatibility shims when you can just change the code.

---

## Architecture

- No Alembic — schema migrations are sequential idempotent `.sql` files in `database/migrations/`, applied and tracked by `db.py:init_db()` (see Database Migrations above).
- No print statements in production code — use the standard `logging` module.
- Never read `os.environ` directly — always go through `get_config()`.
- Never put all routes in `main.py` — one router per domain.
