"""Bulk-import IGC files already sitting in the igc root, matching them to outings.

Only *unambiguous* files are imported automatically: a date with exactly one still-untracked
outing and exactly one unlinked IGC file. Everything else is reported for manual assignment,
because outings carry no clock time so same-day multiples can't be told apart safely.
"""

from __future__ import annotations

import datetime
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vidfactory.config import get_config
from vidfactory.core import igc
from vidfactory.database.models import IgcTrack, Outing

logger = logging.getLogger(__name__)

# HFDTE030416  or  HFDTEDATE:030416,01  -> DD MM YY
_DATE_RE = re.compile(r"^HFDTE(?:DATE:)?\s*(\d{2})(\d{2})(\d{2})")


def _igc_date(path) -> datetime.date | None:
    """Cheap flight-date read from the IGC header (no full parse)."""
    try:
        with open(path, encoding="ISO-8859-1", errors="ignore") as fh:
            for line in fh:
                if line.startswith("B"):  # past the header without a date
                    return None
                m = _DATE_RE.match(line.strip())
                if m:
                    dd, mm, yy = (int(x) for x in m.groups())
                    year = 2000 + yy if yy < 80 else 1900 + yy
                    try:
                        return datetime.date(year, mm, dd)
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def _label(o: Outing) -> str:
    launch = o.launch_site.name if o.launch_site else "?"
    landing = o.landing_site.name if o.landing_site else "?"
    return f"{launch} → {landing}"


def plan(db: Session) -> dict:
    """Classify every unlinked IGC file as auto-matchable or needs-manual."""
    igc_dir = get_config().mount_roots()["igc"]
    linked = set(db.execute(select(IgcTrack.file)).scalars().all())
    files = [p for p in igc_dir.glob("*") if p.suffix.lower() == ".igc" and p.name not in linked]

    files_by_date: dict[datetime.date, list[str]] = {}
    undated: list[str] = []
    for p in files:
        d = _igc_date(p)
        (undated if d is None else files_by_date.setdefault(d, [])).append(p.name)

    dates = list(files_by_date)
    rows = db.execute(
        select(Outing)
        .options(
            selectinload(Outing.igc_track),
            selectinload(Outing.launch_site),
            selectinload(Outing.landing_site),
        )
        .where(Outing.date.in_(dates))
    ).scalars().all() if dates else []
    all_by_date: dict[datetime.date, list[Outing]] = {}
    free_by_date: dict[datetime.date, list[Outing]] = {}
    for o in rows:
        all_by_date.setdefault(o.date, []).append(o)
        if o.igc_track is None:
            free_by_date.setdefault(o.date, []).append(o)

    matched, ambiguous = [], []
    for d, fnames in sorted(files_by_date.items()):
        free = free_by_date.get(d, [])
        if len(fnames) == 1 and len(free) == 1:
            o = free[0]
            matched.append({"outing_id": o.id, "date": d.isoformat(), "file": fnames[0], "label": _label(o)})
            continue
        if not all_by_date.get(d):
            reason = "no outing logged on this date"
        elif not free:
            reason = "that day's outing already has a track"
        elif len(fnames) > 1 and len(free) == 1:
            reason = f"{len(fnames)} IGC files but only 1 free flight"
        elif len(free) > 1:
            reason = f"{len(free)} flights logged that day"
        else:
            reason = "ambiguous"
        ambiguous.append({"date": d.isoformat(), "files": sorted(fnames), "reason": reason})

    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "undated": sorted(undated),
        "total_files": len(files),
    }


def run_import(db: Session) -> dict:
    """Analyze and attach every cleanly-matched file. Returns what happened."""
    p = plan(db)
    igc_dir = get_config().mount_roots()["igc"]
    imported, failed = [], []
    for m in p["matched"]:
        try:
            stats = igc.analyze(str(igc_dir / m["file"]))
        except igc.IgcError as exc:
            failed.append({**m, "error": str(exc)})
            continue
        db.add(IgcTrack(outing_id=m["outing_id"], file=m["file"],
                        analyzed_at=datetime.datetime.utcnow(), **stats))
        imported.append(m)
    db.commit()
    logger.info("Bulk IGC import: %d imported, %d failed, %d ambiguous",
                len(imported), len(failed), len(p["ambiguous"]))
    return {"imported": imported, "failed": failed, "ambiguous": p["ambiguous"], "undated": p["undated"]}
