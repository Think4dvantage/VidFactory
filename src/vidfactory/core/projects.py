"""Project helpers: create from an outing, manage source parts and the hike, build paths."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vidfactory.config import get_config
from vidfactory.database.models import Hike, Outing, Project, SourcePart


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        select(Project)
        .options(
            selectinload(Project.source_parts),
            selectinload(Project.hike),
            selectinload(Project.highlights),
            selectinload(Project.pools),
            selectinload(Project.shorts),
            selectinload(Project.outing).selectinload(Outing.launch_site),
            selectinload(Project.outing).selectinload(Outing.landing_site),
        )
        .where(Project.id == project_id)
    ).scalar_one_or_none()


def get_or_create_for_outing(db: Session, outing_id: int) -> Project:
    existing = db.execute(
        select(Project).where(Project.outing_id == outing_id)
    ).scalar_one_or_none()
    if existing:
        return existing
    project = Project(outing_id=outing_id, flight_type="normal_flight")
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
    date = project.outing.date if project.outing and project.outing.date else None
    return date.strftime("%Y%m%d") if date else f"project{project.id}"


def fullflight_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_fullflights"]
    return str(root / f"{_stem(project)}_FullFlight.mp4")


def summary_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_summaries"]
    return str(root / f"{_stem(project)}_Summary.mp4")


def fullmusic_output_path(project: Project) -> str:
    root = get_config().mount_roots()["output_summaries"]
    return str(root / f"{_stem(project)}_FullFlight_withMusic.mp4")


def credits_path_for(video_output: str) -> str:
    p = Path(video_output)
    return str(p.with_name(p.stem + "_MusicCredits.txt"))
