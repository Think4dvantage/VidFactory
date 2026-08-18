"""Pydantic mirror of Flightlog's frozen `/api/integration/v1` contract.

Field names are copied verbatim from that contract, not guessed. A breaking change there bumps
the path to v2 rather than silently editing v1, so these models should only ever need to change
alongside a deliberate `VF_FLIGHTLOG_URL` version bump.
"""

from __future__ import annotations

from datetime import date as Date_, datetime

from pydantic import BaseModel


class IgcSummary(BaseModel):
    duration_s: float | None = None
    thermal_count: int | None = None
    best_climb_ms: float | None = None
    peak_climb_ms: float | None = None
    glide_ratio: float | None = None
    distance_km: float | None = None
    max_alt_igc_m: float | None = None
    alt_gain_igc_m: float | None = None


class FlightLink(BaseModel):
    kind: str
    external_id: str
    url: str
    label: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FlightMetadataOut(BaseModel):
    id: str
    flight_date: Date_ | None = None
    takeoff_time: datetime | None = None
    landing_time: datetime | None = None
    launch_site_name: str | None = None
    landing_site_name: str | None = None
    category_name: str | None = None
    glider_name: str | None = None
    harness_name: str | None = None
    launch_technique: str | None = None
    duration_min: int | None = None
    distance_km: float | None = None
    max_alt_m: float | None = None
    alt_gain_m: float | None = None
    site_drop_m: float | None = None
    total_descent_m: float | None = None
    has_igc_track: bool = False
    igc_summary: IgcSummary | None = None
    links: list[FlightLink] = []


class SegmentOut(BaseModel):
    kind: str  # thermal | glide | takeoff | landing | max_alt | top_of_climb
    start_offset_s: float
    start_at: datetime | None = None
    duration_s: float | None = None
    alt_change_m: float | None = None
    vertical_velocity_ms: float | None = None
    glide_ratio: float | None = None
