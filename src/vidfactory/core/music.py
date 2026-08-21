"""Background-music selection + bed rendering + credits.

To keep the summary/short filter graphs simple, this module pre-renders a single loudnormed
"music bed" (covering the needed duration) which callers add as one extra input and mix in. Folder
durations are cached by folder-path hash to avoid re-probing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from vidfactory.core.ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}


@dataclass
class MusicSelection:
    mode: str  # 'file' | 'folder' | 'none'
    tracks: list[str] = field(default_factory=list)


def _folder_cache_file(folder: Path, cache_dir: Path) -> Path:
    h = hashlib.md5(str(folder).encode("utf-8")).hexdigest()
    return cache_dir / f"music_cache_{h}.json"


def _scan_durations(folder: Path, runner: FFmpegRunner, cache_dir: Path) -> dict[str, float]:
    # Recursive: genre folders are commonly organized as subfolders-of-subfolders
    # (e.g. `EDM/1. Lights/*.wav`) rather than flat directories of tracks — a plain
    # iterdir() silently finds 0 files for those and callers get music-less output
    # with no error anywhere.
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXT and p.is_file())
    signature = {str(p): (p.stat().st_size, int(p.stat().st_mtime)) for p in files}

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _folder_cache_file(folder, cache_dir)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("signature") == {k: list(v) for k, v in signature.items()}:
                durations = {k: float(v) for k, v in cached["durations"].items()}
                # Log unconditionally, even on a cache hit — a prior scan that cached 0 tracks
                # (e.g. before this function scanned recursively) would otherwise short-circuit
                # here with no trace at all, making a repeat "no music" build look like the
                # picker was never touched.
                logger.info("Scanned %d music tracks in %s (cached)", len(durations), folder)
                return durations
        except (ValueError, KeyError):
            pass

    durations = {}
    for p in files:
        try:
            durations[str(p)] = runner.get_video_info(str(p))[2]
        except RuntimeError:
            logger.warning("Could not probe audio duration: %s", p)
    cache_file.write_text(
        json.dumps({"signature": signature, "durations": durations}), encoding="utf-8"
    )
    logger.info("Scanned %d music tracks in %s", len(durations), folder)
    return durations


def select_tracks(music_path: str, needed_seconds: float, runner: FFmpegRunner, cache_dir: Path) -> MusicSelection:
    if not music_path:
        return MusicSelection(mode="none")
    p = Path(music_path)
    if p.is_file():
        return MusicSelection(mode="file", tracks=[str(p)])
    if not p.is_dir():
        logger.warning("Music path is neither file nor folder: %s", music_path)
        return MusicSelection(mode="none")

    durations = _scan_durations(p, runner, cache_dir)
    if not durations:
        logger.warning("No playable audio files found under %s (recursively) — building without music", p)
        return MusicSelection(mode="none")
    items = list(durations.items())
    random.shuffle(items)
    chosen: list[str] = []
    total = 0.0
    for path, dur in items:
        chosen.append(path)
        total += dur
        if total >= needed_seconds:
            break
    return MusicSelection(mode="folder", tracks=chosen)


def prepare_music_bed(
    music_path: str,
    needed_seconds: float,
    work_dir: Path,
    runner: FFmpegRunner,
    cache_dir: Path,
) -> tuple[str | None, list[str]]:
    """Render a single loudnormed AAC bed >= needed_seconds. Returns (bed_path|None, tracks_used)."""
    sel = select_tracks(music_path, needed_seconds, runner, cache_dir)
    if sel.mode == "none" or not sel.tracks:
        return None, []

    work_dir.mkdir(parents=True, exist_ok=True)
    bed = str(Path(tempfile.mkstemp(suffix=".m4a", dir=work_dir)[1]))

    loudnorm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    if sel.mode == "file":
        args = [
            "-stream_loop", "-1", "-i", sel.tracks[0],
            "-t", f"{needed_seconds:.3f}",
            "-af", f"{loudnorm},aresample=48000",
            "-c:a", "aac", "-b:a", "192k", bed, "-y",
        ]
    else:
        inputs: list[str] = []
        for t in sel.tracks:
            inputs += ["-i", t]
        n = len(sel.tracks)
        concat_in = "".join(f"[{i}:a]" for i in range(n))
        fc = f"{concat_in}concat=n={n}:v=0:a=1[c];[c]{loudnorm},aresample=48000[a]"
        args = inputs + [
            "-filter_complex", fc, "-map", "[a]",
            "-t", f"{needed_seconds:.3f}",
            "-c:a", "aac", "-b:a", "192k", bed, "-y",
        ]
    runner.encode(args, needed_seconds)
    logger.info("Music bed rendered (%s, %d tracks) -> %s", sel.mode, len(sel.tracks), bed)
    return bed, sel.tracks


# The music library ships with resources/StreamBeats Sync_Use License.pdf (Senpai Music Group,
# LLC's Synchronization and Master Use License) — clause 7 asks users to make "reasonable
# efforts" to credit the author's name and each track's title wherever the music is used. This
# is the library-level line for that; per-track author credit comes from each file's own
# artist/title tags (resolve_track_credits below), never asserted for tracks we can't attribute.
LICENSE_NOTICE = (
    "This music library is licensed under the Synchronization and Master Use License from "
    "Senpai Music Group, LLC."
)


def _track_meta(runner: FFmpegRunner, file: str) -> tuple[str | None, str | None]:
    try:
        out = subprocess.run(
            [runner.ffprobe_path, "-v", "quiet", "-show_entries",
             "format_tags=title,artist", "-of", "json", file],
            capture_output=True, text=True, timeout=15,
        )
        tags = (json.loads(out.stdout).get("format", {}) or {}).get("tags", {}) if out.returncode == 0 else {}
        return tags.get("title"), tags.get("artist")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None, None


def resolve_track_credits(tracks: list[str], runner: FFmpegRunner) -> list[dict]:
    """Probe each track's embedded title/artist tags once, at build time, so a later render
    (e.g. the project page's credits box) never has to shell out to ffprobe just to display
    text it already resolved — the result is stored verbatim on `Short.segments_used["music"]`."""
    out = []
    for t in tracks:
        title, artist = _track_meta(runner, t)
        out.append({"file": t, "title": title or Path(t).stem, "artist": artist})
    return out


def normalize_music_credits(value) -> list[dict]:
    """Coerce `Short.segments_used["music"]` to the current shape (list of
    `{file, title, artist}` dicts) regardless of which shape a given row was written with —
    older rows stored a single track path string (or `None`) before this key held resolved
    credits."""
    if not value:
        return []
    if isinstance(value, str):
        return [{"file": value, "title": Path(value).stem, "artist": None}]
    if isinstance(value, list):
        return [
            v if isinstance(v, dict) else {"file": v, "title": Path(v).stem, "artist": None}
            for v in value
        ]
    return []


def build_credits_text(credits: list[dict]) -> str:
    """Ready-to-paste attribution block for a video description. No filesystem paths, no legal
    boilerplate beyond the one line the license actually asks for — just what a viewer/YouTube
    needs: which tracks, whose they are (when known), and the library's license."""
    if not credits:
        return ""
    lines = ["Music:"]
    for c in credits:
        title = c.get("title") or Path(c.get("file") or "").stem
        artist = c.get("artist")
        lines.append(f'- "{title}"' + (f" by {artist}" if artist else ""))
    lines += ["", LICENSE_NOTICE]
    return "\n".join(lines)


def write_credits(credits: list[dict], out_path: str) -> None:
    text = build_credits_text(credits)
    if not text:
        return
    Path(out_path).write_text(text, encoding="utf-8")
    logger.info("Wrote music credits: %s", out_path)
