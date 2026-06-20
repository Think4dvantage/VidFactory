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
    fc = ";".join(parts) + ";" + "".join(concat_labels) + f"concat=n={n}:v=1:a=1[outv][outa]"

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

    total = sum(_seg_duration(s) for s in segments)
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


def build_fullflight_with_music(
    full_flight: str,
    output: str,
    music_bed: str,
    runner: FFmpegRunner,
    *,
    music_volume: float = 0.35,
    original_volume: float = 1.0,
    audio_bitrate: str = "192k",
    progress_cb: ProgressCb = None,
    stage_cb: StageCb = None,
    cancel_event=None,
) -> dict:
    """Mix music under the full flight without re-encoding the video (stream copy)."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    total = runner.get_video_info(full_flight)[2]
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
        "-shortest", output, "-y",
    ]
    if stage_cb:
        stage_cb("Mixing music")
    runner.encode(args, total, progress_cb, cancel_event)
    duration = runner.get_video_info(output)[2]
    logger.info("Full flight with music built: %s (%.1fs)", output, duration)
    return {"output": output, "duration": duration}
