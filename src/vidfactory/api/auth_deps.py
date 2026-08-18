"""Resolves the session cookie into a `User`, and gates routes behind it.

`get_current_user` is a normal FastAPI dependency (via `Depends(get_db)`) rather than middleware,
so it goes through `app.dependency_overrides` like everything else — tests can swap the DB session
without a separate code path. FastAPI caches a dependency's result per request by default, so
`require_user`'s internal `Depends(get_current_user)` and any other route that also declares it
still only run one DB query per request.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from vidfactory.core.auth import SESSION_COOKIE, get_user_for_token
from vidfactory.database.db import get_db
from vidfactory.database.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user = None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user = get_user_for_token(db, token)
    request.state.user = user  # so templates can read it without threading it through context
    return user


def require_user(request: Request, user: User | None = Depends(get_current_user)) -> User:
    if user is not None:
        return user
    if request.url.path.startswith("/api/"):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "AUTH_REQUIRED", "message": "Login required.", "details": {}}},
        )
    # Starlette's default HTTPException handler forwards `headers`, so a 3xx + Location here
    # redirects the browser even though the handler otherwise emits a JSON body.
    raise HTTPException(status_code=303, headers={"Location": "/login"})
