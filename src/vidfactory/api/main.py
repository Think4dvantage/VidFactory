from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from vidfactory import __version__
from vidfactory.api.routers import browser, sse
from vidfactory.api.templating import templates
from vidfactory.config import get_config
from vidfactory.core import filebrowser, gpu_detector
from vidfactory.core.jobs import registry
from vidfactory.database.db import get_engine, init_db

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
app.include_router(browser.router)
app.include_router(sse.router)


@app.get("/", include_in_schema=False)
def index(request: Request):
    cfg = get_config()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "encoder": getattr(app.state, "encoder", "unknown"),
            "mounts": filebrowser.check_mounts(),
            "roots": cfg.mount_roots(),
            "jobs": [j.public() for j in registry.list()],
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

    healthy = checks["sqlite"] == "ok" and all(v == "ok" for v in mounts.values())
    body = {
        "status": "ok" if healthy else "degraded",
        "service": "vidfactory",
        "version": __version__,
        "uptime_seconds": int(time.time() - getattr(app.state, "start_time", time.time())),
        "checks": checks,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)
