"""Translates Flightlog IGC segments into video-timeline hints for the highlight editor.

Flightlog's `start_offset_s` is seconds since takeoff, with no notion of when the camera started
rolling (see the segments endpoint docs). The anchor is the project's own `launch` highlight —
its `start` marks that same takeoff moment on the video timeline — so hints can't be positioned
until the pilot has marked one. Deliberately generic over `kind`: today Flightlog sends
thermal/glide/takeoff/landing/max_alt/top_of_climb, and more kinds may show up later without any
change needed here or in the editor's rendering.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from vidfactory.config import get_config
from vidfactory.core import flightlog_client
from vidfactory.database.models import Project

logger = logging.getLogger(__name__)


def get_hints(db: Session, project: Project) -> dict:
    launch = next((h for h in project.highlights if h.role == "launch"), None)
    if launch is None:
        return {"status": "no_launch_marked", "hints": []}

    cfg = get_config()
    api_key = project.owner.flightlog_api_key if project.owner else None
    if not (cfg.flightlog.base_url and project.external_flight_id and api_key):
        return {"status": "unavailable", "hints": []}

    try:
        segments = flightlog_client.get_flight_segments(
            cfg.flightlog.base_url, api_key, project.external_flight_id
        )
    except (flightlog_client.FlightlogError, httpx.HTTPError) as exc:
        # Best-effort, same as the youtube-metadata enrichment — a Flightlog hiccup must never
        # break the editor page.
        logger.info(
            "Flightlog hints skipped for project %s (flight %s): %s",
            project.id, project.external_flight_id, exc,
        )
        return {"status": "unavailable", "hints": []}

    hints = [
        {
            "kind": s.kind,
            "video_offset_s": launch.start + s.start_offset_s,
            "duration_s": s.duration_s,
            "alt_change_m": s.alt_change_m,
            "vertical_velocity_ms": s.vertical_velocity_ms,
            "glide_ratio": s.glide_ratio,
        }
        for s in segments
    ]
    return {"status": "ok", "hints": hints}
