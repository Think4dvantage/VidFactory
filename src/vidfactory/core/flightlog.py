"""Outing helpers + flight-log rollups (replacing the Flugbuch Übersicht sheet)."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from vidfactory.database.models import Outing, Site

# Columns the outings table may be sorted by (maps query value -> ORM column).
SORTABLE = {
    "date": Outing.date,
    "flight_time_min": Outing.flight_time_min,
    "distance_km": Outing.distance_km,
    "max_alt_m": Outing.max_alt_m,
}


def derived_metrics(outing: Outing) -> dict:
    """height_diff = max_alt - landing_elev ; alt_gain = max_alt - launch_elev (as in Flugbuch)."""
    max_alt = outing.max_alt_m
    launch_elev = outing.launch_site.elevation_m if outing.launch_site else None
    landing_elev = outing.landing_site.elevation_m if outing.landing_site else None
    height_diff = max_alt - landing_elev if (max_alt is not None and landing_elev is not None) else None
    alt_gain = max_alt - launch_elev if (max_alt is not None and launch_elev is not None) else None
    return {"height_diff_m": height_diff, "alt_gain_m": alt_gain}


def _apply_filters(
    stmt,
    *,
    search: str | None = None,
    year: int | None = None,
    category: str | None = None,
    glider: str | None = None,
    site_id: int | None = None,
):
    """Add WHERE clauses shared by listing and counting."""
    if search:
        like = f"%{search}%"
        site_ids = select(Site.id).where(Site.name.ilike(like))
        stmt = stmt.where(
            or_(
                Outing.glider.ilike(like),
                Outing.harness.ilike(like),
                Outing.comment.ilike(like),
                Outing.category.ilike(like),
                Outing.launch_type.ilike(like),
                Outing.launch_site_id.in_(site_ids),
                Outing.landing_site_id.in_(site_ids),
            )
        )
    if year:
        stmt = stmt.where(func.strftime("%Y", Outing.date) == f"{year:04d}")
    if category:
        stmt = stmt.where(Outing.category == category)
    if glider:
        stmt = stmt.where(Outing.glider == glider)
    if site_id:
        stmt = stmt.where(or_(Outing.launch_site_id == site_id, Outing.landing_site_id == site_id))
    return stmt


def list_outings(
    db: Session,
    *,
    limit: int | None = None,
    offset: int = 0,
    sort: str = "date",
    direction: str = "desc",
    **filters,
) -> list[Outing]:
    col = SORTABLE.get(sort, Outing.date)
    order = col.desc() if direction == "desc" else col.asc()
    stmt = (
        select(Outing)
        .options(
            selectinload(Outing.launch_site),
            selectinload(Outing.landing_site),
            selectinload(Outing.project),
        )
        .order_by(order, Outing.id.desc())
        .offset(offset)
    )
    stmt = _apply_filters(stmt, **filters)
    if limit:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def count_outings(db: Session, **filters) -> int:
    stmt = _apply_filters(select(func.count(Outing.id)), **filters)
    return db.scalar(stmt) or 0


def get_outing(db: Session, outing_id: int) -> Outing | None:
    return db.execute(
        select(Outing)
        .options(selectinload(Outing.launch_site), selectinload(Outing.landing_site))
        .where(Outing.id == outing_id)
    ).scalar_one_or_none()


def filter_options(db: Session) -> dict:
    """Distinct values that populate the filter dropdowns."""
    years = [
        int(y)
        for (y,) in db.execute(
            select(func.strftime("%Y", Outing.date))
            .distinct()
            .order_by(func.strftime("%Y", Outing.date).desc())
        ).all()
        if y
    ]
    categories = [
        c
        for (c,) in db.execute(
            select(Outing.category)
            .where(Outing.category.is_not(None), Outing.category != "")
            .distinct()
            .order_by(Outing.category)
        ).all()
    ]
    gliders = [
        g
        for (g,) in db.execute(
            select(Outing.glider)
            .where(Outing.glider.is_not(None), Outing.glider != "")
            .distinct()
            .order_by(Outing.glider)
        ).all()
    ]
    return {"years": years, "categories": categories, "gliders": gliders}


def _site_frequency(db: Session, fk_column, limit: int) -> list[tuple[str, int]]:
    rows = db.execute(
        select(Site.name, func.count(Outing.id))
        .join(Outing, fk_column == Site.id)
        .group_by(Site.name)
        .order_by(func.count(Outing.id).desc(), Site.name)
        .limit(limit)
    ).all()
    return [(name, count) for name, count in rows]


def _record(db: Session, col, value_attr: str) -> dict | None:
    """The single outing with the largest `col` (for the records card)."""
    o = db.execute(
        select(Outing)
        .options(selectinload(Outing.launch_site), selectinload(Outing.landing_site))
        .where(col.is_not(None))
        .order_by(col.desc())
        .limit(1)
    ).scalar_one_or_none()
    if o is None:
        return None
    return {
        "id": o.id,
        "date": o.date.isoformat(),
        "value": getattr(o, value_attr),
        "site": (o.launch_site.name if o.launch_site else None),
    }


def _gain_record(db: Session) -> dict | None:
    """Biggest altitude gain (max_alt - launch elevation); derived, so computed in SQL."""
    launch = aliased(Site)
    gain = (Outing.max_alt_m - launch.elevation_m).label("gain")
    row = db.execute(
        select(Outing.id, Outing.date, gain, launch.name)
        .join(launch, Outing.launch_site_id == launch.id)
        .where(Outing.max_alt_m.is_not(None), launch.elevation_m.is_not(None))
        .order_by(gain.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return {"id": row[0], "date": row[1].isoformat(), "value": row[2], "site": row[3]}


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
        "records": {
            "longest": _record(db, Outing.flight_time_min, "flight_time_min"),
            "farthest": _record(db, Outing.distance_km, "distance_km"),
            "highest": _record(db, Outing.max_alt_m, "max_alt_m"),
            "gain": _gain_record(db),
        },
    }
