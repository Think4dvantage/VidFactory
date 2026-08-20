"""VidFactory's own external integration contract -- API-key authenticated.

Mirrors the shape of Flightlog's /api/integration/v1 that this app itself calls
(core/flightlog_client.py), so an external tool (the YouTube-management pipeline) can pull
project data and push back a published-video link without a browser session, using a per-user
API key generated on /account (core/auth.generate_api_key). Reuses the exact same handlers as the
session-authed routes in api/routers/youtube.py -- only the auth dependency differs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vidfactory.api.auth_deps import require_api_user
from vidfactory.api.routers.youtube import FlightlogLinkIn, get_youtube_metadata, push_flightlog_link
from vidfactory.core import projects
from vidfactory.database.db import get_db
from vidfactory.database.models import User
from vidfactory.models.youtube import ProjectYoutubeMetadata

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/integration/v1", tags=["integration"], dependencies=[Depends(require_api_user)]
)


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(require_api_user)):
    rows = projects.list_projects(db, user.id)
    logger.info("[VF:integration] %s GET /projects -> %d project(s)", user.username, len(rows))
    return {
        "data": [
            {
                "project_id": p.id,
                "date": p.date.isoformat() if p.date else None,
                "flight_type": p.flight_type,
                "external_flight_id": p.external_flight_id,
                "has_full_flight": bool(p.full_flight_file),
                "has_summary": bool(p.summary_file),
                "has_fullflight_music": bool(p.fullflight_music_file),
            }
            for p in rows
        ],
        "total": len(rows),
    }


@router.get("/projects/{project_id}/youtube-metadata", response_model=ProjectYoutubeMetadata)
def get_youtube_metadata_ep(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(require_api_user)
):
    logger.info(
        "[VF:integration] %s GET /projects/%s/youtube-metadata", user.username, project_id
    )
    return get_youtube_metadata(project_id, db, user)


@router.post("/projects/{project_id}/flightlog-link")
def push_flightlog_link_ep(
    project_id: int,
    payload: FlightlogLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    logger.info(
        "[VF:integration] %s POST /projects/%s/flightlog-link", user.username, project_id
    )
    return push_flightlog_link(project_id, payload, db, user)
