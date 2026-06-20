"""Encoder detection: NVENC (NVIDIA) -> QSV (Intel /dev/dri) -> libx264.

Ported from ShortFactory. The Fedora host has both an NVIDIA RTX 3070 Mobile and Intel graphics,
so both hardware paths are probed; the QSV path is test-encoded before being trusted.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GPUConfig:
    codec: str
    preset: str
    display_name: str
    extra_args: list[str]

    def encoding_args(self, bitrate: str) -> list[str]:
        args = ["-c:v", self.codec, "-preset", self.preset]
        # libx264 uses CRF (in extra_args); a -b:v there would conflict.
        if self.codec != "libx264":
            args += ["-b:v", bitrate]
        args.extend(self.extra_args)
        return args


_cached: GPUConfig | None = None


def detect(ffmpeg_path: str = "ffmpeg") -> GPUConfig:
    global _cached
    if _cached is None:
        _cached = _detect(ffmpeg_path)
        logger.info("Encoder selected: %s (%s)", _cached.codec, _cached.display_name)
    return _cached


def _detect(ffmpeg_path: str) -> GPUConfig:
    # 1. NVIDIA NVENC
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu_name = result.stdout.strip().splitlines()[0]
            return GPUConfig("h264_nvenc", "p4", f"NVIDIA {gpu_name}", [])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2. Intel QSV — present in encoder list AND able to actually encode
    try:
        enc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        if "h264_qsv" in enc.stdout:
            test = subprocess.run(
                [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
                    "-c:v", "h264_qsv", "-f", "null", "-",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if test.returncode == 0:
                return GPUConfig("h264_qsv", "medium", "Intel QSV", ["-look_ahead", "1"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 3. CPU fallback
    return GPUConfig("libx264", "medium", "CPU (libx264)", ["-crf", "18"])
