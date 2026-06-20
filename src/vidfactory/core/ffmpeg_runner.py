"""FFmpeg/ffprobe subprocess wrapper. Ported from ShortFactory.

`encode()` parses ffmpeg stderr to drive a progress callback (wired to SSE by the job layer).
A fonts.conf path may be supplied so `drawtext` can resolve fonts by name.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
_SPEED_RE = re.compile(r"speed=\s*(\S+)")


def _parse_time(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


class FFmpegRunner:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        fonts_conf: Optional[Path] = None,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._env = os.environ.copy()
        if fonts_conf and Path(fonts_conf).exists():
            self._env["FONTCONFIG_FILE"] = str(fonts_conf)

    def probe(self, file_path: str) -> dict:
        """ffprobe JSON for the first video stream + format."""
        args = [
            self.ffprobe_path, "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,r_frame_rate,codec_name",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, env=self._env)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed for {file_path}: {result.stderr}")
        return json.loads(result.stdout)

    def get_video_info(self, file_path: str) -> tuple[int, int, float]:
        """(width, height, duration_seconds), preferring stream duration over format."""
        data = self.probe(file_path)
        streams = data.get("streams", [])
        fmt = data.get("format", {})

        width = height = 0
        duration = 0.0
        if streams:
            width = int(streams[0].get("width", 0))
            height = int(streams[0].get("height", 0))
            if streams[0].get("duration"):
                duration = float(streams[0]["duration"])
        if not duration and fmt.get("duration"):
            duration = float(fmt["duration"])
        return width, height, duration

    def encode(
        self,
        args: list[str],
        total_duration: float,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Run `ffmpeg <args>` (without the executable). Raises RuntimeError on failure."""
        cmd = [self.ffmpeg_path] + args
        logger.info("ffmpeg: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._env,
        )

        stderr_lines: list[str] = []
        try:
            for line in proc.stderr:  # type: ignore[union-attr]
                stderr_lines.append(line)
                if cancel_event and cancel_event.is_set():
                    proc.terminate()
                    raise RuntimeError("Encoding cancelled.")
                if progress_cb and total_duration > 0:
                    m = _TIME_RE.search(line)
                    if m:
                        fraction = min(_parse_time(*m.groups()) / total_duration, 1.0)
                        sm = _SPEED_RE.search(line)
                        progress_cb(fraction, sm.group(1) if sm else "")
        finally:
            proc.wait()

        if proc.returncode != 0:
            tail = "".join(stderr_lines[-30:])
            raise RuntimeError(f"FFmpeg failed (exit {proc.returncode}):\n{tail}")


@lru_cache(maxsize=1)
def get_runner() -> FFmpegRunner:
    """Process-wide FFmpegRunner built from config (ffmpeg/ffprobe paths + fonts.conf)."""
    from vidfactory.config import get_config

    cfg = get_config()
    return FFmpegRunner(cfg.ffmpeg.ffmpeg_path, cfg.ffmpeg.ffprobe_path, cfg.fonts_conf)
