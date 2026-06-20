from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from vidfactory.core.jobs import registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/jobs", include_in_schema=False)
def list_jobs():
    return {"data": [j.public() for j in registry.list()], "total": len(registry.list())}


@router.post("/api/jobs/{job_id}/cancel", include_in_schema=False)
def cancel_job(job_id: str):
    ok = registry.cancel(job_id)
    return JSONResponse({"cancelled": ok})


@router.get("/events/{job_id}")
async def job_events(job_id: str):
    """Stream a job's progress until it reaches a terminal state."""

    async def event_gen():
        while True:
            job = registry.get(job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"message": "unknown job"})}
                return
            yield {"event": "progress", "data": json.dumps(job.public())}
            if job.is_terminal:
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_gen())
