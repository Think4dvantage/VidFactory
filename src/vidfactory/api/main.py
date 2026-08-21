from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from vidfactory import __version__
from vidfactory.api.auth_deps import require_user
from vidfactory.api.routers import auth, browser, integration, projects, sse, youtube
from vidfactory.api.templating import templates
from vidfactory.config import get_config
from vidfactory.core import auth as auth_core
from vidfactory.core import filebrowser, gpu_detector
from vidfactory.core import projects as projects_core
from vidfactory.core.jobs import registry
from vidfactory.database.db import get_db, get_engine, init_db
from vidfactory.database.models import User

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    logging.basicConfig(
        level=getattr(logging, cfg.app.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    app.state.start_time = time.time()

    logger.info("VidFactory starting — version %s", __version__)
    logger.info("Config loaded: log_level=%s db=%s", cfg.app.log_level, cfg.db_path)

    init_db()
    with Session(get_engine()) as db:
        auth_core.ensure_bootstrap_user(db)

    mounts = filebrowser.check_mounts()
    for name, status in mounts.items():
        level = logging.INFO if status == "ok" else logging.WARNING
        logger.log(level, "Mount %-18s %s (%s)", name, status, cfg.mount_roots()[name])

    encoder = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
    app.state.encoder = encoder.codec

    logger.info("HTTP server ready")
    yield
    logger.info("VidFactory shutting down")


app = FastAPI(title="VidFactory", version=__version__, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth.router)
app.include_router(browser.router)
app.include_router(integration.router)
app.include_router(projects.router)
app.include_router(sse.router)
app.include_router(youtube.router)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    # require_user() raises HTTPException(detail={"error": {...}}) for /api/* 401s, and
    # detail=None + a Location header for page-route redirects — flatten the former to the
    # project's existing error-envelope shape instead of Starlette's default {"detail": ...} wrap.
    content = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.get("/", include_in_schema=False)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    cfg = get_config()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "encoder": getattr(app.state, "encoder", "unknown"),
            "mounts": filebrowser.check_mounts(),
            "roots": cfg.mount_roots(),
            "jobs": [j.public() for j in registry.list(user.id)],
            "projects": projects_core.list_projects(db, user.id),
        },
    )


@app.get("/health")
def health():
    cfg = get_config()
    checks: dict = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["sqlite"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["sqlite"] = f"error: {exc}"

    mounts = filebrowser.check_mounts()
    checks["mounts"] = mounts
    checks["encoder"] = getattr(app.state, "encoder", "unknown")
    checks["jobs_active"] = registry.active_count()
    checks["jobs_queued"] = registry.queued_count()

    # Liveness vs readiness: the container is "up" if the process + DB are serving (the flight
    # log works off SQLite alone). Missing NAS mounts are reported as "degraded" but must NOT mark
    # the container unhealthy — otherwise Traefik refuses to route and the UI (which surfaces the
    # mount problem) becomes unreachable. Only a DB failure returns 503.
    live = checks["sqlite"] == "ok"
    mounts_ok = all(v == "ok" for v in mounts.values())
    status = "ok" if (live and mounts_ok) else ("degraded" if live else "down")
    body = {
        "status": status,
        "service": "vidfactory",
        "version": __version__,
        "uptime_seconds": int(time.time() - getattr(app.state, "start_time", time.time())),
        "checks": checks,
    }
    return JSONResponse(status_code=200 if live else 503, content=body)
