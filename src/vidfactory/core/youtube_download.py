"""Download a flight's already-published full-flight video from YouTube.

The URL is never taken from the client and never stored: it is re-resolved from the flight's
Flightlog `links` every time (see `find_fullflight_url`), so nothing user-supplied ever reaches
yt-dlp. The result lands at the project's normal full-flight path, so everything downstream
(720p proxy, editor, Summary, Shorts) works exactly as for an uploaded/concatenated flight.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from vidfactory.config import get_config
from vidfactory.core import flightlog_client
from vidfactory.database.models import Project
from vidfactory.models.flightlog import FlightLink

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_LABEL_RE = re.compile(r"full[\s_-]*flight", re.IGNORECASE)

# Best video + best audio, whatever YouTube offers (4K when available) -- remuxed to mp4, never
# re-encoded.
_FORMAT = "bv*+ba/b"


class DownloadCancelled(Exception):
    pass


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS)


def find_fullflight_url(links: list[FlightLink]) -> str | None:
    """First link whose label reads "Full Flight"/"FullFlight" (any case/spacing/hyphenation)
    and whose URL is on YouTube, else None."""
    for link in links:
        if link.label and _LABEL_RE.search(link.label) and _is_youtube(link.url):
            return link.url
    return None


def resolve_fullflight_url(project: Project) -> str | None:
    """Best-effort lookup, same contract as the other Flightlog enrichments (M6/M8): no flight
    id / no key / Flightlog down or 404 all just mean None, never an exception."""
    cfg = get_config()
    api_key = project.owner.flightlog_api_key if project.owner else None
    if not (cfg.flightlog.base_url and project.external_flight_id and api_key):
        return None
    try:
        meta = flightlog_client.get_flight_metadata(cfg.flightlog.base_url, api_key, project.external_flight_id)
    except (flightlog_client.FlightlogError, httpx.HTTPError) as exc:
        logger.warning(
            "[VF:youtube_download] Flightlog lookup failed — project=%s flight=%s: %s",
            project.id, project.external_flight_id, exc,
        )
        return None
    if meta is None:
        return None
    return find_fullflight_url(meta.links)


def download(
    url: str,
    output: str,
    *,
    ffmpeg_path: str = "ffmpeg",
    progress_cb: Callable[..., None] | None = None,
    stage_cb: Callable[[str], None] | None = None,
    cancel_event=None,
) -> dict:
    """Download `url` to `output` (an .mp4 path). Raises on failure or cancel."""
    import yt_dlp  # lazy: only this feature needs it

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        if d.get("status") == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress_cb(min(d.get("downloaded_bytes", 0) / total, 0.99), d.get("_speed_str", "").strip())

    opts = {
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out.with_suffix("")) + ".%(ext)s",
        "ffmpeg_location": ffmpeg_path,
        "noplaylist": True,
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    logger.info("Job [ytdownload] yt-dlp — url=%s format=%s -> %s", url, _FORMAT, output)
    if stage_cb:
        stage_cb("Downloading from YouTube")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except DownloadCancelled:
        out.unlink(missing_ok=True)
        raise
    except yt_dlp.utils.DownloadError as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled() from exc
        raise
    if not out.exists():
        raise RuntimeError(f"yt-dlp finished but {output} was not produced")
    logger.info("Job [ytdownload] done — %s (%d bytes)", output, out.stat().st_size)
    return {"output": output}
