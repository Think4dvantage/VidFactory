from __future__ import annotations

import datetime
import logging
import math
import tempfile

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from vidfactory.api.templating import templates
from vidfactory.core import flightlog, sites
from vidfactory.core.importer import import_flugbuch
from vidfactory.database.db import get_db
from vidfactory.database.models import Outing
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
    return {
        "outings": [serialize_outing(o) for o in rows],
        "total": total,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "f": f,
    }

_OUTING_FIELDS = (
    "date", "launch_site_id", "landing_site_id", "glider", "harness",
    "flight_time_min", "distance_km", "max_alt_m", "category", "launch_type", "comment",
)
_INT_FIELDS = {"launch_site_id", "landing_site_id", "flight_time_min", "max_alt_m"}
_FLOAT_FIELDS = {"distance_km"}


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


async def _form_values(request: Request) -> dict:
    form = await request.form()
    return {f: _coerce(f, str(form.get(f, ""))) for f in _OUTING_FIELDS}


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
    }
    ctx.update(_table_context(db, request))
    return templates.TemplateResponse(request, "flightlog.html", ctx)


@router.get("/api/flightlog/outings/table", include_in_schema=False)
def outings_table(request: Request, db: Session = Depends(get_db)):
    ctx = _table_context(db, request)
    ctx["options"] = flightlog.filter_options(db)
    return templates.TemplateResponse(request, "partials/outings_table.html", ctx)


@router.get("/api/flightlog/outings/{outing_id}/form", include_in_schema=False)
def outing_form(outing_id: int, request: Request, db: Session = Depends(get_db)):
    o = flightlog.get_outing(db, outing_id)
    if o is None:
        return Response(status_code=404)
    return templates.TemplateResponse(
        request,
        "partials/outing_form.html",
        {
            "outing": serialize_outing(o),
            "launch_sites": sites.list_sites(db, "launch"),
            "landing_sites": sites.list_sites(db, "landing"),
        },
    )


# ---- Mutations (HTMX -> HX-Redirect) -------------------------------------

@router.post("/api/flightlog/outings", include_in_schema=False)
async def create_outing(request: Request, db: Session = Depends(get_db)):
    values = await _form_values(request)
    if values["date"] is None:
        return Response("date is required", status_code=400)
    db.add(Outing(**values))
    db.commit()
    return _redirect()


@router.post("/api/flightlog/outings/{outing_id}", include_in_schema=False)
async def update_outing(outing_id: int, request: Request, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is None:
        return Response(status_code=404)
    for field, value in (await _form_values(request)).items():
        setattr(o, field, value)
    db.commit()
    return _redirect()


@router.post("/api/flightlog/outings/{outing_id}/delete", include_in_schema=False)
def delete_outing(outing_id: int, db: Session = Depends(get_db)):
    o = db.get(Outing, outing_id)
    if o is not None:
        db.delete(o)
        db.commit()
    return _redirect()


@router.post("/api/flightlog/import", include_in_schema=False)
async def import_xlsx(file: UploadFile, db: Session = Depends(get_db)):
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    summary = import_flugbuch(tmp_path, db, replace=True)
    logger.info("Flugbuch upload import: %s", summary)
    return _redirect()


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
