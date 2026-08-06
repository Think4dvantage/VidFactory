from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from vidfactory.core.flightlog import derived_metrics
from vidfactory.database.models import Outing


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    kind: str
    elevation_m: int | None = None


class OutingBase(BaseModel):
    date: datetime.date
    launch_site_id: int | None = None
    landing_site_id: int | None = None
    glider: str | None = None
    harness: str | None = None
    flight_time_min: int | None = None
    distance_km: float | None = None
    max_alt_m: int | None = None
    category: str | None = None
    launch_type: str | None = None
    comment: str | None = None
    climb_m: int | None = None
    hike_distance_km: float | None = None
    hike_duration_min: int | None = None


class OutingCreate(OutingBase):
    pass


class OutingUpdate(BaseModel):
    date: datetime.date | None = None
    launch_site_id: int | None = None
    landing_site_id: int | None = None
    glider: str | None = None
    harness: str | None = None
    flight_time_min: int | None = None
    distance_km: float | None = None
    max_alt_m: int | None = None
    category: str | None = None
    launch_type: str | None = None
    comment: str | None = None


class IgcOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file: str
    duration_s: int | None = None
    max_alt_m: int | None = None
    thermal_count: int = 0
    total_climb_m: int = 0
    best_climb_ms: float | None = None
    avg_climb_ms: float | None = None
    glide_count: int = 0
    total_glide_km: float | None = None
    glide_ratio: float | None = None


class OutingOut(OutingBase):
    id: int
    launch_site_name: str | None = None
    landing_site_name: str | None = None
    height_diff_m: int | None = None
    alt_gain_m: int | None = None
    has_project: bool = False
    buddy_ids: list[int] = []
    buddy_names: list[str] = []
    igc: IgcOut | None = None
    # True when no track is linked but an IGC file for this date sits unlinked on the share.
    igc_available: bool = False


def serialize_outing(o: Outing, igc_available: bool = False) -> OutingOut:
    metrics = derived_metrics(o)
    return OutingOut(
        id=o.id,
        date=o.date,
        launch_site_id=o.launch_site_id,
        landing_site_id=o.landing_site_id,
        glider=o.glider,
        harness=o.harness,
        flight_time_min=o.flight_time_min,
        distance_km=o.distance_km,
        max_alt_m=o.max_alt_m,
        category=o.category,
        launch_type=o.launch_type,
        comment=o.comment,
        climb_m=o.climb_m,
        hike_distance_km=o.hike_distance_km,
        hike_duration_min=o.hike_duration_min,
        launch_site_name=o.launch_site.name if o.launch_site else None,
        landing_site_name=o.landing_site.name if o.landing_site else None,
        height_diff_m=metrics["height_diff_m"],
        alt_gain_m=metrics["alt_gain_m"],
        has_project=o.project is not None,
        buddy_ids=[b.id for b in o.buddies],
        buddy_names=[b.name for b in o.buddies],
        igc=IgcOut.model_validate(o.igc_track) if o.igc_track else None,
        igc_available=igc_available,
    )
