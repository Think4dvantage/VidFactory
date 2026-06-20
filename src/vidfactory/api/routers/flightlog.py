from __future__ import annotations

import datetime
import logging
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
    return templates.TemplateResponse(
        request,
        "flightlog.html",
        {
            "stats": flightlog.stats(db),
            "outings": [serialize_outing(o) for o in flightlog.list_outings(db, limit=200)],
            "launch_sites": sites.list_sites(db, "launch"),
            "landing_sites": sites.list_sites(db, "landing"),
        },
    )


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
    rows = [serialize_outing(o) for o in flightlog.list_outings(db, limit, offset)]
    return {"data": rows, "total": len(rows)}
