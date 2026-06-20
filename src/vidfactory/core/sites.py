from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vidfactory.database.models import Site


def list_sites(db: Session, kind: str | None = None) -> list[Site]:
    stmt = select(Site)
    if kind:
        stmt = stmt.where(Site.kind == kind)
    stmt = stmt.order_by(Site.name)
    return list(db.execute(stmt).scalars())


def get_or_create(db: Session, name: str, kind: str, elevation_m: int | None = None) -> Site:
    site = db.execute(
        select(Site).where(Site.name == name, Site.kind == kind)
    ).scalar_one_or_none()
    if site is None:
        site = Site(name=name, kind=kind, elevation_m=elevation_m)
        db.add(site)
        db.flush()
    return site
