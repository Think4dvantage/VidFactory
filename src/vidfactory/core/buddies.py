"""Flight buddies: the managed roster of people the user flies with."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vidfactory.database.models import Buddy


def list_all(db: Session) -> list[Buddy]:
    return list(
        db.execute(select(Buddy).order_by(Buddy.sort_order, Buddy.name)).scalars()
    )


def add(db: Session, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    exists = db.execute(select(Buddy.id).where(Buddy.name == name)).first()
    if exists:
        return False
    db.add(Buddy(name=name))
    db.commit()
    return True


def delete(db: Session, buddy_id: int) -> None:
    row = db.get(Buddy, buddy_id)
    if row is not None:
        db.delete(row)
        db.commit()


def by_ids(db: Session, ids: list[int]) -> list[Buddy]:
    if not ids:
        return []
    return list(db.execute(select(Buddy).where(Buddy.id.in_(ids))).scalars())
