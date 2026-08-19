# Testing Conventions

## Philosophy

Testing is mandatory for any project built with this blueprint. It ensures that the AI-assisted development process remains reliable and that new changes don't introduce regressions.

---

## Backend: Pytest

Use `pytest` for all backend tests. (`pytest-asyncio` is a dependency but unused so far — every
route under test is sync; reach for it only if that changes.)

### Location
All backend tests live in `tests/backend/`. Shared fixtures live in `tests/backend/conftest.py`
(`db_session`: in-memory SQLite via `sqlalchemy.pool.StaticPool`; `client`: `TestClient` with
`get_db` overridden to `db_session`; `user`: a persisted `User`; `auth_client`: `client` with
`require_user` overridden to `user`, for tests that aren't testing auth itself). Add new shared
fixtures there rather than duplicating them per-file.

### Standards
- **Naming**: `test_*.py`
- **API Testing**: `fastapi.testclient.TestClient` (sync — it wraps `httpx` internally), via the
  `client`/`auth_client` fixtures above. Use `app.dependency_overrides` for anything not already
  covered by a fixture (see `test_flightlog_hints.py` for `monkeypatch`-ing `flightlog_client`
  calls instead, when the thing under test isn't a FastAPI dependency).
- **Database**: in-memory SQLite (`sqlite:///:memory:`) via the `db_session` fixture — schema
  created directly from the ORM (`Base.metadata.create_all`), not by running the `.sql` migrations.
- **Auth**: routes behind `require_user` need either the `auth_client` fixture (fast path) or a
  real cookie-based login (`client.post("/login", data={...})`) when the test is specifically about
  auth/session/cross-user behavior — see `test_auth.py` for both patterns.

### Example API Test

```python
def test_youtube_metadata_404(auth_client):
    resp = auth_client.get("/api/projects/9999/youtube-metadata")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ENTITY_NOT_FOUND"
```

---

## Frontend: Playwright

Because this project uses a "no-build" server-rendered frontend (Jinja + HTMX), we use `Playwright` for End-to-End (E2E) testing. This is the most reliable way to test that the UI behaves correctly in real browsers — especially the highlight editor and live job-progress flows.

### Location
All frontend tests live in `tests/frontend/`.

### Standards
- **Naming**: `test_*.py` (using Playwright's Python library).
- **Setup**: Playwright should point to the dev instance or a local test server.
- **Interactions**: Use standard Playwright selectors (e.g., `page.get_by_text()`, `page.get_by_role()`).

### Example E2E Test

```python
import pytest
from playwright.sync_api import Page, expect

def test_login_page_renders(page: Page):
    page.goto("http://localhost:8000/login")
    expect(page.get_by_text("Username")).to_be_visible()
    expect(page.locator("button")).to_be_visible()
```

---

## CI / Automation

- All tests should run automatically on every Pull Request via GitHub Actions.
- Ensure the test suite is "green" before merging any new feature or fix.
- **Coverage**: Aim for 80%+ coverage, but prioritize critical paths (highlight dedup/auto-fill, FFmpeg arg construction, `UsedMap` short de-dup, auth/ownership scoping, Flightlog client error-envelope handling, API contracts).
