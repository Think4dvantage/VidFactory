from __future__ import annotations

import csv
import datetime
import io
import logging
import math
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from vidfactory.api.templating import templates
from vidfactory.config import get_config
from vidfactory.core import buddies, flightlog, igc, igc_import, lookups, sites
from vidfactory.database.db import get_db
from vidfactory.database.models import IgcTrack, Outing
from vidfactory.models.flightlog import SiteOut, serialize_outing

logger = logging.getLogger(__name__)

router = APIRouter()

PAGE_SIZE = 50


def _parse_filters(request: Request) -> dict:
    """Pull the (validated) filter / sort / page state out of the query string."""
    q = request.query_params

    def _pos_int(name: str) -> int | None:
        v = q.get(name, "").strip()
        return int(v) if v.isdigit() else None

    sort = q.get("sort", "date")
    sort = sort if sort in flightlog.SORTABLE else "date"
    direction = q.get("direction", "desc")
    direction = direction if direction in ("asc", "desc") else "desc"
    return {
        "search": q.get("search", "").strip() or None,
        "year": _pos_int("year"),
        "category": q.get("category", "").strip() or None,
        "glider": q.get("glider", "").strip() or None,
        "site_id": _pos_int("site_id"),
        "sort": sort,
        "direction": direction,
        "page": max(1, _pos_int("page") or 1),
    }


def _table_context(db: Session, request: Request) -> dict:
    """Filtered + paginated outings plus the state the table partial echoes back."""
    f = _parse_filters(request)
    filters = {k: f[k] for k in ("search", "year", "category", "glider", "site_id")}
    total = flightlog.count_outings(db, **filters)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(f["page"], pages)
    rows = flightlog.list_outings(
        db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        sort=f["sort"], direction=f["direction"], **filters,
    )
    f["page"] = page
    igc_dates = igc_import.unlinked_igc_dates(db)
    return {
        "outings": [
            serialize_outing(o, igc_available=(o.igc_track is None and o.date in igc_dates))
            for o in rows
        ],
        "total": total,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "f": f,
    }

_OUTING_FIELDS = (
    "date", "launch_site_id", "landing_site_id", "glider", "harness",
    "flight_time_min", "distance_km", "max_alt_m", "category", "launch_type", "comment",
    "climb_m", "hike_distance_km", "hike_duration_min",
)
_INT_FIELDS = {
    "launch_site_id", "landing_site_id", "flight_time_min", "max_alt_m",
    "climb_m", "hike_duration_min",
}
_FLOAT_FIELDS = {"distance_km", "hike_distance_km"}


def _coerce(field: str, raw: str):
    raw = (raw or "").strip()
    if raw == "":
        return None
    if field == "date":
        return datetime.date.fromisoformat(raw)
    if field in _INT_FIELDS:
        return int(raw)
    if field in _FLOAT_FIELDS:
        return float(raw)
    return raw


async def _form_payload(request: Request) -> tuple[dict, list[int]]:
    """Coerced scalar fields + the selected buddy ids from one form read."""
    form = await request.form()
    values = {f: _coerce(f, str(form.get(f, ""))) for f in _OUTING_FIELDS}
    buddy_ids = [int(x) for x in form.getlist("buddy_ids") if str(x).isdigit()]
    return values, buddy_ids


def _redirect() -> Response:
    return Response(status_code=204, headers={"HX-Redirect": "/flightlog"})


# ---- Page ----------------------------------------------------------------

@router.get("/flightlog", include_in_schema=False)
def flightlog_page(request: Request, db: Session = Depends(get_db)):
    ctx = {
        "stats": flightlog.stats(db),
        "options": flightlog.filter_options(db),
        "launch_sites": sites.list_sites(db, "launch"),
        "landing_sites": sites.list_sites(db, "landing"),
        "lookups": lookups.all_options(db),
        "all_buddies": buddies.list_all(db),
        "hikefly_category": flightlog.HIKEFLY_CATEGORY,
    }
    ctx.update(_table_context(db, request))
    return templates.TemplateResponse(request, "flightlog.html", ctx)


@router.get("/flightlog/stats", include_in_schema=False)
def flightlog_stats_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "flightlog_stats.html",
        {
            "stats": flightlog.stats(db),
            "duration": flightlog.flight_duration_year(db),
            "category_matrix": flightlog.category_year_matrix(db),
            "launch_type": flightlog.launch_type_year(db),
            "buddy_matrix": flightlog.buddy_year_matrix(db),
            "hikefly": flightlog.hikefly_stats(db),
            "igc": flightlog.igc_summary(db),
        },
    )


@router.get("/api/flightlog/outings/table", include_in_schema=False)
def outings_table(request: Request, db: Session = Depends(get_db)):
    ctx = _table_context(db, request)
    ctx["options"] = flightlog.filter_options(db)
    return templates.TemplateResponse(request, "partials/outings_table.html", ctx)


def _form_context(db: Session, outing, igc_error: str | None = None) -> dict:
    return {
        "outing": outing,
        "launch_sites": sites.list_sites(db, "launch"),
        "landing_sites": sites.list_sites(db, "landing"),
        "lookups": lookups.all_options(db),
        "all_buddies": buddies.list_all(db),
        "hikefly_category": flightlog.HIKEFLY_CATEGORY,
        "igc_error": igc_error,
    }


def _igc_dir() -> Path:
    d = get_config().mount_roots()["igc"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _form_response(request: Request, db: Session, outing_id: int, igc_error: str | None = None) -> Response:
    """Re-render the edit form for an outing (after an IGC upload/delete)."""
    o = flightlog.get_outing(db, outing_id)
    return templates.TemplateResponse(
        request, "partials/outing_form.html", _form_context(db, serialize_outing(o), igc_error)
    )


@router.get("/api/flightlog/outings/new/form", include_in_schema=False)
def outing_form_blank(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "partials/outing_form.html", _form_context(db, None))


@router.get("/api/flightlog/outings/{outing_id}/form", include_in_schema=False)
def outing_form(outing_id: int, request: Request, db: Session = Depends(get_db)):
    o = flightlog.get_outing(db, outing_id)
    if o is None:
        return Response(status_code=404)
    return templates.TemplateResponse(
        request, "partials/outing_form.html", _form_context(db, serialize_outing(o))
    )


# ---- Mutations (HTMX -> HX-Redirect) -------------------------------------

@router.post("/api/flightlog/outings", include_in_schema=False)
async def create_outing(request: Request, db: Session = Depends(get_db)):
    values, buddy_ids = await _form_payload(request)
    if values["date"] is None:
        return Response("date is required", status_code=400)
    o = Outing(**values)
    o.buddies = buddies.by_ids(db, buddy_ids)
    db.add(o)
    db.commit()
    return _redirect()


@router.post("/api/flightlog/outings/{outing_id}", include_in_schema=False)
async def update_outing(outing_id: int, request: Request, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is None:
        return Response(status_code=404)
    values, buddy_ids = await _form_payload(request)
    for field, value in values.items():
        setattr(o, field, value)
    o.buddies = buddies.by_ids(db, buddy_ids)
    db.commit()
    return _redirect()


@router.post("/api/flightlog/outings/{outing_id}/delete", include_in_schema=False)
def delete_outing(outing_id: int, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is not None:
        db.delete(o)
        db.commit()
    return _redirect()


# ---- IGC track upload / analysis -----------------------------------------

@router.post("/api/flightlog/outings/{outing_id}/igc", include_in_schema=False)
async def upload_igc(outing_id: int, request: Request, file: UploadFile, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is None:
        return Response(status_code=404)
    dest = _igc_dir() / f"{o.date.isoformat()}_{o.id}.igc"
    dest.write_bytes(await file.read())
    try:
        stats = igc.analyze(str(dest))
    except igc.IgcError as exc:
        dest.unlink(missing_ok=True)
        logger.warning("IGC analysis failed for outing %s: %s", outing_id, exc)
        return _form_response(request, db, outing_id, igc_error=str(exc))

    track = o.igc_track or IgcTrack(outing_id=o.id)
    track.file = dest.name
    track.analyzed_at = datetime.datetime.utcnow()
    for key, value in stats.items():
        setattr(track, key, value)
    if o.igc_track is None:
        db.add(track)
    db.commit()
    logger.info("IGC analyzed for outing %s: %s thermals, %sm climbed",
                outing_id, stats["thermal_count"], stats["total_climb_m"])
    return _form_response(request, db, outing_id)


@router.post("/api/flightlog/outings/{outing_id}/igc/delete", include_in_schema=False)
def delete_igc(outing_id: int, request: Request, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is not None and o.igc_track is not None:
        (_igc_dir() / o.igc_track.file).unlink(missing_ok=True)
        db.delete(o.igc_track)
        db.commit()
    return _form_response(request, db, outing_id)


@router.get("/api/flightlog/igc/scan", include_in_schema=False)
def igc_scan(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "partials/igc_import.html", {"report": igc_import.plan(db), "applied": False}
    )


@router.post("/api/flightlog/igc/import", include_in_schema=False)
def igc_run_import(request: Request, db: Session = Depends(get_db)):
    resp = templates.TemplateResponse(
        request, "partials/igc_import.html", {"report": igc_import.run_import(db), "applied": True}
    )
    resp.headers["HX-Trigger"] = "igc-imported"
    return resp


# ---- Manage dropdown data + buddies --------------------------------------

def _manager_context(db: Session) -> dict:
    return {"groups": lookups.grouped(db), "buddies": buddies.list_all(db)}


def _manager_response(request: Request, db: Session) -> Response:
    """Render the manager; signal the page to refresh the add form."""
    resp = templates.TemplateResponse(request, "partials/lookups_manager.html", _manager_context(db))
    resp.headers["HX-Trigger"] = "lookups-changed"
    return resp


@router.get("/api/flightlog/lookups", include_in_schema=False)
def lookups_manager(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "partials/lookups_manager.html", _manager_context(db))


@router.post("/api/flightlog/lookups", include_in_schema=False)
async def lookups_add(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    lookups.add(db, str(form.get("kind", "")), str(form.get("value", "")))
    return _manager_response(request, db)


@router.post("/api/flightlog/lookups/{lookup_id}/delete", include_in_schema=False)
def lookups_delete(lookup_id: int, request: Request, db: Session = Depends(get_db)):
    lookups.delete(db, lookup_id)
    return _manager_response(request, db)


@router.post("/api/flightlog/buddies", include_in_schema=False)
async def buddies_add(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    buddies.add(db, str(form.get("name", "")))
    return _manager_response(request, db)


@router.post("/api/flightlog/buddies/{buddy_id}/delete", include_in_schema=False)
def buddies_delete(buddy_id: int, request: Request, db: Session = Depends(get_db)):
    buddies.delete(db, buddy_id)
    return _manager_response(request, db)


# ---- CSV export ----------------------------------------------------------

_CSV_COLUMNS = (
    "date", "launch_site", "landing_site", "category", "flight_time_min",
    "distance_km", "max_alt_m", "alt_gain_m", "glider", "harness", "launch_type",
    "climb_m", "hike_distance_km", "hike_duration_min", "buddies", "comment",
)


def _v(value):
    return value if value is not None else ""


@router.get("/api/flightlog/export.csv", include_in_schema=False)
def export_csv(db: Session = Depends(get_db)):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for o in flightlog.list_outings(db, sort="date", direction="asc"):
        row = serialize_outing(o)
        writer.writerow([
            row.date, row.launch_site_name or "", row.landing_site_name or "",
            row.category or "", _v(row.flight_time_min), _v(row.distance_km),
            _v(row.max_alt_m), _v(row.alt_gain_m), row.glider or "", row.harness or "",
            row.launch_type or "", _v(row.climb_m), _v(row.hike_distance_km),
            _v(row.hike_duration_min), ", ".join(row.buddy_names), row.comment or "",
        ])
    buf.seek(0)
    today = datetime.date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="flightlog_{today}.csv"'},
    )


# ---- JSON API ------------------------------------------------------------

@router.get("/api/flightlog/stats")
def stats_json(db: Session = Depends(get_db)):
    return flightlog.stats(db)


@router.get("/api/flightlog/sites")
def sites_json(kind: str | None = None, db: Session = Depends(get_db)):
    data = [SiteOut.model_validate(s).model_dump() for s in sites.list_sites(db, kind)]
    return {"data": data, "total": len(data)}


@router.get("/api/flightlog/outings")
def outings_json(limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    rows = [serialize_outing(o) for o in flightlog.list_outings(db, limit=limit, offset=offset)]
    return {"data": rows, "total": len(rows)}
