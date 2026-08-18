from __future__ import annotations

from datetime import date as Date_, datetime

from pydantic import BaseModel

from vidfactory.models.flightlog import FlightMetadataOut, SegmentOut


class HighlightMeta(BaseModel):
    id: int
    name: str
    comment: str | None
    start: float
    end: float
    role: str
    type: str
    use_in_summary: bool
    make_short: bool


class SegmentOrderEntry(BaseModel):
    """One entry of `merge_overlaps()` over `use_in_summary` highlights, in content order.

    NOT a rendered Summary.mp4 timestamp — `auto_fill` filler (unnamed, added only when a build
    undershoots its target length) can shift real offsets, and the target length used per build
    isn't persisted. `offset_in_segments_seconds` is the cumulative duration of highlight segments
    only, i.e. where this segment would land in a summary built with no filler.
    """

    name: str | None
    comment: str | None
    type: str
    start: float
    end: float | None
    duration: float
    offset_in_segments_seconds: float


class ShortMeta(BaseModel):
    id: int
    output_file: str
    title: str | None
    short_type: str
    duration: float | None
    created_at: datetime
    source_highlight_id: int | None
    source_highlight_name: str | None
    segments_used: dict


class ProjectYoutubeMetadata(BaseModel):
    project_id: int
    date: Date_ | None
    flight_type: str
    external_flight_id: str | None
    full_flight_file: str | None
    summary_file: str | None
    fullflight_music_file: str | None
    highlights: list[HighlightMeta]
    segment_order: list[SegmentOrderEntry]
    shorts: list[ShortMeta]
    # Best-effort Flightlog enrichment — None whenever external_flight_id/the owner's API key
    # isn't set, or the Flightlog call fails. Never blocks this endpoint (see core/youtube_meta.py).
    flight: FlightMetadataOut | None = None
    flight_segments: list[SegmentOut] | None = None
