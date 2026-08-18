"""Assembles the read-only YouTube-metadata payload for a project (M5a — API only, no file).

Consumed by an external YouTube-management service over `GET /api/projects/{id}/youtube-metadata`;
that service is expected to derive chapters/titles/descriptions itself from this raw data. Full-flight
timestamps come straight from `Highlight` rows (ground truth). `segment_order` reuses
`highlights.merge_overlaps()` to report the *content* order highlights would appear in a Summary
build — see `SegmentOrderEntry` in `models/youtube.py` for why it is not a rendered-video timestamp.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from vidfactory.config import get_config
from vidfactory.core import flightlog_client
from vidfactory.core.highlights import list_highlights, merge_overlaps
from vidfactory.database.models import Project

logger = logging.getLogger(__name__)


def _fetch_flight(project: Project) -> tuple[dict | None, list[dict] | None]:
    """Best-effort Flightlog enrichment — never raises. None/None if not configured or on error."""
    cfg = get_config()
    api_key = project.owner.flightlog_api_key if project.owner else None
    if not (cfg.flightlog.base_url and project.external_flight_id and api_key):
        return None, None
    try:
        meta = flightlog_client.get_flight_metadata(
            cfg.flightlog.base_url, api_key, project.external_flight_id
        )
        if meta is None:
            return None, None
        segments = flightlog_client.get_flight_segments(
            cfg.flightlog.base_url, api_key, project.external_flight_id
        )
        return meta.model_dump(mode="json"), [s.model_dump(mode="json") for s in segments]
    except (flightlog_client.FlightlogError, httpx.HTTPError) as exc:
        # Best-effort enrichment — a Flightlog outage/misconfiguration must never break this
        # endpoint, so any error (documented API error or raw network failure) just omits it.
        logger.info(
            "Flightlog enrichment skipped for project %s (flight %s): %s",
            project.id, project.external_flight_id, exc,
        )
        return None, None


def build_metadata(db: Session, project: Project) -> dict:
    hls = list_highlights(db, project.id)

    highlights_out = [
        {
            "id": h.id,
            "name": h.name,
            "comment": h.comment,
            "start": h.start,
            "end": h.end,
            "role": h.role,
            "type": h.type,
            "use_in_summary": h.use_in_summary,
            "make_short": h.make_short,
        }
        for h in hls
    ]

    segments = merge_overlaps([h for h in hls if h.use_in_summary])
    offset = 0.0
    segment_order: list[dict] = []
    for seg in segments:
        dur = float(seg["duration"]) if seg["type"] == "picture" else float(seg["end"] - seg["start"])
        segment_order.append(
            {
                "name": seg.get("name") or None,
                "comment": seg.get("comment"),
                "type": seg["type"],
                "start": seg["start"],
                "end": seg.get("end"),
                "duration": dur,
                "offset_in_segments_seconds": offset,
            }
        )
        offset += dur

    highlight_names = {h.id: h.name for h in hls}
    shorts_out = [
        {
            "id": s.id,
            "output_file": s.output_file,
            "title": s.title,
            "short_type": s.short_type,
            "duration": s.duration,
            "created_at": s.created_at,
            "source_highlight_id": s.source_highlight_id,
            "source_highlight_name": highlight_names.get(s.source_highlight_id),
            "segments_used": s.segments_used,
        }
        for s in project.shorts
    ]

    flight, flight_segments = _fetch_flight(project)

    return {
        "project_id": project.id,
        "date": project.date,
        "flight_type": project.flight_type,
        "external_flight_id": project.external_flight_id,
        "full_flight_file": project.full_flight_file,
        "summary_file": project.summary_file,
        "fullflight_music_file": project.fullflight_music_file,
        "highlights": highlights_out,
        "segment_order": segment_order,
        "shorts": shorts_out,
        "flight": flight,
        "flight_segments": flight_segments,
    }
