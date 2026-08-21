from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from vidfactory.api.auth_deps import require_user
from vidfactory.api.templating import templates
from vidfactory.core.jobs import registry
from vidfactory.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _public_with_queue_position(job) -> dict:
    return {**job.public(), "queue_position": registry.position_in_queue(job.id)}


@router.get("/jobs", include_in_schema=False)
def jobs_page(request: Request, user: User = Depends(require_user)):
    """All of the user's jobs (running/queued/finished) across every project — the FFmpeg queue
    is global (M13), so this is the one place to see everything in flight without hunting
    through each project's own page. `/api/jobs` already has everything this needs, including
    `queue_position`; the page itself just polls it (see static/jobs.js)."""
    return templates.TemplateResponse(request, "jobs.html", {})


@router.get("/api/jobs", include_in_schema=False)
def list_jobs(user: User = Depends(require_user)):
    jobs = registry.list(user.id)
    return {"data": [_public_with_queue_position(j) for j in jobs], "total": len(jobs)}


@router.post("/api/jobs/{job_id}/cancel", include_in_schema=False)
def cancel_job(job_id: str, user: User = Depends(require_user)):
    if registry.get_owned(job_id, user.id) is None:
        return JSONResponse(status_code=404, content={"cancelled": False})
    ok = registry.cancel(job_id)
    return JSONResponse({"cancelled": ok})


@router.get("/events/{job_id}")
async def job_events(job_id: str, user: User = Depends(require_user)):
    """Stream a job's progress until it reaches a terminal state."""

    async def event_gen():
        while True:
            job = registry.get_owned(job_id, user.id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"message": "unknown job"})}
                return
            yield {"event": "progress", "data": json.dumps(_public_with_queue_position(job))}
            if job.is_terminal:
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_gen())
