from __future__ import annotations

import logging

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from vidfactory.api.templating import templates
from vidfactory.config import get_config
from vidfactory.core import filebrowser, gpu_detector, highlights, projects
from vidfactory.core.concat import concatenate
from vidfactory.core.ffmpeg_runner import get_runner
from vidfactory.core.jobs import registry
from vidfactory.database.db import get_db, get_engine
from vidfactory.database.models import Project
from vidfactory.models.highlight import HighlightIn, HighlightOut

logger = logging.getLogger(__name__)

router = APIRouter()


def _video_files() -> list[str]:
    """Flat list of source video filenames under the videos root (InstaOut)."""
    try:
        listing = filebrowser.list_dir("videos", "")
    except filebrowser.PathNotAllowed:
        return []
    return [e["rel"] for e in listing["entries"] if e["kind"] == "video"]


def _redirect(project_id: int) -> Response:
    return Response(status_code=204, headers={"HX-Redirect": f"/projects/{project_id}"})


@router.get("/projects/by-outing/{outing_id}", include_in_schema=False)
def project_for_outing(outing_id: int, db: Session = Depends(get_db)):
    project = projects.get_or_create_for_outing(db, outing_id)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", include_in_schema=False)
def project_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    videos_root = get_config().mount_roots()["videos"]
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "project": project,
            "outing": project.outing,
            "parts": projects.ordered_parts(project),
            "hike": project.hike,
            "instaout_files": _video_files(),
            "videos_root": str(videos_root),
        },
    )


@router.get("/projects/{project_id}/editor", include_in_schema=False)
def editor_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return RedirectResponse(f"/projects/{project_id}", status_code=303)
    return templates.TemplateResponse(
        request, "editor.html", {"project": project, "outing": project.outing}
    )


@router.get("/api/projects/{project_id}/fullflight/video", include_in_schema=False)
def fullflight_video(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or not project.full_flight_file or not Path(project.full_flight_file).exists():
        return Response(status_code=404)
    # Starlette FileResponse honours Range requests, so the browser can scrub/seek.
    return FileResponse(project.full_flight_file, media_type="video/mp4")


@router.get("/api/projects/{project_id}/highlights", include_in_schema=False)
def list_highlights(project_id: int, db: Session = Depends(get_db)):
    rows = [HighlightOut.model_validate(h) for h in highlights.list_highlights(db, project_id)]
    return {"data": rows, "total": len(rows)}


@router.post("/api/projects/{project_id}/highlights", include_in_schema=False)
def create_highlight(project_id: int, payload: HighlightIn, db: Session = Depends(get_db)):
    if db.get(Project, project_id) is None:
        return Response(status_code=404)
    h = highlights.create_highlight(db, project_id, payload.model_dump())
    return HighlightOut.model_validate(h)


@router.post("/api/projects/{project_id}/highlights/{highlight_id}", include_in_schema=False)
def update_highlight(project_id: int, highlight_id: int, payload: HighlightIn, db: Session = Depends(get_db)):
    h = highlights.update_highlight(db, highlight_id, payload.model_dump())
    if h is None:
        return Response(status_code=404)
    return HighlightOut.model_validate(h)


@router.post("/api/projects/{project_id}/highlights/{highlight_id}/delete", include_in_schema=False)
def delete_highlight(project_id: int, highlight_id: int, db: Session = Depends(get_db)):
    highlights.delete_highlight(db, highlight_id)
    return Response(status_code=204)


@router.post("/api/projects/{project_id}/flight-type", include_in_schema=False)
async def set_flight_type(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    projects.set_flight_type(db, project, str(form.get("flight_type", "normal_flight")))
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/parts/add", include_in_schema=False)
async def add_parts(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    rels = (await request.form()).getlist("files")
    root = get_config().mount_roots()["videos"]
    files = [str(root / rel) for rel in rels if rel]
    if files:
        projects.add_parts(db, project, files)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/parts/{part_id}/move", include_in_schema=False)
async def move_part(project_id: int, part_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    direction = str((await request.form()).get("dir", "up"))
    projects.move_part(db, project, part_id, direction)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/parts/{part_id}/delete", include_in_schema=False)
def delete_part(project_id: int, part_id: int, db: Session = Depends(get_db)):
    projects.remove_part(db, part_id)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/hike", include_in_schema=False)
async def set_hike(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    root = get_config().mount_roots()["videos"]
    sources = [str(root / rel) for rel in form.getlist("sources") if rel]
    try:
        speed = float(form.get("speed_factor", "1.0") or "1.0")
    except ValueError:
        speed = 1.0
    projects.set_hike(db, project, sources, speed)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/build", include_in_schema=False)
def build_fullflight(project_id: int, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    if not project.source_parts:
        return Response("No source parts added.", status_code=400)
    job = registry.run("concat", _build_target(project_id))
    return {"job_id": job.id}


@router.get("/api/projects/{project_id}/status", include_in_schema=False)
def project_status(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        return Response(status_code=404)
    return {"full_flight_file": project.full_flight_file}


def _build_target(project_id: int):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_project(db, project_id)
            parts = [p.file for p in projects.ordered_parts(project)]
            hike = project.hike
            use_hike = hike is not None and project.flight_type == "hike_and_fly"
            hike_files = list(hike.sources) if use_hike else None
            speed = hike.speed_factor if use_hike else 1.0
            output = projects.fullflight_output_path(project)
            cfg = get_config()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            result = concatenate(
                parts, output, get_runner(), gpu,
                hike_files=hike_files, speed_factor=speed,
                video_bitrate=cfg.encode.video_bitrate, audio_bitrate=cfg.encode.audio_bitrate,
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            project.full_flight_file = result["output"]
            db.commit()
            return result["output"]

    return target
