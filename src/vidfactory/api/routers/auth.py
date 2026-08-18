from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from vidfactory.api.auth_deps import get_current_user, require_user
from vidfactory.api.templating import templates
from vidfactory.core import auth
from vidfactory.database.db import get_db
from vidfactory.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", include_in_schema=False)
def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", include_in_schema=False)
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = str(form.get("username") or "")
    password = str(form.get("password") or "")
    user = auth.authenticate(db, username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password."}, status_code=401
        )
    token = auth.create_session(db, user)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return resp


@router.post("/logout", include_in_schema=False)
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        auth.delete_session(db, token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@router.get("/account", include_in_schema=False)
def account_page(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(
        request, "account.html", {"flightlog_key_set": bool(user.flightlog_api_key)}
    )


@router.post("/api/account/flightlog-key", include_in_schema=False)
async def set_flightlog_key(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    form = await request.form()
    api_key = str(form.get("api_key") or "").strip()
    user.flightlog_api_key = api_key or None
    db.commit()
    return Response(status_code=204, headers={"HX-Redirect": "/account"})
