"""Project helpers: create standalone, manage source parts and the hike, build paths."""

from __future__ import annotations

import datetime

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vidfactory.config import get_config
from vidfactory.database.models import Hike, Project, SourcePart


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        select(Project)
        .options(
            selectinload(Project.source_parts),
            selectinload(Project.hike),
            selectinload(Project.highlights),
            selectinload(Project.pools),
            selectinload(Project.shorts),
        )
        .where(Project.id == project_id)
    ).scalar_one_or_none()


def list_projects(db: Session) -> list[Project]:
    return list(
        db.execute(
            select(Project).order_by(Project.date.desc().nullslast(), Project.created_at.desc())
        ).scalars()
    )


def create_project(db: Session, date: datetime.date | None = None) -> Project:
    project = Project(date=date or datetime.date.today(), flight_type="normal_flight")
    db.add(project)
    db.commit()
    return get_project(db, project.id)


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


def set_hike(db: Session, project: Project, sources: list[str], speed_factor: float) -> None:
    hike = project.hike
    if not sources:
        if hike:
            db.delete(hike)
            db.commit()
        return
    if hike is None:
        hike = Hike(project_id=project.id)
        db.add(hike)
    hike.sources = sources
    hike.speed_factor = speed_factor
    db.commit()


def _stem(project: Project) -> str:
    date = project.date or project.created_at.date()
    return date.strftime("%Y%m%d")


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
    root = get_config().mount_roots()["output_summaries"]
    return str(root / f"{_stem(project)}_FullFlight_withMusic.mp4")


def credits_path_for(video_output: str) -> str:
    p = Path(video_output)
    return str(p.with_name(p.stem + "_MusicCredits.txt"))
