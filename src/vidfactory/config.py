from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AppSection(BaseModel):
    log_level: str = "INFO"


class FFmpegSection(BaseModel):
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    resources_dir: str = "resources"


class MountsSection(BaseModel):
    videos: str = "/data/InstaOut"
    music: str = "/data/music"
    output_fullflights: str = "/data/fullflights"
    output_summaries: str = "/data/summaries"
    output_shorts: str = "/data/shorts"
    archive: str = "/data/Archive"
    igc: str = "/data/igc"


class EncodeSection(BaseModel):
    video_bitrate: str = "20M"
    audio_bitrate: str = "192k"


class MusicSection(BaseModel):
    music_volume: float = 0.35
    original_audio_volume: float = 1.0


class ShortsSection(BaseModel):
    # Caps chosen so a fully-loaded highlight short (hook + launch + flying*count + landing + CTA)
    # stays under ~30s: 6 + 4 + 3*3 + 4 + 3 = 26s.
    clip_duration: float = 3.0          # length of each sampled flying clip
    flying_clip_count: int = 3
    cta_duration: float = 3.0
    hook_max: float = 6.0               # cap on the leading highlight clip
    launch_max: float = 4.0
    landing_max: float = 4.0
    video_bitrate: str = "8M"
    audio_bitrate: str = "128k"
    cta_line1: str = "for more relaxed Paragliding"
    cta_line2: str = "Like & Subscribe"


class Config(BaseModel):
    # Local data dir for the SQLite DB + caches. MUST be a local filesystem — SQLite WAL mode
    # does not work over NFS/SMB, so this is deliberately separate from the NAS `archive` mount.
    data_dir: str = "/app/data"
    app: AppSection = Field(default_factory=AppSection)
    ffmpeg: FFmpegSection = Field(default_factory=FFmpegSection)
    mounts: MountsSection = Field(default_factory=MountsSection)
    encode: EncodeSection = Field(default_factory=EncodeSection)
    music: MusicSection = Field(default_factory=MusicSection)
    shorts: ShortsSection = Field(default_factory=ShortsSection)

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "vidfactory.db"

    @property
    def fonts_conf(self) -> Path:
        return Path(self.ffmpeg.resources_dir) / "fonts.conf"

    @property
    def cta_image(self) -> Path:
        return Path(self.ffmpeg.resources_dir) / "EndScreenBackground.JPG"

    def mount_roots(self) -> dict[str, Path]:
        """name -> Path for every configured storage root."""
        return {
            "videos": Path(self.mounts.videos),
            "music": Path(self.mounts.music),
            "output_fullflights": Path(self.mounts.output_fullflights),
            "output_summaries": Path(self.mounts.output_summaries),
            "output_shorts": Path(self.mounts.output_shorts),
            "archive": Path(self.mounts.archive),
            "igc": Path(self.mounts.igc),
        }

    def writable_roots(self) -> set[str]:
        return {"output_fullflights", "output_summaries", "output_shorts", "archive", "igc"}


# Environment overrides for the storage roots (handy in docker-compose / .env).
_ENV_MOUNT_KEYS = {
    "videos": "VF_VIDEOS",
    "music": "VF_MUSIC",
    "output_fullflights": "VF_OUTPUT_FULLFLIGHTS",
    "output_summaries": "VF_OUTPUT_SUMMARIES",
    "output_shorts": "VF_OUTPUT_SHORTS",
    "archive": "VF_ARCHIVE",
    "igc": "VF_IGC",
}

CONFIG_PATH = Path(os.environ.get("VF_CONFIG", "config.yml"))


@lru_cache(maxsize=1)
def get_config() -> Config:
    data: dict = {}
    if CONFIG_PATH.exists():
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg = Config(**data)

    for field, env in _ENV_MOUNT_KEYS.items():
        val = os.environ.get(env)
        if val:
            setattr(cfg.mounts, field, val)

    log_level = os.environ.get("VF_LOG_LEVEL")
    if log_level:
        cfg.app.log_level = log_level

    data_dir = os.environ.get("VF_DATA_DIR")
    if data_dir:
        cfg.data_dir = data_dir

    return cfg
