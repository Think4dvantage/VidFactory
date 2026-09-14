from __future__ import annotations

import datetime
import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from vidfactory.api.auth_deps import require_user
from vidfactory.api.templating import templates
from vidfactory.config import get_config
import re

from vidfactory.core import chunked_upload, filebrowser, flightlog_hints, gpu_detector, highlights, music, projects, summary
from vidfactory.core import shorts as shorts_engine
from vidfactory.core.concat import build_preview, concatenate, hike_output_duration, plan as concat_plan
from vidfactory.core.ffmpeg_runner import get_runner
from vidfactory.core.jobs import registry
from vidfactory.database.db import get_db, get_engine
from vidfactory.database.models import Highlight, Project, Short, User
from vidfactory.models.highlight import HighlightIn, HighlightOut

logger = logging.getLogger(__name__)


def _build_conflict(kind: str) -> JSONResponse:
    # Every build kind writes to a deterministic, project-scoped output path (e.g.
    # summary_output_path() is the same file for every summary build of a given project) --
    # running two of the same kind concurrently interleaves both processes' writes into one
    # corrupted output with neither side reporting an error. Block the second request instead.
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "CONFLICT",
                "message": f"A {kind} build is already running for this project.",
                "details": {"kind": kind},
            }
        },
    )

router = APIRouter(dependencies=[Depends(require_user)])


def _music_entries() -> list[dict]:
    """Folders and audio files under the music root, as {label, path} (absolute)."""
    root = get_config().mount_roots()["music"]
    try:
        listing = filebrowser.list_dir("music", "")
    except filebrowser.PathNotAllowed:
        return []
    out = []
    for e in listing["entries"]:
        if e["kind"] == "dir":
            out.append({"label": f"📁 {e['name']} (folder)", "path": str(root / e["rel"])})
        elif e["kind"] == "audio":
            out.append({"label": f"🎵 {e['name']}", "path": str(root / e["rel"])})
    return out


def _redirect(project_id: int) -> Response:
    return Response(status_code=204, headers={"HX-Redirect": f"/projects/{project_id}"})


@router.post("/projects/new", include_in_schema=False)
async def create_project(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    form = await request.form()
    date_str = str(form.get("date") or "")
    project_date = datetime.date.fromisoformat(date_str) if date_str else None
    pilot_name = str(form.get("pilot_name") or "").strip() or None
    project = projects.create_project(db, user.id, date=project_date, pilot_name=pilot_name)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", include_in_schema=False)
def project_page(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    shorts = projects.prune_missing_shorts(db, project)
    # Summary/FullFlight+music never persisted which tracks they used, so their pasteable text
    # comes from the sibling credits file a build writes. Shorts DO persist it (segments_used),
    # so theirs is rebuilt straight from the DB — see core/music.build_credits_text.
    short_credits = {
        s.id: text
        for s in shorts
        if (text := music.build_credits_text(music.normalize_music_credits(s.segments_used.get("music"))))
    }
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "project": project,
            "parts": projects.ordered_parts(project),
            "hike": project.hike,
            "music_entries": _music_entries(),
            "music_defaults": get_config().music,
            "highlight_count": len(project.highlights),
            "make_short_count": sum(1 for h in project.highlights if h.make_short),
            "shorts": shorts,
            "short_credits": short_credits,
            "summary_credits": projects.read_credits_text(project.summary_file),
            "fullmusic_credits": projects.read_credits_text(project.fullflight_music_file),
        },
    )


@router.get("/projects/{project_id}/editor", include_in_schema=False)
def editor_page(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return RedirectResponse(f"/projects/{project_id}", status_code=303)
    has_preview = bool(project.preview_file and Path(project.preview_file).exists())
    return templates.TemplateResponse(
        request,
        "editor.html",
        {"project": project, "has_preview": has_preview},
    )


@router.get("/api/projects/{project_id}/fullflight/video", include_in_schema=False)
def fullflight_video(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    # Prefer the lightweight 720p proxy for smooth scrubbing; fall back to the 4K source.
    for path in (project.preview_file, project.full_flight_file):
        if path and Path(path).exists():
            return FileResponse(path, media_type="video/mp4")
    return Response(status_code=404)


@router.post("/api/projects/{project_id}/preview/build", include_in_schema=False)
def build_preview_ep(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    if registry.find_active("preview", project_id):
        return _build_conflict("preview")
    job = registry.run("preview", user.id, _preview_target(project_id, user.id), project_id=project_id)
    return {"job_id": job.id}


def _preview_target(project_id: int, owner_id: int):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_owned_project(db, project_id, owner_id)
            cfg = get_config()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            out = projects.preview_output_path(project)
            res = build_preview(
                project.full_flight_file, out, get_runner(), gpu,
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            project.preview_file = res["output"]
            db.commit()
            return res["output"]

    return target


@router.get("/api/projects/{project_id}/flightlog-hints", include_in_schema=False)
def get_flightlog_hints(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Segment-derived timeline hints (thermal starts, etc.) for the highlight editor — see
    core/flightlog_hints.py. `{"status": "no_launch_marked"|"unavailable"|"ok", "hints": [...]}`,
    never an error: the editor treats this as a best-effort overlay, not a hard dependency."""
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    return flightlog_hints.get_hints(db, project)


@router.get("/api/projects/{project_id}/highlights", include_in_schema=False)
def list_highlights(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    rows = [HighlightOut.model_validate(h) for h in highlights.list_highlights(db, project_id)]
    return {"data": rows, "total": len(rows)}


@router.post("/api/projects/{project_id}/highlights", include_in_schema=False)
def create_highlight(project_id: int, payload: HighlightIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    h = highlights.create_highlight(db, project_id, payload.model_dump())
    return HighlightOut.model_validate(h)


@router.post("/api/projects/{project_id}/highlights/picture-upload", include_in_schema=False)
async def upload_highlight_picture(project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    # Registered before the /highlights/{highlight_id} routes below: Starlette matches path
    # patterns before FastAPI converts params, so a literal "picture-upload" segment would
    # otherwise match {highlight_id}: int first and 422 on the failed int conversion.
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in filebrowser.IMAGE_EXT:
        return JSONResponse({"error": f"Unsupported image type: {ext or 'unknown'}"}, status_code=400)
    dest_dir = get_config().uploads_dir_path / str(project_id) / "pictures"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _dedupe_name(dest_dir, Path(file.filename).name)
    data = await file.read()
    dest.write_bytes(data)
    logger.info("Picture highlight image uploaded — project=%s file=%s (%d bytes)", project_id, dest, len(data))
    return JSONResponse({"image_path": str(dest)})


@router.post("/api/projects/{project_id}/highlights/{highlight_id}", include_in_schema=False)
def update_highlight(project_id: int, highlight_id: int, payload: HighlightIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    h = highlights.update_highlight(db, highlight_id, payload.model_dump())
    if h is None:
        return Response(status_code=404)
    return HighlightOut.model_validate(h)


@router.post("/api/projects/{project_id}/highlights/{highlight_id}/delete", include_in_schema=False)
def delete_highlight(project_id: int, highlight_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    highlights.delete_highlight(db, highlight_id)
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/highlights/{highlight_id}/picture", include_in_schema=False)
def highlight_picture(project_id: int, highlight_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    h = db.get(Highlight, highlight_id)
    if h is None or h.project_id != project_id or not h.image_path or not Path(h.image_path).exists():
        return Response(status_code=404)
    return FileResponse(h.image_path)


@router.post("/api/projects/{project_id}/flight-type", include_in_schema=False)
async def set_flight_type(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    projects.set_flight_type(db, project, str(form.get("flight_type", "normal_flight")))
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/flightlog-id", include_in_schema=False)
async def set_flightlog_id(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    project.external_flight_id = str(form.get("external_flight_id") or "").strip() or None
    db.commit()
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/pilot-name", include_in_schema=False)
async def set_pilot_name(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    pilot_name = str(form.get("pilot_name") or "").strip() or None
    projects.set_pilot_name(db, project, pilot_name)
    return _redirect(project_id)


def _staging_dir(project_id: int, *, hike: bool = False) -> Path:
    base = get_config().uploads_dir_path / str(project_id)
    return (base / "hike" / ".staging") if hike else (base / ".staging")


def _dedupe_name(dest_dir: Path, name: str) -> Path:
    """Same collision handling as the old whole-file endpoints — checked again at finish
    time (not just at begin), since hours can pass between the two for a large upload."""
    dest = dest_dir / name
    if dest.exists():
        dest = dest_dir / f"{dest.stem}_{uuid.uuid4().hex[:8]}{dest.suffix}"
    return dest


# Chunked upload: `begin` (find/create a staging slot, report bytes already received),
# `chunk` (append one piece at a known offset), `finish` (verify + move into place). See
# core/chunked_upload.py docstring for why — this replaced a single-request whole-file
# upload that a 30-minute Traefik entrypoint readTimeout would kill on any large file.
@router.post("/api/projects/{project_id}/parts/upload/begin", include_in_schema=False)
async def begin_part_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    body = await request.json()
    try:
        filename, offset = chunked_upload.begin(_staging_dir(project_id), str(body.get("filename") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"filename": filename, "offset": offset})


@router.put("/api/projects/{project_id}/parts/upload/chunk", include_in_schema=False)
async def chunk_part_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    filename = request.query_params.get("filename", "")
    try:
        offset = int(request.query_params.get("offset", "-1"))
    except ValueError:
        return JSONResponse({"error": "invalid offset"}, status_code=400)
    try:
        new_size = await chunked_upload.append_chunk(_staging_dir(project_id), filename, offset, request.stream())
    except chunked_upload.OffsetMismatch as exc:
        return JSONResponse({"offset": exc.actual_offset}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"offset": new_size})


@router.post("/api/projects/{project_id}/parts/upload/finish", include_in_schema=False)
async def finish_part_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    body = await request.json()
    filename = str(body.get("filename") or "")
    try:
        total_size = int(body.get("total_size", -1))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid total_size"}, status_code=400)
    try:
        staged = chunked_upload.finish(_staging_dir(project_id), filename, total_size)
    except chunked_upload.OffsetMismatch as exc:
        return JSONResponse({"offset": exc.actual_offset}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    dest_dir = get_config().uploads_dir_path / str(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _dedupe_name(dest_dir, staged.name)
    staged.rename(dest)
    projects.add_parts(db, project, [str(dest)])
    return JSONResponse({"file": str(dest)})


@router.post("/api/projects/{project_id}/parts/{part_id}/move", include_in_schema=False)
async def move_part(project_id: int, part_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    direction = str((await request.form()).get("dir", "up"))
    projects.move_part(db, project, part_id, direction)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/parts/{part_id}/delete", include_in_schema=False)
def delete_part(project_id: int, part_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    projects.remove_part(db, part_id)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/hike/upload/begin", include_in_schema=False)
async def begin_hike_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    body = await request.json()
    try:
        filename, offset = chunked_upload.begin(_staging_dir(project_id, hike=True), str(body.get("filename") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"filename": filename, "offset": offset})


@router.put("/api/projects/{project_id}/hike/upload/chunk", include_in_schema=False)
async def chunk_hike_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if projects.get_owned_project(db, project_id, user.id) is None:
        return Response(status_code=404)
    filename = request.query_params.get("filename", "")
    try:
        offset = int(request.query_params.get("offset", "-1"))
    except ValueError:
        return JSONResponse({"error": "invalid offset"}, status_code=400)
    try:
        new_size = await chunked_upload.append_chunk(_staging_dir(project_id, hike=True), filename, offset, request.stream())
    except chunked_upload.OffsetMismatch as exc:
        return JSONResponse({"offset": exc.actual_offset}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"offset": new_size})


@router.post("/api/projects/{project_id}/hike/upload/finish", include_in_schema=False)
async def finish_hike_upload(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    body = await request.json()
    filename = str(body.get("filename") or "")
    try:
        total_size = int(body.get("total_size", -1))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid total_size"}, status_code=400)
    try:
        speed = float(body.get("speed_factor", 1.0) or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    try:
        staged = chunked_upload.finish(_staging_dir(project_id, hike=True), filename, total_size)
    except chunked_upload.OffsetMismatch as exc:
        return JSONResponse({"offset": exc.actual_offset}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    dest_dir = get_config().uploads_dir_path / str(project_id) / "hike"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _dedupe_name(dest_dir, staged.name)
    staged.rename(dest)
    projects.add_hike_sources(db, project, [str(dest)], speed)
    return JSONResponse({"file": str(dest)})


@router.post("/api/projects/{project_id}/hike/remove", include_in_schema=False)
async def remove_hike_source(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    try:
        index = int((await request.form()).get("index", "-1"))
    except ValueError:
        index = -1
    projects.remove_hike_source(db, project, index)
    return _redirect(project_id)


@router.post("/api/projects/{project_id}/build", include_in_schema=False)
def build_fullflight(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.source_parts:
        return Response("No source parts added.", status_code=400)
    if registry.find_active("concat", project_id):
        return _build_conflict("concat")
    job = registry.run("concat", user.id, _build_target(project_id, user.id), project_id=project_id)
    return {"job_id": job.id}


@router.get("/api/projects/{project_id}/concat-plan", include_in_schema=False)
def concat_plan_partial(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    parts = [p.file for p in projects.ordered_parts(project)]
    hike = project.hike
    use_hike = hike is not None and project.flight_type == "hike_and_fly"
    hike_files = list(hike.sources) if use_hike else None
    speed = hike.speed_factor if use_hike else 1.0
    result = concat_plan(parts, get_runner(), hike_files=hike_files, speed_factor=speed)
    return templates.TemplateResponse(request, "partials/concat_plan.html", {"plan": result})


@router.get("/api/projects/{project_id}/status", include_in_schema=False)
def project_status(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    return {"full_flight_file": project.full_flight_file}


@router.post("/api/projects/{project_id}/summary/build", include_in_schema=False)
async def build_summary_ep(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    form = await request.form()
    target = float(form.get("target_seconds") or 90)
    music_path = str(form.get("music_path") or "")
    mv = float(form.get("music_volume") or 0.35)
    ov = float(form.get("original_volume") or 1.0)
    if registry.find_active("summary", project_id):
        return _build_conflict("summary")
    job = registry.run(
        "summary", user.id, _summary_target(project_id, user.id, target, music_path, mv, ov),
        project_id=project_id,
    )
    return {"job_id": job.id}


@router.post("/api/projects/{project_id}/fullmusic/build", include_in_schema=False)
async def build_fullmusic_ep(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    form = await request.form()
    music_path = str(form.get("music_path") or "")
    if not music_path:
        return Response("Select music.", status_code=400)
    mv = float(form.get("music_volume") or 0.35)
    ov = float(form.get("original_volume") or 1.0)
    if registry.find_active("fullmusic", project_id):
        return _build_conflict("fullmusic")
    job = registry.run(
        "fullmusic", user.id, _fullmusic_target(project_id, user.id, music_path, mv, ov),
        project_id=project_id,
    )
    return {"job_id": job.id}


@router.post("/api/projects/{project_id}/shorts/build", include_in_schema=False)
async def build_shorts_ep(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = projects.get_owned_project(db, project_id, user.id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    form = await request.form()
    mode = str(form.get("mode") or "highlight")
    count = int(form.get("count") or 1)
    music_path = str(form.get("music_path") or "")
    mv = float(form.get("music_volume") or 0.35)
    ov = float(form.get("original_volume") or 1.0)
    cfg = get_config()
    # One task per short to build -- resolved here (cheap: DB only, no ffprobe/ffmpeg) so each
    # becomes its own queued job below, rather than one "shorts" job silently building N videos
    # with no visible sign of how many are left (see M24: the Job Queue page listed exactly one
    # row no matter how many shorts a highlight-driven batch produced).
    if mode == "highlight":
        hls = highlights.list_highlights(db, project_id)
        tasks = [
            ((hl.start, min(hl.end, hl.start + cfg.shorts.hook_max)), hl.name, hl.id)
            for hl in hls if hl.make_short
        ]
        if not tasks:
            return Response("No highlights flagged 'make short'.", status_code=400)
    else:
        tasks = [(None, None, None) for _ in range(max(1, count))]
    logger.info(
        "[VF:shorts] build requested project=%s mode=%s count=%s music_path=%r -> %d short(s) queued",
        project_id, mode, count, music_path, len(tasks),
    )
    if registry.find_active("shorts", project_id):
        return _build_conflict("shorts")
    # Shared across every job below (not per-job state) so the flying/hike-clip de-dup that used
    # to happen within one big loop still holds across the whole batch -- the global queue runs
    # jobs strictly one at a time, so mutating this same dict from each job's target in turn is safe.
    used: shorts_engine.UsedMap = {}
    job_ids = [
        registry.run(
            "shorts", user.id,
            _short_target(project_id, user.id, mode, music_path, mv, ov, hook, title, hid, used),
            project_id=project_id, title=title or "",
        ).id
        for hook, title, hid in tasks
    ]
    return {"job_ids": job_ids}


def _flying_pool(project: Project, full_dur: float, start: float = 0.0) -> list[tuple[float, float]]:
    """Flying-only ranges on the full-flight timeline, clipped to start at `start` (past any
    prepended Hike & Fly segment, so random flying picks never land in the hike footage)."""
    ranges = [(max(p.start, start), p.end) for p in project.pools if p.kind == "flying"]
    ranges = [(s, e) for s, e in ranges if e > s]
    return ranges or [(start, full_dur)]


def _role_clip(hls, role: str, cap: float) -> tuple[float, float] | None:
    for h in hls:
        if h.role == role:
            return (h.start, min(h.end, h.start + cap))
    return None


def _next_short_path(project: Project) -> str:
    from vidfactory.core.projects import _stem
    root = get_config().mount_roots()["output_shorts"]
    root.mkdir(parents=True, exist_ok=True)
    stem = _stem(project)
    nums = [
        int(m.group(1))
        for p in root.glob(f"{stem}_Short_*.mp4")
        if (m := re.search(r"_Short_(\d+)", p.stem))
    ]
    nn = (max(nums) + 1) if nums else 1
    return str(root / f"{stem}_Short_{nn:02d}.mp4")


def _short_target(
    project_id: int, owner_id: int, mode: str, music_path: str, mv: float, ov: float,
    hook: tuple[float, float] | None, title: str | None, hid: int | None,
    used: shorts_engine.UsedMap,
):
    """Builds exactly one short. `used` is shared (by closure) across every task in the same
    batch — see `build_shorts_ep` — so the flying/hike-clip de-dup across a batch's shorts still
    works even though each is now its own queued job rather than one loop inside a single job.
    Highlights/pool/launch/landing are re-fetched fresh here rather than passed in from the
    request, since a batch can sit queued a while (behind other users' jobs) before this runs."""
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_owned_project(db, project_id, owner_id)
            full = project.full_flight_file
            cfg = get_config()
            sc = cfg.shorts
            runner = get_runner()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            w, h, dur = runner.get_video_info(full)
            hls = highlights.list_highlights(db, project_id)
            launch = _role_clip(hls, "launch", sc.launch_max)
            landing = _role_clip(hls, "landing", sc.landing_max)

            hike = project.hike
            use_hike = bool(hike and hike.sources and project.flight_type == "hike_and_fly")
            hike_end = 0.0
            hike_pool: list[tuple[float, float]] = []
            if use_hike:
                hike_end = max(0.0, min(hike_output_duration(list(hike.sources), hike.speed_factor, runner), dur))
                hike_pool = [(0.0, hike_end)]
                # launch/landing are user-marked and bypass the flying pool entirely (never
                # de-duped against `used`, never clamped here) — if a pilot marked launch while
                # still inside the sped-up hike segment, the "launch" clip is actually hike
                # footage. Not our call to silently clamp a user's mark; just make it diagnosable.
                if launch and launch[0] < hike_end:
                    logger.warning(
                        "[VF:shorts] project=%s launch highlight starts at %.1fs, before the "
                        "hike segment ends at %.1fs — the 'launch' clip in this short will "
                        "actually be hike footage",
                        project_id, launch[0], hike_end,
                    )

            pool = _flying_pool(project, dur, start=hike_end)

            job.set_stage("Picking clips")
            hike_clips = (
                shorts_engine.pick_clips(hike_pool, sc.hike_clip_count, sc.clip_duration, used, full)
                if use_hike else []
            )
            flying = shorts_engine.pick_clips(pool, sc.flying_clip_count, sc.clip_duration, used, full)
            if not flying:
                raise ValueError("Flying pool exhausted — not enough unused footage.")
            clips = (
                ([hook] if hook else [])
                + hike_clips
                + ([launch] if launch else [])
                + flying
                + ([landing] if landing else [])
            )
            total_needed = sum(e - s for s, e in clips) + sc.cta_duration
            bed, tracks = (None, [])
            if music_path:
                job.set_stage("Preparing music")
                bed, tracks = music.prepare_music_bed(
                    music_path, total_needed, Path(cfg.data_dir) / "tmp", runner,
                    Path(cfg.data_dir) / "music_cache",
                )
            out = _next_short_path(project)

            job.set_stage("Encoding short")
            res = shorts_engine.build_short(
                full, clips, out, runner, gpu, src_w=w, src_h=h,
                cta_image=str(cfg.cta_image), cta_duration=sc.cta_duration,
                cta_line1=sc.cta_line1, cta_line2=sc.cta_line2,
                video_bitrate=sc.video_bitrate, audio_bitrate=sc.audio_bitrate,
                music_bed=bed, music_volume=mv, original_volume=ov,
                progress_cb=job.set_progress, cancel_event=job.cancel_event,
            )
            # Resolved once here (title/artist tags) and reused for both the sibling credits
            # file and the DB record, so the project page never has to re-probe ffprobe just
            # to show the pasteable text (see core/music.resolve_track_credits).
            music_credits = music.resolve_track_credits(tracks, runner) if tracks else []
            if music_credits:
                music.write_credits(music_credits, projects.credits_path_for(out))
            db.add(Short(
                project_id=project_id, output_file=res["output"], short_type=mode,
                duration=res["duration"], source_highlight_id=hid, title=title,
                segments_used={"hook": hook, "hike": hike_clips, "launch": launch, "flying": flying,
                               "landing": landing, "music": music_credits},
            ))
            db.commit()
            if bed:
                Path(bed).unlink(missing_ok=True)
            return res["output"]

    return target


def _probe_fps(runner, file: str) -> str:
    data = runner.probe(file)
    streams = data.get("streams", [])
    return (streams[0].get("r_frame_rate") if streams else None) or "30"


def _summary_target(project_id: int, owner_id: int, target_seconds: float, music_path: str, mv: float, ov: float):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_owned_project(db, project_id, owner_id)
            full = project.full_flight_file
            cfg = get_config()
            runner = get_runner()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            w, h, full_dur = runner.get_video_info(full)
            fps = _probe_fps(runner, full)
            hls = [hl for hl in highlights.list_highlights(db, project_id) if hl.use_in_summary]
            segs = highlights.merge_overlaps(hls)
            segs = summary.auto_fill(segs, target_seconds, full_dur)
            # +cta_duration: the CTA end screen rides along at the end of every summary (see
            # build_summary), so the music bed needs to be long enough to still be playing then.
            total = sum(summary._seg_duration(s) for s in segs) + cfg.shorts.cta_duration
            bed, tracks = (None, [])
            if music_path:
                job.set_stage("Selecting music")
                bed, tracks = music.prepare_music_bed(
                    music_path, total, Path(cfg.data_dir) / "tmp", runner,
                    Path(cfg.data_dir) / "music_cache",
                )
            out = projects.summary_output_path(project)
            res = summary.build_summary(
                full, segs, out, runner, gpu, width=w, height=h, fps=fps,
                video_bitrate=cfg.encode.video_bitrate, audio_bitrate=cfg.encode.audio_bitrate,
                music_bed=bed, music_volume=mv, original_volume=ov,
                cta_image=str(cfg.cta_image), cta_duration=cfg.shorts.cta_duration,
                cta_line1=cfg.shorts.cta_line1, cta_line2=cfg.shorts.cta_line2,
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            if tracks:
                music.write_credits(music.resolve_track_credits(tracks, runner), projects.credits_path_for(out))
            project.summary_file = res["output"]
            db.commit()
            if bed:
                Path(bed).unlink(missing_ok=True)
            return res["output"]

    return target


def _fullmusic_target(project_id: int, owner_id: int, music_path: str, mv: float, ov: float):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_owned_project(db, project_id, owner_id)
            full = project.full_flight_file
            cfg = get_config()
            runner = get_runner()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            full_dur = runner.get_video_info(full)[2]
            job.set_stage("Selecting music")
            # +cta_duration: same reason as _summary_target — the CTA end screen is appended
            # after the music-mixed full flight, so the bed needs to cover it too.
            bed, tracks = music.prepare_music_bed(
                music_path, full_dur + cfg.shorts.cta_duration, Path(cfg.data_dir) / "tmp", runner,
                Path(cfg.data_dir) / "music_cache",
            )
            if not bed:
                raise ValueError("No playable music found at the selected path.")
            out = projects.fullmusic_output_path(project)
            res = summary.build_fullflight_with_music(
                full, out, bed, runner, gpu, music_volume=mv, original_volume=ov,
                audio_bitrate=cfg.encode.audio_bitrate, video_bitrate=cfg.encode.video_bitrate,
                cta_image=str(cfg.cta_image), cta_duration=cfg.shorts.cta_duration,
                cta_line1=cfg.shorts.cta_line1, cta_line2=cfg.shorts.cta_line2,
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            music.write_credits(music.resolve_track_credits(tracks, runner), projects.credits_path_for(out))
            project.fullflight_music_file = res["output"]
            db.commit()
            Path(bed).unlink(missing_ok=True)
            return res["output"]

    return target


def _build_target(project_id: int, owner_id: int):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_owned_project(db, project_id, owner_id)
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
            # Auto-build the 720p editor proxy in the background once the full flight exists.
            registry.run("preview", owner_id, _preview_target(project_id, owner_id), project_id=project_id)
            return result["output"]

    return target
