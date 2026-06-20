"""Outing helpers + flight-log rollups (replacing the Flugbuch Übersicht sheet)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from vidfactory.database.models import Outing, Site


def derived_metrics(outing: Outing) -> dict:
    """height_diff = max_alt - landing_elev ; alt_gain = max_alt - launch_elev (as in Flugbuch)."""
    max_alt = outing.max_alt_m
    launch_elev = outing.launch_site.elevation_m if outing.launch_site else None
    landing_elev = outing.landing_site.elevation_m if outing.landing_site else None
    height_diff = max_alt - landing_elev if (max_alt is not None and landing_elev is not None) else None
    alt_gain = max_alt - launch_elev if (max_alt is not None and launch_elev is not None) else None
    return {"height_diff_m": height_diff, "alt_gain_m": alt_gain}


def list_outings(db: Session, limit: int | None = None, offset: int = 0) -> list[Outing]:
    stmt = (
        select(Outing)
        .options(selectinload(Outing.launch_site), selectinload(Outing.landing_site))
        .order_by(Outing.date.desc(), Outing.id.desc())
        .offset(offset)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def get_outing(db: Session, outing_id: int) -> Outing | None:
    return db.execute(
        select(Outing)
        .options(selectinload(Outing.launch_site), selectinload(Outing.landing_site))
        .where(Outing.id == outing_id)
    ).scalar_one_or_none()


def _site_frequency(db: Session, fk_column, limit: int) -> list[tuple[str, int]]:
    rows = db.execute(
        select(Site.name, func.count(Outing.id))
        .join(Outing, fk_column == Site.id)
        .group_by(Site.name)
        .order_by(func.count(Outing.id).desc(), Site.name)
        .limit(limit)
    ).all()
    return [(name, count) for name, count in rows]


def stats(db: Session, top: int = 10) -> dict:
    total_flights = db.scalar(select(func.count()).select_from(Outing)) or 0
    total_min = db.scalar(select(func.coalesce(func.sum(Outing.flight_time_min), 0))) or 0
    total_km = db.scalar(select(func.coalesce(func.sum(Outing.distance_km), 0.0))) or 0.0

    # Seasonality: flights per calendar month (SQLite strftime).
    month_rows = db.execute(
        select(func.strftime("%m", Outing.date), func.count(Outing.id))
        .group_by(func.strftime("%m", Outing.date))
        .order_by(func.strftime("%m", Outing.date))
    ).all()
    by_month = {int(mm): count for mm, count in month_rows if mm}

    year_rows = db.execute(
        select(func.strftime("%Y", Outing.date), func.count(Outing.id))
        .group_by(func.strftime("%Y", Outing.date))
        .order_by(func.strftime("%Y", Outing.date))
    ).all()
    by_year = {int(yy): count for yy, count in year_rows if yy}

    cat_rows = db.execute(
        select(Outing.category, func.count(Outing.id))
        .group_by(Outing.category)
        .order_by(func.count(Outing.id).desc())
    ).all()

    return {
        "total_flights": total_flights,
        "total_hours": round(total_min / 60, 1),
        "total_distance_km": round(float(total_km), 1),
        "top_launches": _site_frequency(db, Outing.launch_site_id, top),
        "top_landings": _site_frequency(db, Outing.landing_site_id, top),
        "by_month": by_month,
        "by_year": by_year,
        "by_category": [(c or "—", n) for c, n in cat_rows],
    }
