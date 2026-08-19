"""Resumable chunked upload, staged on local disk.

Works around Traefik's entrypoint `readTimeout` (a hard cap — 30 min by default — on how
long any single HTTP request may take) by never sending a multi-GB file in one request:
the client splits it into small chunks and sends each as its own request. As a side effect
this also makes uploads resume-safe: progress lives entirely in the size of the staging
file on disk, not in-memory session state, so a laptop sleep, a dropped network, or even
this container restarting mid-upload only costs the bytes since the last chunk landed —
never the whole transfer. The client just calls `begin` again for the same filename and
gets back how many bytes already made it, then resumes from there.

Chunks must arrive strictly in order — `append_chunk` rejects (with the real current size)
any chunk whose expected offset doesn't match, so the client always knows where to resync
after a failure instead of corrupting the file or getting stuck.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator


class OffsetMismatch(Exception):
    """The staging file isn't at the offset the caller expected. `actual_offset` is where it
    really is — the client should resync to this and continue (or finish) from there."""

    def __init__(self, actual_offset: int):
        self.actual_offset = actual_offset
        super().__init__(f"Staging file is at offset {actual_offset}")


def safe_name(filename: str) -> str:
    """Strip any directory components — never trust a client-supplied path."""
    name = Path(filename).name
    if not name or name in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    return name


def _staging_path(staging_dir: Path, filename: str) -> Path:
    """Resolve `filename` under `staging_dir`, rejecting traversal outside it."""
    name = safe_name(filename)
    base = staging_dir.resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise ValueError(f"Path escapes staging dir: {filename!r}")
    return target


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def begin(staging_dir: Path, filename: str) -> tuple[str, int]:
    """Start (or resume) an upload. Returns (safe_filename, bytes_already_received)."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = _staging_path(staging_dir, filename)
    return path.name, _size(path)


async def append_chunk(
    staging_dir: Path, filename: str, expected_offset: int, stream: AsyncIterator[bytes]
) -> int:
    """Append one chunk's bytes at `expected_offset`. Returns the new total size.

    Raises OffsetMismatch if the staging file isn't currently at `expected_offset` —
    happens when a previous chunk partially landed before the connection dropped.
    """
    path = _staging_path(staging_dir, filename)
    current = _size(path)
    if current != expected_offset:
        raise OffsetMismatch(current)
    with path.open("ab") as out:
        async for data in stream:
            out.write(data)
    return _size(path)


def finish(staging_dir: Path, filename: str, expected_total: int) -> Path:
    """Verify the staged file is complete and return its path for the caller to move.

    Raises OffsetMismatch (actual size, however short) if it isn't fully landed yet.
    """
    path = _staging_path(staging_dir, filename)
    size = _size(path)
    if size != expected_total:
        raise OffsetMismatch(size)
    return path
