"""Highlight CRUD + overlap merge (the merge is reused by the summary engine)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vidfactory.database.models import Highlight

_FIELDS = (
    "name", "start", "end", "comment", "type", "role",
    "image_path", "duration", "use_in_summary", "make_short",
)


def list_highlights(db: Session, project_id: int) -> list[Highlight]:
    return list(
        db.execute(
            select(Highlight)
            .where(Highlight.project_id == project_id)
            .order_by(Highlight.start, Highlight.id)
        ).scalars()
    )


def create_highlight(db: Session, project_id: int, data: dict) -> Highlight:
    h = Highlight(project_id=project_id, **{k: data[k] for k in _FIELDS if k in data})
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def update_highlight(db: Session, highlight_id: int, data: dict) -> Highlight | None:
    h = db.get(Highlight, highlight_id)
    if h is None:
        return None
    for k in _FIELDS:
        if k in data:
            setattr(h, k, data[k])
    db.commit()
    db.refresh(h)
    return h


def delete_highlight(db: Session, highlight_id: int) -> bool:
    h = db.get(Highlight, highlight_id)
    if h is None:
        return False
    db.delete(h)
    db.commit()
    return True


def merge_overlaps(highlights: list[Highlight]) -> list[dict]:
    """Sort video highlights by start and merge overlapping/adjacent ranges.

    Picture highlights are point inserts and are passed through unchanged. Returns plain dicts
    so callers (the summary engine) don't depend on ORM identity.
    """
    videos = sorted(
        (h for h in highlights if h.type == "video"), key=lambda h: (h.start, h.end)
    )
    merged: list[dict] = []
    for h in videos:
        if merged and h.start <= merged[-1]["end"]:
            last = merged[-1]
            last["end"] = max(last["end"], h.end)
            if not last.get("name") and h.name:
                last["name"] = h.name
        else:
            merged.append(
                {"name": h.name, "start": h.start, "end": h.end,
                 "comment": h.comment, "role": h.role, "type": "video"}
            )
    pictures = [
        {"name": h.name, "comment": h.comment, "type": "picture",
         "image_path": h.image_path, "duration": h.duration or 5.0, "start": h.start}
        for h in highlights if h.type == "picture"
    ]
    return sorted(merged + pictures, key=lambda d: d["start"])
