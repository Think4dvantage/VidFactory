"""Project helpers: create standalone, manage source parts and the hike, build paths."""

from __future__ import annotations

import datetime

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vidfactory.config import get_config
from vidfactory.database.models import Hike, Project, SourcePart


def _project_query():
    return select(Project).options(
        selectinload(Project.source_parts),
        selectinload(Project.hike),
        selectinload(Project.highlights),
        selectinload(Project.pools),
        selectinload(Project.shorts),
    )


def get_owned_project(db: Session, project_id: int, owner_id: int) -> Project | None:
    """A project not owned by `owner_id` is treated as not found (never 403) — same shape
    Flightlog itself uses so ownership can't be probed via response differences."""
    return db.execute(
        _project_query().where(Project.id == project_id, Project.owner_id == owner_id)
    ).scalar_one_or_none()


def list_projects(db: Session, owner_id: int) -> list[Project]:
    return list(
        db.execute(
            select(Project)
            .where(Project.owner_id == owner_id)
            .order_by(Project.date.desc().nullslast(), Project.created_at.desc())
        ).scalars()
    )


def create_project(db: Session, owner_id: int, date: datetime.date | None = None) -> Project:
    project = Project(owner_id=owner_id, date=date or datetime.date.today(), flight_type="normal_flight")
    db.add(project)
    db.commit()
    return get_owned_project(db, project.id, owner_id)


def set_flight_type(db: Session, project: Project, flight_type: str) -> None:
    project.flight_type = flight_type
    db.commit()


def ordered_parts(project: Project) -> list[SourcePart]:
    return sorted(project.source_parts, key=lambda p: (p.order, p.id))


def add_parts(db: Session, project: Project, files: list[str]) -> None:
    start = max((p.order for p in project.source_parts), default=-1) + 1
    for i, f in enumerate(files):
        db.add(SourcePart(project_id=project.id, file=f, order=start + i))
    db.commit()


def remove_part(db: Session, part_id: int) -> None:
    part = db.get(SourcePart, part_id)
    if part:
        db.delete(part)
        db.commit()


def move_part(db: Session, project: Project, part_id: int, direction: str) -> None:
    parts = ordered_parts(project)
    idx = next((i for i, p in enumerate(parts) if p.id == part_id), None)
    if idx is None:
        return
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(parts):
        parts[idx].order, parts[swap].order = parts[swap].order, parts[idx].order
        db.commit()


def add_hike_sources(db: Session, project: Project, files: list[str], speed_factor: float) -> None:
    """Append uploaded hike-footage files and update the speed factor (there's no NAS browser to
    pick from any more — files arrive via upload, so this always appends rather than replacing)."""
    hike = project.hike
    if hike is None:
        hike = Hike(project_id=project.id, sources=[])
        db.add(hike)
    if files:
        hike.sources = list(hike.sources) + files
    hike.speed_factor = speed_factor
    db.commit()


def remove_hike_source(db: Session, project: Project, index: int) -> None:
    hike = project.hike
    if hike is None:
        return
    sources = list(hike.sources)
    if 0 <= index < len(sources):
        del sources[index]
    hike.sources = sources
    db.commit()


def _stem(project: Project) -> str:
    # Project id is included because output roots are shared across all users' files — two
    # pilots flying the same day would otherwise overwrite each other's outputs.
    date = project.date or project.created_at.date()
    return f"{date.strftime('%Y%m%d')}_P{project.id}"


def fullflight_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_fullflights"]
    return str(root / f"{_stem(project)}_FullFlight.mp4")


def preview_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_fullflights"]
    return str(root / f"{_stem(project)}_preview.mp4")


def summary_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_summaries"]
    return str(root / f"{_stem(project)}_Summary.mp4")


def fullmusic_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_fullflights"]
    return str(root / f"{_stem(project)}_FullFlight_withMusic.mp4")


def credits_path_for(video_output: str) -> str:
    p = Path(video_output)
    return str(p.with_name(p.stem + "_MusicCredits.txt"))
