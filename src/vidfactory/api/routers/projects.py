from __future__ import annotations

import datetime
import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from vidfactory.api.templating import templates
from vidfactory.config import get_config
import re

from vidfactory.core import filebrowser, gpu_detector, highlights, music, projects, summary
from vidfactory.core import shorts as shorts_engine
from vidfactory.core.concat import build_preview, concatenate, plan as concat_plan
from vidfactory.core.ffmpeg_runner import get_runner
from vidfactory.core.jobs import registry
from vidfactory.database.db import get_db, get_engine
from vidfactory.database.models import Project, Short
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
async def create_project(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    date_str = str(form.get("date") or "")
    project_date = datetime.date.fromisoformat(date_str) if date_str else None
    project = projects.create_project(db, date=project_date)
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
            "parts": projects.ordered_parts(project),
            "hike": project.hike,
            "instaout_files": _video_files(),
            "videos_root": str(videos_root),
            "music_entries": _music_entries(),
            "music_defaults": get_config().music,
            "highlight_count": len(project.highlights),
            "make_short_count": sum(1 for h in project.highlights if h.make_short),
            "shorts": sorted(project.shorts, key=lambda s: s.created_at, reverse=True),
        },
    )


@router.get("/projects/{project_id}/editor", include_in_schema=False)
def editor_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
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
def fullflight_video(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        return Response(status_code=404)
    # Prefer the lightweight 720p proxy for smooth scrubbing; fall back to the 4K source.
    for path in (project.preview_file, project.full_flight_file):
        if path and Path(path).exists():
            return FileResponse(path, media_type="video/mp4")
    return Response(status_code=404)


@router.post("/api/projects/{project_id}/preview/build", include_in_schema=False)
def build_preview_ep(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    job = registry.run("preview", _preview_target(project_id))
    return {"job_id": job.id}


def _preview_target(project_id: int):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_project(db, project_id)
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


_UPLOAD_CHUNK = 8 * 1024 * 1024


@router.post("/api/projects/{project_id}/parts/upload", include_in_schema=False)
async def upload_parts(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    form = await request.form()
    files = [f for f in form.getlist("files") if hasattr(f, "filename") and f.filename]
    if not files:
        return Response("No files selected.", status_code=400)
    dest_dir = get_config().uploads_dir_path / str(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for f in files:
        name = Path(f.filename).name  # strip any directory components from the client
        dest = dest_dir / name
        if dest.exists():
            dest = dest_dir / f"{dest.stem}_{uuid.uuid4().hex[:8]}{dest.suffix}"
        with dest.open("wb") as out:
            while chunk := await f.read(_UPLOAD_CHUNK):
                out.write(chunk)
        saved.append(str(dest))
    projects.add_parts(db, project, saved)
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


@router.get("/api/projects/{project_id}/concat-plan", include_in_schema=False)
def concat_plan_partial(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
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
def project_status(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        return Response(status_code=404)
    return {"full_flight_file": project.full_flight_file}


@router.post("/api/projects/{project_id}/summary/build", include_in_schema=False)
async def build_summary_ep(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
    if project is None:
        return Response(status_code=404)
    if not project.full_flight_file:
        return Response("Build the full flight first.", status_code=400)
    form = await request.form()
    target = float(form.get("target_seconds") or 90)
    music_path = str(form.get("music_path") or "")
    mv = float(form.get("music_volume") or 0.35)
    ov = float(form.get("original_volume") or 1.0)
    job = registry.run("summary", _summary_target(project_id, target, music_path, mv, ov))
    return {"job_id": job.id}


@router.post("/api/projects/{project_id}/fullmusic/build", include_in_schema=False)
async def build_fullmusic_ep(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
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
    job = registry.run("fullmusic", _fullmusic_target(project_id, music_path, mv, ov))
    return {"job_id": job.id}


@router.post("/api/projects/{project_id}/shorts/build", include_in_schema=False)
async def build_shorts_ep(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = projects.get_project(db, project_id)
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
    job = registry.run("shorts", _shorts_target(project_id, mode, count, music_path, mv, ov))
    return {"job_id": job.id}


def _flying_pool(project: Project, full_dur: float) -> list[tuple[float, float]]:
    ranges = [(p.start, p.end) for p in project.pools if p.kind == "flying"]
    return ranges or [(0.0, full_dur)]


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


def _shorts_target(project_id: int, mode: str, count: int, music_path: str, mv: float, ov: float):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_project(db, project_id)
            full = project.full_flight_file
            cfg = get_config()
            sc = cfg.shorts
            runner = get_runner()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            w, h, dur = runner.get_video_info(full)
            hls = highlights.list_highlights(db, project_id)
            launch = _role_clip(hls, "launch", sc.launch_max)
            landing = _role_clip(hls, "landing", sc.landing_max)
            pool = _flying_pool(project, dur)
            used: shorts_engine.UsedMap = {}

            if mode == "highlight":
                tasks = [
                    ((hl.start, min(hl.end, hl.start + sc.hook_max)), hl.name, hl.id)
                    for hl in hls if hl.make_short
                ]
                if not tasks:
                    raise ValueError("No highlights flagged 'make short'.")
            else:
                tasks = [(None, None, None) for _ in range(max(1, count))]

            built = []
            n = len(tasks)
            for i, (hook, title, hid) in enumerate(tasks):
                if job.cancel_event.is_set():
                    break
                job.set_stage(f"Short {i + 1}/{n}")
                flying = shorts_engine.pick_clips(pool, sc.flying_clip_count, sc.clip_duration, used, full)
                clips = ([hook] if hook else []) + ([launch] if launch else []) + flying + ([landing] if landing else [])
                if not flying:
                    raise ValueError("Flying pool exhausted — not enough unused footage.")
                total_needed = sum(e - s for s, e in clips) + sc.cta_duration
                bed, tracks = (None, [])
                if music_path:
                    bed, tracks = music.prepare_music_bed(
                        music_path, total_needed, Path(cfg.data_dir) / "tmp", runner,
                        Path(cfg.data_dir) / "music_cache",
                    )
                out = _next_short_path(project)

                def scaled(frac, spd, _i=i, _n=n):
                    job.set_progress((_i + frac) / _n, spd)

                res = shorts_engine.build_short(
                    full, clips, out, runner, gpu, src_w=w, src_h=h,
                    cta_image=str(cfg.cta_image), cta_duration=sc.cta_duration,
                    cta_line1=sc.cta_line1, cta_line2=sc.cta_line2,
                    video_bitrate=sc.video_bitrate, audio_bitrate=sc.audio_bitrate,
                    music_bed=bed, music_volume=mv, original_volume=ov,
                    progress_cb=scaled, cancel_event=job.cancel_event,
                )
                db.add(Short(
                    project_id=project_id, output_file=res["output"], short_type=mode,
                    duration=res["duration"], source_highlight_id=hid, title=title,
                    segments_used={"hook": hook, "launch": launch, "flying": flying,
                                   "landing": landing, "music": (tracks[0] if tracks else None)},
                ))
                db.commit()
                if bed:
                    Path(bed).unlink(missing_ok=True)
                built.append(res["output"])
            return f"{len(built)} short(s)"

    return target


def _probe_fps(runner, file: str) -> str:
    data = runner.probe(file)
    streams = data.get("streams", [])
    return (streams[0].get("r_frame_rate") if streams else None) or "30"


def _summary_target(project_id: int, target_seconds: float, music_path: str, mv: float, ov: float):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_project(db, project_id)
            full = project.full_flight_file
            cfg = get_config()
            runner = get_runner()
            gpu = gpu_detector.detect(cfg.ffmpeg.ffmpeg_path)
            w, h, full_dur = runner.get_video_info(full)
            fps = _probe_fps(runner, full)
            hls = [hl for hl in highlights.list_highlights(db, project_id) if hl.use_in_summary]
            segs = highlights.merge_overlaps(hls)
            segs = summary.auto_fill(segs, target_seconds, full_dur)
            total = sum(summary._seg_duration(s) for s in segs)
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
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            if tracks:
                music.write_credits(tracks, projects.credits_path_for(out), runner)
            project.summary_file = res["output"]
            db.commit()
            if bed:
                Path(bed).unlink(missing_ok=True)
            return res["output"]

    return target


def _fullmusic_target(project_id: int, music_path: str, mv: float, ov: float):
    def target(job):
        with Session(get_engine()) as db:
            project = projects.get_project(db, project_id)
            full = project.full_flight_file
            cfg = get_config()
            runner = get_runner()
            full_dur = runner.get_video_info(full)[2]
            job.set_stage("Selecting music")
            bed, tracks = music.prepare_music_bed(
                music_path, full_dur, Path(cfg.data_dir) / "tmp", runner,
                Path(cfg.data_dir) / "music_cache",
            )
            if not bed:
                raise ValueError("No playable music found at the selected path.")
            out = projects.fullmusic_output_path(project)
            res = summary.build_fullflight_with_music(
                full, out, bed, runner, music_volume=mv, original_volume=ov,
                audio_bitrate=cfg.encode.audio_bitrate,
                progress_cb=job.set_progress, stage_cb=job.set_stage, cancel_event=job.cancel_event,
            )
            music.write_credits(tracks, projects.credits_path_for(out), runner)
            project.fullflight_music_file = res["output"]
            db.commit()
            Path(bed).unlink(missing_ok=True)
            return res["output"]

    return target


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
            # Auto-build the 720p editor proxy in the background once the full flight exists.
            registry.run("preview", _preview_target(project_id))
            return result["output"]

    return target
