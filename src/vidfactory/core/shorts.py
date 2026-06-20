"""Shorts engine: assemble vertical 1080x1920 shorts from the full flight.

All clips are trimmed from the full flight via input-seeking. Ported from ShortFactory's
short_builder (normalize filters, UsedMap de-dup, CTA end screen). Two modes are orchestrated by the
caller: highlight-driven (hook -> launch? -> flying -> landing? -> CTA) and random-pool
(launch? -> flying -> landing? -> CTA). Load-bearing details kept verbatim: setsar=1:1 + fps=30 on
every stream, one input per clip, CTA = looped image + anullsrc, chronological sort after sampling.
"""

from __future__ import annotations

import logging
import random
import tempfile
from pathlib import Path
from typing import Callable, Optional

from vidfactory.core.ffmpeg_runner import FFmpegRunner
from vidfactory.core.gpu_detector import GPUConfig

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
ProgressCb = Optional[Callable[[float, str], None]]
UsedMap = dict[str, list[tuple[float, float]]]

Range = tuple[float, float]


def _subtract(rng: Range, used: list[Range]) -> list[Range]:
    rs, re = rng
    free: list[Range] = []
    cur = rs
    for s, e in sorted(u for u in used if u[1] > rs and u[0] < re):
        if s > cur:
            free.append((cur, min(s, re)))
        cur = max(cur, e)
        if cur >= re:
            break
    if cur < re:
        free.append((cur, re))
    return free


def _available_windows(pool: list[Range], needed: float, used: list[Range]) -> list[Range]:
    out: list[Range] = []
    for rng in pool:
        for fs, fe in _subtract(rng, used):
            if fe - fs >= needed:
                out.append((fs, fe))
    return out


def pick_subclip(pool: list[Range], needed: float, used: UsedMap, key: str) -> Range | None:
    windows = _available_windows(pool, needed, used.get(key, []))
    if not windows:
        return None
    fs, fe = random.choice(windows)
    start = random.uniform(fs, fe - needed)
    clip = (start, start + needed)
    used.setdefault(key, []).append(clip)
    return clip


def pick_clips(pool: list[Range], count: int, needed: float, used: UsedMap, key: str) -> list[Range]:
    clips: list[Range] = []
    for _ in range(count):
        c = pick_subclip(pool, needed, used, key)
        if c is None:
            break
        clips.append(c)
    clips.sort()  # chronological so the short reads as a coherent flight
    return clips


def _normalize_v(idx: int, src_w: int, src_h: int) -> str:
    if src_w >= src_h:  # horizontal -> centre-crop to 9:16
        geo = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={W}:{H}"
    else:  # already vertical -> pillarbox
        geo = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
    return f"[{idx}:v]{geo},setsar=1,fps={FPS},setpts=PTS-STARTPTS,format=yuv420p[v{idx}]"


def build_short(
    full_flight: str,
    clips: list[Range],
    output: str,
    runner: FFmpegRunner,
    gpu: GPUConfig,
    *,
    src_w: int,
    src_h: int,
    cta_image: str,
    cta_duration: float,
    cta_line1: str,
    cta_line2: str,
    video_bitrate: str = "8M",
    audio_bitrate: str = "128k",
    music_bed: str | None = None,
    music_volume: float = 0.35,
    original_volume: float = 1.0,
    progress_cb: ProgressCb = None,
    cancel_event=None,
) -> dict:
    if not clips:
        raise ValueError("No clips for the short.")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="vf_short_")

    inputs: list[str] = []
    v_parts: list[str] = []
    a_parts: list[str] = []
    labels: list[str] = []
    idx = 0
    for (s, e) in clips:
        inputs += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", full_flight]
        v_parts.append(_normalize_v(idx, src_w, src_h))
        a_parts.append(f"[{idx}:a]aresample=async=1,asetpts=PTS-STARTPTS[a{idx}]")
        labels += [f"[v{idx}]", f"[a{idx}]"]
        idx += 1

    # CTA: looped still image + silent audio, with two centred drawtext lines (textfile = no escaping).
    inputs += ["-loop", "1", "-t", f"{cta_duration:.3f}", "-i", cta_image]
    img_idx = idx
    idx += 1
    inputs += ["-f", "lavfi", "-t", f"{cta_duration:.3f}", "-i",
               "anullsrc=channel_layout=stereo:sample_rate=48000"]
    sil_idx = idx
    idx += 1
    tf1, tf2 = str(Path(tmp_dir) / "l1.txt"), str(Path(tmp_dir) / "l2.txt")
    Path(tf1).write_text(cta_line1, encoding="utf-8")
    Path(tf2).write_text(cta_line2, encoding="utf-8")
    dt1 = (f"drawtext=textfile='{tf1}':expansion=none:fontcolor=white:fontsize=64:borderw=5"
           f":bordercolor=black:x=(w-text_w)/2:y=h*0.60:font=Arial")
    dt2 = (f"drawtext=textfile='{tf2}':expansion=none:fontcolor=white:fontsize=76:borderw=5"
           f":bordercolor=black:x=(w-text_w)/2:y=h*0.60+100:font=Arial")
    v_parts.append(
        f"[{img_idx}]fps={FPS},scale={W}:{H},setsar=1,{dt1},{dt2},format=yuv420p,setpts=PTS-STARTPTS[vcta]"
    )
    a_parts.append(f"[{sil_idx}:a]aresample=async=1[acta]")
    labels += ["[vcta]", "[acta]"]

    n = len(clips) + 1
    fc = ";".join(v_parts + a_parts) + ";" + "".join(labels) + f"concat=n={n}:v=1:a=1[outv][outa]"

    if music_bed:
        inputs += ["-i", music_bed]
        bed_idx = idx
        idx += 1
        fc += (
            f";[outa]volume={original_volume}[am];[{bed_idx}:a]volume={music_volume}[bm];"
            f"[am][bm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "[outa]"

    total = sum(e - s for s, e in clips) + cta_duration
    args = (
        inputs
        + ["-filter_complex", fc, "-map", "[outv]", "-map", audio_map]
        + gpu.encoding_args(video_bitrate)
        + ["-c:a", "aac", "-b:a", audio_bitrate, "-r", str(FPS), output, "-y"]
    )
    try:
        runner.encode(args, total, progress_cb, cancel_event)
    finally:
        for f in (tf1, tf2):
            Path(f).unlink(missing_ok=True)
        Path(tmp_dir).rmdir() if Path(tmp_dir).exists() and not any(Path(tmp_dir).iterdir()) else None

    duration = runner.get_video_info(output)[2]
    logger.info("Short built: %s (%.1fs, %d clips)", output, duration, len(clips))
    return {"output": output, "duration": duration}
