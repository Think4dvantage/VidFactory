"""Summary engine: merge highlights, auto-fill to a target length, and encode a single-pass
filter_complex summary (name/comment overlays, picture highlights, optional music). Also the
full-flight-with-music overlay.

Ported from PS_VidAggregator (VideoSummaryCreator.ps1 / AddMusicToVideo.ps1). Overlay text is passed
via drawtext `textfile=` to avoid filtergraph escaping issues with arbitrary (German) comments.
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path
from typing import Callable, Optional

from vidfactory.core.ffmpeg_runner import FFmpegRunner
from vidfactory.core.gpu_detector import GPUConfig

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float, str], None]]
StageCb = Optional[Callable[[str], None]]


def _seg_duration(seg: dict) -> float:
    if seg["type"] == "picture":
        return float(seg.get("duration") or 5.0)
    return float(seg["end"] - seg["start"])


def auto_fill(segments: list[dict], target_seconds: float, full_duration: float,
              filler_len: float = 5.0) -> list[dict]:
    """Add evenly-distributed, non-overlapping filler video segments until ~target_seconds."""
    total = sum(_seg_duration(s) for s in segments)
    if total >= target_seconds or full_duration <= 0:
        return sorted(segments, key=lambda s: s["start"])

    video_ranges = [(s["start"], s["end"]) for s in segments if s["type"] == "video"]

    def overlaps(a: float, b: float) -> bool:
        return any(a < r_end and b > r_start for r_start, r_end in video_ranges)

    need = target_seconds - total
    n = max(1, math.ceil(need / filler_len))
    added = 0
    for i in range(n * 3):  # oversample slots; stop once enough non-overlapping ones land
        if added >= n:
            break
        pos = ((i + 0.5) / (n * 3)) * max(full_duration - filler_len, 0.0)
        start, end = pos, min(pos + filler_len, full_duration)
        if end - start < 1.0 or overlaps(start, end):
            continue
        segments.append({"name": "", "comment": None, "start": start, "end": end,
                         "role": "normal", "type": "video"})
        video_ranges.append((start, end))
        added += 1
    return sorted(segments, key=lambda s: s["start"])


def _overlay_text(seg: dict) -> str | None:
    text = (seg.get("comment") or "").strip() or (seg.get("name") or "").strip()
    return text or None


def _drawtext(textfile: str, height: int) -> str:
    fs = max(24, height // 24)
    return (
        f"drawtext=textfile='{textfile}':expansion=none:fontcolor=white:fontsize={fs}"
        f":borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-text_h-40:font=Arial"
    )


def _cta_drawtext(tf1: str, tf2: str, height: int) -> str:
    """Two centred lines for the CTA end screen (same copy as `shorts.build_short`'s CTA — see
    `config.ShortsSection.cta_line1/cta_line2` — but sized relative to `height` rather than
    shorts' fixed 1920px-tall vertical frame, since Summary/FullFlight+music vary in resolution)."""
    fs1 = max(28, height // 24)
    fs2 = max(34, height // 18)
    d1 = (f"drawtext=textfile='{tf1}':expansion=none:fontcolor=white:fontsize={fs1}"
          f":borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.55:font=Arial")
    d2 = (f"drawtext=textfile='{tf2}':expansion=none:fontcolor=white:fontsize={fs2}"
          f":borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.55+{fs1 + 40}:font=Arial")
    return f"{d1},{d2}"


def _scaled_progress(cb: ProgressCb, lo: float, hi: float) -> ProgressCb:
    if cb is None:
        return None
    return lambda frac, speed: cb(lo + (hi - lo) * frac, speed)


def build_summary(
    full_flight: str,
    segments: list[dict],
    output: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    *,
    width: int,
    height: int,
    fps: str,
    video_bitrate: str = "20M",
    audio_bitrate: str = "192k",
    music_bed: str | None = None,
    music_volume: float = 0.35,
    original_volume: float = 1.0,
    cta_image: str,
    cta_duration: float,
    cta_line1: str,
    cta_line2: str,
    progress_cb: ProgressCb = None,
    stage_cb: StageCb = None,
    cancel_event=None,
) -> dict:
    if not segments:
        raise ValueError("No segments to summarize.")
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    inputs: list[str] = ["-i", full_flight]
    next_idx = 1
    parts: list[str] = []
    concat_labels: list[str] = []
    tmp_text: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="vf_sum_")

    for i, seg in enumerate(segments):
        v_lbl, a_lbl = f"v{i}", f"a{i}"
        overlay = _overlay_text(seg)
        dt = ""
        if overlay:
            tf = str(Path(tmp_dir) / f"t{i}.txt")
            Path(tf).write_text(overlay, encoding="utf-8")
            tmp_text.append(tf)
            dt = "," + _drawtext(tf, height)

        if seg["type"] == "picture":
            dur = _seg_duration(seg)
            inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", seg["image_path"]]
            img_idx = next_idx
            next_idx += 1
            inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                       "anullsrc=channel_layout=stereo:sample_rate=48000"]
            sil_idx = next_idx
            next_idx += 1
            parts.append(
                f"[{img_idx}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p{dt}[{v_lbl}]"
            )
            parts.append(f"[{sil_idx}:a]aresample=async=1[{a_lbl}]")
        else:
            s, e = seg["start"], seg["end"]
            parts.append(
                f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,setsar=1{dt}[{v_lbl}]"
            )
            parts.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[{a_lbl}]")
        concat_labels += [f"[{v_lbl}]", f"[{a_lbl}]"]

    n = len(segments)

    # CTA end screen — every produced video should end on it (see config.ShortsSection /
    # config.Config.cta_image), appended here as one more concat input so it rides through the
    # same single-pass encode as the rest (and so the music mix below, which runs on the combined
    # [outa], keeps playing under it — matching how shorts.build_short already does this).
    cta_v, cta_a = f"v{n}", f"a{n}"
    inputs += ["-loop", "1", "-t", f"{cta_duration:.3f}", "-i", cta_image]
    img_idx = next_idx
    next_idx += 1
    inputs += ["-f", "lavfi", "-t", f"{cta_duration:.3f}", "-i",
               "anullsrc=channel_layout=stereo:sample_rate=48000"]
    sil_idx = next_idx
    next_idx += 1
    tf1, tf2 = str(Path(tmp_dir) / "cta1.txt"), str(Path(tmp_dir) / "cta2.txt")
    Path(tf1).write_text(cta_line1, encoding="utf-8")
    Path(tf2).write_text(cta_line2, encoding="utf-8")
    tmp_text += [tf1, tf2]
    parts.append(
        f"[{img_idx}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p,"
        f"{_cta_drawtext(tf1, tf2, height)}[{cta_v}]"
    )
    parts.append(f"[{sil_idx}:a]aresample=async=1[{cta_a}]")
    concat_labels += [f"[{cta_v}]", f"[{cta_a}]"]

    fc = ";".join(parts) + ";" + "".join(concat_labels) + f"concat=n={n + 1}:v=1:a=1[outv][outa]"

    if music_bed:
        inputs += ["-i", music_bed]
        bed_idx = next_idx
        next_idx += 1
        fc += (
            f";[outa]volume={original_volume}[am]"
            f";[{bed_idx}:a]volume={music_volume}[bm]"
            f";[am][bm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "[outa]"

    total = sum(_seg_duration(s) for s in segments) + cta_duration
    args = (
        inputs
        + ["-filter_complex", fc, "-map", "[outv]", "-map", audio_map]
        + gpu.encoding_args(video_bitrate)
        + ["-c:a", "aac", "-b:a", audio_bitrate, "-r", str(fps), output, "-y"]
    )
    if stage_cb:
        stage_cb("Encoding summary")
    try:
        runner.encode(args, total, progress_cb, cancel_event)
    finally:
        for tf in tmp_text:
            Path(tf).unlink(missing_ok=True)
        Path(tmp_dir).rmdir() if not any(Path(tmp_dir).iterdir()) else None

    duration = runner.get_video_info(output)[2]
    logger.info("Summary built: %s (%.1fs, %d segments)", output, duration, n)
    return {"output": output, "duration": duration, "segments": n}


def _probe_fps(runner: FFmpegRunner, file: str) -> str:
    data = runner.probe(file)
    streams = data.get("streams", [])
    rate = streams[0].get("r_frame_rate") if streams else None
    return rate or "30"


def build_fullflight_with_music(
    full_flight: str,
    output: str,
    music_bed: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    *,
    music_volume: float = 0.35,
    original_volume: float = 1.0,
    audio_bitrate: str = "192k",
    video_bitrate: str = "20M",
    cta_image: str,
    cta_duration: float,
    cta_line1: str,
    cta_line2: str,
    progress_cb: ProgressCb = None,
    stage_cb: StageCb = None,
    cancel_event=None,
) -> dict:
    """Mix music under the full flight (video stream-copied, only audio re-encoded) and append
    the CTA end screen.

    The CTA is encoded separately, at the flight's own resolution/fps, then joined with the
    concat demuxer (`-c copy`) — the same "normalize a short segment to the target spec, then
    stream-copy concat" technique `core/concat.py` already uses to prepend a sped-up hike, so the
    (often multi-hour, 4K) main video is never re-encoded just to tack on a 3s still image.
    """
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    w, h, main_dur = runner.get_video_info(full_flight)
    fps = _probe_fps(runner, full_flight)
    total = main_dur + cta_duration

    tmp_dir = tempfile.mkdtemp(prefix="vf_fm_")
    main_tmp = str(Path(tmp_dir) / "main.mp4")
    cta_tmp = str(Path(tmp_dir) / "cta.mp4")
    tf1 = str(Path(tmp_dir) / "cta1.txt")
    tf2 = str(Path(tmp_dir) / "cta2.txt")
    try:
        if stage_cb:
            stage_cb("Mixing music")
        fc = (
            f"[0:a]volume={original_volume}[am];"
            f"[1:a]volume={music_volume}[bm];"
            f"[am][bm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        args = [
            "-i", full_flight, "-i", music_bed,
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate,
            "-shortest", main_tmp, "-y",
        ]
        runner.encode(args, main_dur, _scaled_progress(progress_cb, 0.0, 0.85), cancel_event)

        if stage_cb:
            stage_cb("Building end screen")
        Path(tf1).write_text(cta_line1, encoding="utf-8")
        Path(tf2).write_text(cta_line2, encoding="utf-8")
        cta_filter = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p,"
            f"{_cta_drawtext(tf1, tf2, h)}[v]"
        )
        cta_args = (
            ["-loop", "1", "-t", f"{cta_duration:.3f}", "-i", cta_image,
             "-f", "lavfi", "-t", f"{cta_duration:.3f}", "-i",
             "anullsrc=channel_layout=stereo:sample_rate=48000",
             "-filter_complex", cta_filter, "-map", "[v]", "-map", "1:a"]
            + gpu.encoding_args(video_bitrate)
            + ["-c:a", "aac", "-b:a", audio_bitrate, cta_tmp, "-y"]
        )
        runner.encode(cta_args, cta_duration, _scaled_progress(progress_cb, 0.85, 0.95), cancel_event)

        if stage_cb:
            stage_cb("Appending end screen")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lst:
            for f in (main_tmp, cta_tmp):
                safe = str(f).replace("'", "'\\''")
                lst.write(f"file '{safe}'\n")
            list_path = lst.name
        try:
            concat_args = ["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output, "-y"]
            runner.encode(concat_args, total, _scaled_progress(progress_cb, 0.95, 1.0), cancel_event)
        finally:
            Path(list_path).unlink(missing_ok=True)
    finally:
        for f in (main_tmp, cta_tmp, tf1, tf2):
            Path(f).unlink(missing_ok=True)
        Path(tmp_dir).rmdir() if Path(tmp_dir).exists() and not any(Path(tmp_dir).iterdir()) else None

    duration = runner.get_video_info(output)[2]
    logger.info("Full flight with music built: %s (%.1fs, incl. CTA)", output, duration)
    return {"output": output, "duration": duration}
