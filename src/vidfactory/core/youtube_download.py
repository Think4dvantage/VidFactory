"""Download a flight's already-published full-flight video from YouTube.

The URL is never taken from the client and never stored: it is re-resolved from the flight's
Flightlog `links` every time (see `find_fullflight_url`), so nothing user-supplied ever reaches
yt-dlp. The result lands at the project's normal full-flight path, so everything downstream
(720p proxy, editor, Summary, Shorts) works exactly as for an uploaded/concatenated flight.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
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


# yt-dlp's own retries (below) cover network hiccups and 5xx, but its downloader raises any 4xx
# straight away -- and a mid-download 403 on a multi-GB file is what YouTube's media servers
# actually do (seen live on the first 20 GB attempt). So a failed attempt is retried here with a
# fresh extraction (new signed URL); the .part files from the previous attempt are resumed.
_ATTEMPTS = 4
_BACKOFF_S = 5.0
_HTTP_ERROR_RE = re.compile(r"HTTP Error \d{3}")

# 10 MiB range requests instead of one multi-GB stream: less exposed to throttling / a single
# rejected response, and each chunk is an independent request.
_CHUNK_SIZE = 10 * 1024 * 1024


class DownloadCancelled(Exception):
    pass


class _YtdlpLogger:
    """Routes yt-dlp's own output into our logs so an operator can see what it picked (format
    ids, resolution) and what went wrong. Its per-tick "[download]" progress lines are dropped."""

    def debug(self, msg: str) -> None:
        if msg.startswith("[download]"):
            return
        logger.info("[VF:youtube_download] yt-dlp: %s", msg)

    info = debug

    def warning(self, msg: str) -> None:
        logger.warning("[VF:youtube_download] yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        logger.error("[VF:youtube_download] yt-dlp: %s", msg)


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

    # yt-dlp wants a real path (or dir), not a bare command name it would have to look up on
    # PATH itself -- with the default "ffmpeg" it reports "ffmpeg is not installed" even when it is.
    ffmpeg = shutil.which(ffmpeg_path)
    if ffmpeg is None:
        raise RuntimeError(f"ffmpeg not found (configured ffmpeg_path={ffmpeg_path!r}, not on PATH)")

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        if d.get("status") == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress_cb(min(d.get("downloaded_bytes", 0) / total, 0.99), d.get("_speed_str", "").strip())

    def pp_hook(d: dict) -> None:
        # The video+audio merge (ffmpeg, no progress reporting) of a multi-GB download takes a
        # while; without a stage the bar just sits at 99% and looks stuck.
        if d.get("postprocessor") != "Merger":
            return
        if d.get("status") == "started":
            logger.info("Job [ytdownload] merging video+audio with ffmpeg — %s", output)
            if stage_cb:
                stage_cb("Merging video+audio (ffmpeg)")
        elif d.get("status") == "finished":
            logger.info("Job [ytdownload] merge finished — %s", output)

    opts = {
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out.with_suffix("")) + ".%(ext)s",
        "ffmpeg_location": ffmpeg,
        "noplaylist": True,
        # Not overwriting keeps a finished video/audio stream (or a finished output) from a
        # previous attempt instead of deleting and re-downloading it; partial .part files resume
        # regardless (continuedl defaults on).
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "http_chunk_size": _CHUNK_SIZE,
        "logger": _YtdlpLogger(),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }
    logger.info(
        "Job [ytdownload] starting — url=%s format=%s ffmpeg=%s -> %s", url, _FORMAT, ffmpeg, output
    )
    if stage_cb:
        stage_cb("Downloading from YouTube")
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except DownloadCancelled:
            raise
        except yt_dlp.utils.DownloadError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled() from exc
            transient = bool(_HTTP_ERROR_RE.search(str(exc)))
            if not transient or attempt == _ATTEMPTS:
                logger.error(
                    "Job [ytdownload] yt-dlp failed — attempt %d/%d url=%s output=%s: %s",
                    attempt, _ATTEMPTS, url, output, exc,
                )
                raise
            delay = _BACKOFF_S * attempt
            logger.warning(
                "Job [ytdownload] attempt %d/%d failed (%s) — retrying in %.0fs with a fresh URL, "
                "resuming partial files", attempt, _ATTEMPTS, exc, delay,
            )
            if stage_cb:
                stage_cb(f"Retrying download ({attempt + 1}/{_ATTEMPTS})")
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise DownloadCancelled() from exc
            else:
                time.sleep(delay)
            if stage_cb:
                stage_cb("Downloading from YouTube")
    if not out.exists():
        raise RuntimeError(f"yt-dlp finished but {output} was not produced")
    logger.info("Job [ytdownload] done — %s (%d bytes)", output, out.stat().st_size)
    return {"output": output}
