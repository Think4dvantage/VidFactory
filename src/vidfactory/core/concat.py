"""Concatenate ordered source parts into one full-flight video.

Normal flight: parts are stream-copied via the concat demuxer (fast, lossless) — Insta360 30-min
splits are homogeneous. Hike & Fly: the hiking footage is prepended. If it's already time-lapsed on
delivery (the usual case, speed_factor == 1.0) it is copied as-is; otherwise it is re-encoded with a
setpts/atempo speed-up (NVENC) to match the flight before the stream-copy concat.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable, Optional

from vidfactory.core.ffmpeg_runner import FFmpegRunner
from vidfactory.core.gpu_detector import GPUConfig

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float, str], None]]
StageCb = Optional[Callable[[str], None]]


def _atempo_chain(speed: float) -> str:
    """atempo is limited to [0.5, 2.0] per instance; chain to reach `speed`."""
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-3:
        factors.append(remaining)
    return ",".join(f"atempo={f:g}" for f in factors) or "atempo=1.0"


def _scaled_progress(cb: ProgressCb, lo: float, hi: float) -> ProgressCb:
    if cb is None:
        return None
    return lambda frac, speed: cb(lo + (hi - lo) * frac, speed)


def _concat_copy(
    files: list[str],
    output: str,
    total_duration: float,
    runner: FFmpegRunner,
    progress_cb: ProgressCb,
    cancel_event,
) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lst:
        for f in files:
            safe = str(f).replace("'", "'\\''")
            lst.write(f"file '{safe}'\n")
        list_path = lst.name
    try:
        # No -movflags +faststart: on multi-GB stream-copy it triggers a second full-file
        # rewrite (slow over NFS, no progress) that made the bar appear stuck near 100%.
        args = ["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output, "-y"]
        runner.encode(args, total_duration, progress_cb, cancel_event)
    finally:
        Path(list_path).unlink(missing_ok=True)


def _encode_sped_hike(
    hike_files: list[str],
    speed_factor: float,
    target_w: int,
    target_h: int,
    target_fps: str,
    out_path: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    video_bitrate: str,
    audio_bitrate: str,
    total_out_duration: float,
    progress_cb: ProgressCb,
    cancel_event,
) -> None:
    """Speed up hiking footage and normalize it to the flight's spec (so the later concat can copy)."""
    inputs: list[str] = []
    for f in hike_files:
        inputs += ["-i", f]
    n = len(hike_files)
    vfilter = (
        f"[0:v]" if n == 1 else "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[hv];[hv]"
    )
    afilter = (
        f"[0:a]" if n == 1 else "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[ha];[ha]"
    )
    fc = (
        f"{vfilter}setpts=(1/{speed_factor})*PTS,"
        f"scale={target_w}:{target_h},setsar=1,fps={target_fps},format=yuv420p[v];"
        f"{afilter}{_atempo_chain(speed_factor)},aresample=async=1[a]"
    )
    args = (
        inputs
        + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
        + gpu.encoding_args(video_bitrate)
        + ["-c:a", "aac", "-b:a", audio_bitrate, out_path, "-y"]
    )
    runner.encode(args, total_out_duration, progress_cb, cancel_event)


def hike_output_duration(hike_files: list[str], speed_factor: float, runner: FFmpegRunner) -> float:
    """Seconds the (optionally sped-up) hike segment occupies on the full-flight timeline.

    Mirrors the speed-up math `concatenate()` uses when prepending it, so other callers (the
    shorts builder needs the hike/flying boundary) don't have to re-derive the formula.
    """
    raw = sum(runner.get_video_info(f)[2] for f in hike_files)
    needs_speed = abs(speed_factor - 1.0) > 1e-3
    return (raw / speed_factor) if needs_speed else raw


def concatenate(
    parts: list[str],
    output: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    *,
    hike_files: list[str] | None = None,
    speed_factor: float = 1.0,
    video_bitrate: str = "20M",
    audio_bitrate: str = "192k",
    progress_cb: ProgressCb = None,
    stage_cb: StageCb = None,
    cancel_event=None,
) -> dict:
    if not parts:
        raise ValueError("No source parts to concatenate.")
    for f in parts + (hike_files or []):
        if not Path(f).exists():
            raise FileNotFoundError(f"Source file not found: {f}")

    Path(output).parent.mkdir(parents=True, exist_ok=True)

    part_dur = sum(runner.get_video_info(p)[2] for p in parts)
    hike_in_dur = sum(runner.get_video_info(h)[2] for h in (hike_files or []))
    needs_speed = bool(hike_files) and abs(speed_factor - 1.0) > 1e-3
    hike_out_dur = (hike_in_dur / speed_factor) if needs_speed else hike_in_dur
    total = part_dur + hike_out_dur

    concat_files: list[str] = []
    tmp_hike: str | None = None
    try:
        if hike_files and needs_speed:
            if stage_cb:
                stage_cb("Speeding up hike")
            w, h, _ = runner.get_video_info(parts[0])
            fps = _probe_fps(runner, parts[0])
            tmp_hike = str(Path(output).with_suffix("")) + "_hike_tmp.mp4"
            _encode_sped_hike(
                hike_files, speed_factor, w, h, fps, tmp_hike, runner, gpu,
                video_bitrate, audio_bitrate, hike_out_dur,
                _scaled_progress(progress_cb, 0.0, 0.4), cancel_event,
            )
            concat_files.append(tmp_hike)
            concat_cb = _scaled_progress(progress_cb, 0.4, 1.0)
        else:
            if hike_files:
                concat_files.extend(hike_files)
            concat_cb = progress_cb

        concat_files.extend(parts)
        if stage_cb:
            stage_cb("Concatenating")
        _concat_copy(concat_files, output, total, runner, concat_cb, cancel_event)
    finally:
        if tmp_hike:
            Path(tmp_hike).unlink(missing_ok=True)

    if stage_cb:
        stage_cb("Finalizing")
    duration = runner.get_video_info(output)[2]
    logger.info("Full flight built: %s (%.1fs)", output, duration)
    return {"output": output, "duration": duration}


def _safe_duration(runner: FFmpegRunner, file: str) -> tuple[float, str]:
    """(seconds, note) for one file. note is '' when ok, else 'missing'/'unreadable'."""
    if not Path(file).exists():
        logger.warning("Concat plan: source missing — %s", file)
        return 0.0, "missing"
    try:
        return runner.get_video_info(file)[2], ""
    except Exception as exc:  # noqa: BLE001 - degrade gracefully in the UI, never 500 the page
        logger.warning("Concat plan: ffprobe failed for %s: %s", file, exc)
        return 0.0, "unreadable"


def plan(
    parts: list[str],
    runner: FFmpegRunner,
    *,
    hike_files: list[str] | None = None,
    speed_factor: float = 1.0,
) -> dict:
    """Preview of what `concatenate` will produce: ordered items with durations + grand total.

    Mirrors the real build order — for Hike & Fly the (optionally sped-up) hike is prepended.
    Probes each source on demand; missing/unreadable files are flagged, not fatal.
    """
    rows: list[dict] = []
    total = 0.0
    has_issue = False

    needs_speed = bool(hike_files) and abs(speed_factor - 1.0) > 1e-3
    if hike_files:
        raw = 0.0
        for f in hike_files:
            d, note = _safe_duration(runner, f)
            raw += d
            has_issue = has_issue or bool(note)
        out_dur = (raw / speed_factor) if needs_speed else raw
        note = (
            f"{len(hike_files)} file(s), {_hms_label(raw)} raw → ×{speed_factor:g}"
            if needs_speed else f"{len(hike_files)} file(s)"
        )
        rows.append({"label": "Hike (prepended)", "seconds": out_dur, "note": note, "kind": "hike"})
        total += out_dur

    for f in parts:
        d, note = _safe_duration(runner, f)
        has_issue = has_issue or bool(note)
        rows.append({"label": Path(f).name, "seconds": d, "note": note, "kind": "part"})
        total += d

    logger.info("Concat plan: %d item(s), total %.1fs%s", len(rows), total,
                " (has issues)" if has_issue else "")
    return {"rows": rows, "total_seconds": total, "part_count": len(parts),
            "has_hike": bool(hike_files), "has_issue": has_issue}


def _hms_label(value: float) -> str:
    total = int(round(value or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _probe_fps(runner: FFmpegRunner, file: str) -> str:
    data = runner.probe(file)
    streams = data.get("streams", [])
    rate = streams[0].get("r_frame_rate") if streams else None
    return rate or "30"


def build_preview(
    full_flight: str,
    output: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    *,
    height: int = 720,
    video_bitrate: str = "3M",
    audio_bitrate: str = "128k",
    progress_cb: ProgressCb = None,
    stage_cb: StageCb = None,
    cancel_event=None,
) -> dict:
    """Low-res proxy for smooth browser scrubbing: 720p, short GOP (snappy seeking), faststart.

    Same duration/timeline as the full flight, so highlights marked on the proxy map 1:1 to the 4K source.
    """
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    total = runner.get_video_info(full_flight)[2]
    if stage_cb:
        stage_cb("Building 720p preview")
    args = (
        ["-i", full_flight, "-vf", f"scale=-2:{height}"]
        + gpu.encoding_args(video_bitrate)
        + ["-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate,
           "-movflags", "+faststart", output, "-y"]
    )
    runner.encode(args, total, progress_cb, cancel_event)
    duration = runner.get_video_info(output)[2]
    logger.info("Preview proxy built: %s (%.1fs)", output, duration)
    return {"output": output, "duration": duration}
