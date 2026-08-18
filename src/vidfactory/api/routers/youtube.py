from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vidfactory.api.auth_deps import require_user
from vidfactory.config import get_config
from vidfactory.core import flightlog_client, projects, youtube_meta
from vidfactory.database.db import get_db
from vidfactory.database.models import User
from vidfactory.models.youtube import ProjectYoutubeMetadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["youtube"], dependencies=[Depends(require_user)])


def _not_found(project_id: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "ENTITY_NOT_FOUND",
                "message": f"Project {project_id} not found.",
                "details": {"project_id": project_id},
            }
        },
    )


@router.get("/{project_id}/youtube-metadata", response_model=ProjectYoutubeMetadata)
def get_youtube_metadata(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        logger.warning("GET /api/projects/%s/youtube-metadata -> 404 (no such project)", project_id)
        return _not_found(project_id)
    data = youtube_meta.build_metadata(db, project)
    logger.info(
        "GET /api/projects/%s/youtube-metadata -> 200 (highlights=%d shorts=%d)",
        project_id, len(data["highlights"]), len(data["shorts"]),
    )
    return data


class FlightlogLinkIn(BaseModel):
    youtube_url: str
    label: str | None = None


@router.post("/{project_id}/flightlog-link")
def push_flightlog_link(
    project_id: int,
    payload: FlightlogLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Called by the external YTChannelMgmt MCP once it has actually published a video —
    VidFactory itself has no public URL for its own (local-file) outputs to push automatically."""
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return _not_found(project_id)
    if not project.external_flight_id:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "NO_FLIGHT_LINKED", "message": "Project has no Flightlog flight id set.", "details": {}}},
        )
    if not user.flightlog_api_key:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "NO_API_KEY", "message": "No Flightlog API key configured on your account.", "details": {}}},
        )
    cfg = get_config()
    if not cfg.flightlog.base_url:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "FLIGHTLOG_DISABLED", "message": "VF_FLIGHTLOG_URL is not configured.", "details": {}}},
        )
    try:
        link = flightlog_client.push_video_link(
            cfg.flightlog.base_url, user.flightlog_api_key, project.external_flight_id,
            project.id, payload.youtube_url, payload.label,
        )
    except flightlog_client.FlightlogError as exc:
        logger.warning(
            "POST /api/projects/%s/flightlog-link -> %s %s: %s", project_id, exc.status_code, exc.code, exc.message
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": {}}},
        )
    except httpx.HTTPError as exc:
        logger.warning("POST /api/projects/%s/flightlog-link -> Flightlog unreachable: %s", project_id, exc)
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "FLIGHTLOG_UNREACHABLE", "message": str(exc), "details": {}}},
        )
    return link.model_dump(mode="json")
