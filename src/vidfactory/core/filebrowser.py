"""Server-side file browser scoped to the configured mount roots.

No file uploads: the browser only lists paths under the allowed roots and rejects any path that
escapes them. Used to pick source videos, music folders, and image highlights.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from vidfactory.config import get_config

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}


class PathNotAllowed(Exception):
    pass


def _root(root_name: str) -> Path:
    roots = get_config().mount_roots()
    if root_name not in roots:
        raise PathNotAllowed(f"Unknown root: {root_name}")
    return roots[root_name]


def resolve(root_name: str, rel: str = "") -> Path:
    """Resolve `rel` under a named root, rejecting traversal outside it."""
    base = _root(root_name).resolve()
    target = (base / rel.lstrip("/\\")).resolve()
    if base != target and base not in target.parents:
        raise PathNotAllowed(f"Path escapes root '{root_name}': {rel}")
    return target


def _kind(p: Path) -> str:
    ext = p.suffix.lower()
    if p.is_dir():
        return "dir"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "file"


def list_dir(root_name: str, rel: str = "") -> dict:
    target = resolve(root_name, rel)
    if not target.exists():
        return {"root": root_name, "rel": rel, "exists": False, "entries": []}
    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            stat = child.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        entries.append(
            {
                "name": child.name,
                "rel": str(child.relative_to(_root(root_name).resolve())).replace("\\", "/"),
                "kind": _kind(child),
                "size": size,
                "mtime": mtime,
            }
        )
    return {"root": root_name, "rel": rel, "exists": True, "entries": entries}


def delete(root_name: str, rel: str) -> None:
    """Delete a single file under a writable root.

    Raises `PathNotAllowed` if the root isn't writable, `rel` escapes the root, or the target
    isn't an existing file (no recursive directory deletion via the browser).
    """
    if root_name not in get_config().writable_roots():
        raise PathNotAllowed(f"Root '{root_name}' is not writable")
    target = resolve(root_name, rel)
    if not target.is_file():
        raise PathNotAllowed(f"Not a file: {rel}")
    target.unlink()
    logger.info("[VF:browser] deleted root=%s path=%s", root_name, rel)


def check_mounts() -> dict[str, str]:
    """name -> 'ok' | 'missing' | 'not writable'."""
    cfg = get_config()
    writable = cfg.writable_roots()
    status: dict[str, str] = {}
    for name, path in cfg.mount_roots().items():
        if not path.exists():
            status[name] = "missing"
        elif name in writable and not _is_writable(path):
            status[name] = "not writable"
        else:
            status[name] = "ok"
    return status


def _is_writable(path: Path) -> bool:
    probe = path / ".vf_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def thumbnail(root_name: str, rel: str, size: int = 320) -> bytes:
    """Return a JPEG thumbnail for an image file under a root."""
    from PIL import Image  # local import: only needed when a thumbnail is requested

    target = resolve(root_name, rel)
    if target.suffix.lower() not in IMAGE_EXT or not target.is_file():
        raise PathNotAllowed(f"Not a previewable image: {rel}")
    with Image.open(target) as img:
        img = img.convert("RGB")
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
