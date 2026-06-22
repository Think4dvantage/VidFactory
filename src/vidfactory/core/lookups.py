"""Managed dropdown values for the outing form (category / glider / harness / launch_type)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vidfactory.database.models import Lookup, Outing

# The kinds the form exposes, with a human label for the manager UI.
KINDS: dict[str, str] = {
    "category": "Category",
    "glider": "Glider",
    "harness": "Harness",
    "launch_type": "Launch type",
}


def list_for(db: Session, kind: str) -> list[str]:
    rows = db.execute(
        select(Lookup.value)
        .where(Lookup.kind == kind)
        .order_by(Lookup.sort_order, Lookup.value)
    ).all()
    return [v for (v,) in rows]


def all_options(db: Session) -> dict[str, list[str]]:
    """{kind: [values]} for every managed kind (drives the form selects)."""
    return {kind: list_for(db, kind) for kind in KINDS}


def grouped(db: Session) -> list[tuple[str, str, list[tuple[int, str]]]]:
    """(kind, label, [(id, value)]) for the manage-dropdowns UI."""
    out: list[tuple[str, str, list[tuple[int, str]]]] = []
    for kind, label in KINDS.items():
        rows = db.execute(
            select(Lookup.id, Lookup.value)
            .where(Lookup.kind == kind)
            .order_by(Lookup.sort_order, Lookup.value)
        ).all()
        out.append((kind, label, [(rid, val) for rid, val in rows]))
    return out


def add(db: Session, kind: str, value: str) -> bool:
    """Add a value (no-op for unknown kinds, blanks, or duplicates). True if inserted."""
    value = (value or "").strip()
    if kind not in KINDS or not value:
        return False
    exists = db.execute(
        select(Lookup.id).where(Lookup.kind == kind, Lookup.value == value)
    ).first()
    if exists:
        return False
    db.add(Lookup(kind=kind, value=value))
    db.commit()
    return True


def delete(db: Session, lookup_id: int) -> None:
    row = db.get(Lookup, lookup_id)
    if row is not None:
        db.delete(row)
        db.commit()


def sync_from_outings(db: Session) -> None:
    """Ensure every distinct value used by an outing exists as a lookup (e.g. after import)."""
    columns = {
        "category": Outing.category,
        "glider": Outing.glider,
        "harness": Outing.harness,
        "launch_type": Outing.launch_type,
    }
    for kind, col in columns.items():
        existing = set(list_for(db, kind))
        used = db.execute(
            select(col).where(col.is_not(None), col != "").distinct()
        ).all()
        for (value,) in used:
            if value not in existing:
                db.add(Lookup(kind=kind, value=value))
                existing.add(value)
    db.commit()
