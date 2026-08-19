from __future__ import annotations

import pytest

from sqlalchemy import select

from vidfactory.api.routers import projects as projects_router
from vidfactory.config import Config
from vidfactory.core import chunked_upload
from vidfactory.database.models import Hike, Project, SourcePart


async def _bytes_stream(data: bytes):
    yield data


# --- core/chunked_upload.py: unit-level, no HTTP ---


async def test_begin_append_finish_roundtrip(tmp_path):
    staging = tmp_path / ".staging"
    name, offset = chunked_upload.begin(staging, "clip.mp4")
    assert name == "clip.mp4"
    assert offset == 0

    size = await chunked_upload.append_chunk(staging, name, 0, _bytes_stream(b"hello "))
    assert size == 6
    size = await chunked_upload.append_chunk(staging, name, 6, _bytes_stream(b"world"))
    assert size == 11

    path = chunked_upload.finish(staging, name, 11)
    assert path.read_bytes() == b"hello world"


async def test_begin_resumes_from_existing_size(tmp_path):
    staging = tmp_path / ".staging"
    name, _ = chunked_upload.begin(staging, "clip.mp4")
    await chunked_upload.append_chunk(staging, name, 0, _bytes_stream(b"partial"))

    # Simulates the client coming back after a sleep/disconnect and re-checking.
    _, offset = chunked_upload.begin(staging, "clip.mp4")
    assert offset == 7


async def test_append_chunk_offset_mismatch(tmp_path):
    staging = tmp_path / ".staging"
    name, _ = chunked_upload.begin(staging, "clip.mp4")
    await chunked_upload.append_chunk(staging, name, 0, _bytes_stream(b"abc"))

    with pytest.raises(chunked_upload.OffsetMismatch) as exc:
        await chunked_upload.append_chunk(staging, name, 0, _bytes_stream(b"xyz"))
    assert exc.value.actual_offset == 3


def test_finish_before_complete_raises_with_actual_size(tmp_path):
    staging = tmp_path / ".staging"
    name, _ = chunked_upload.begin(staging, "clip.mp4")
    with pytest.raises(chunked_upload.OffsetMismatch) as exc:
        chunked_upload.finish(staging, name, 999)
    assert exc.value.actual_offset == 0


def test_safe_name_strips_directory_components(tmp_path):
    # Path(...).name already reduces any traversal attempt to just the basename — verify
    # the staged file lands inside staging_dir, not wherever "../../etc/passwd" would resolve.
    staging = tmp_path / ".staging"
    name, offset = chunked_upload.begin(staging, "../../etc/passwd")
    assert name == "passwd"
    assert offset == 0


def test_safe_name_rejects_degenerate_names(tmp_path):
    staging = tmp_path / ".staging"
    for bad in ("", ".", ".."):
        with pytest.raises(ValueError):
            chunked_upload.begin(staging, bad)


# --- router-level: begin -> chunk -> finish over HTTP ---


@pytest.fixture()
def uploads_dir(tmp_path, monkeypatch):
    cfg = Config(uploads_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(projects_router, "get_config", lambda: cfg)
    return tmp_path / "uploads"


def _make_project(db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight")
    db_session.add(project)
    db_session.commit()
    return project


def test_parts_upload_roundtrip(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)
    data = b"x" * 20

    begin = auth_client.post(
        f"/api/projects/{project.id}/parts/upload/begin",
        json={"filename": "flight.mp4", "total_size": len(data)},
    )
    assert begin.status_code == 200
    filename, offset = begin.json()["filename"], begin.json()["offset"]
    assert offset == 0

    chunk1 = auth_client.put(
        f"/api/projects/{project.id}/parts/upload/chunk?filename={filename}&offset=0",
        content=data[:10],
    )
    assert chunk1.status_code == 200
    assert chunk1.json()["offset"] == 10

    chunk2 = auth_client.put(
        f"/api/projects/{project.id}/parts/upload/chunk?filename={filename}&offset=10",
        content=data[10:],
    )
    assert chunk2.status_code == 200
    assert chunk2.json()["offset"] == 20

    finish = auth_client.post(
        f"/api/projects/{project.id}/parts/upload/finish",
        json={"filename": filename, "total_size": len(data)},
    )
    assert finish.status_code == 200
    final_path = finish.json()["file"]
    assert (uploads_dir / str(project.id) / "flight.mp4").read_bytes() == data

    parts = db_session.execute(select(SourcePart).where(SourcePart.project_id == project.id)).scalars().all()
    assert [p.file for p in parts] == [final_path]


def test_chunk_offset_mismatch_returns_409_with_actual_offset(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)
    begin = auth_client.post(
        f"/api/projects/{project.id}/parts/upload/begin",
        json={"filename": "flight.mp4", "total_size": 10},
    )
    filename = begin.json()["filename"]
    auth_client.put(
        f"/api/projects/{project.id}/parts/upload/chunk?filename={filename}&offset=0",
        content=b"12345",
    )

    resp = auth_client.put(
        f"/api/projects/{project.id}/parts/upload/chunk?filename={filename}&offset=0",
        content=b"12345",
    )
    assert resp.status_code == 409
    assert resp.json()["offset"] == 5


def test_hike_upload_finish_sets_speed_factor(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)
    data = b"hikefootage"

    begin = auth_client.post(
        f"/api/projects/{project.id}/hike/upload/begin",
        json={"filename": "hike1.mp4", "total_size": len(data)},
    )
    filename = begin.json()["filename"]
    auth_client.put(
        f"/api/projects/{project.id}/hike/upload/chunk?filename={filename}&offset=0",
        content=data,
    )
    finish = auth_client.post(
        f"/api/projects/{project.id}/hike/upload/finish",
        json={"filename": filename, "total_size": len(data), "speed_factor": 32.0},
    )
    assert finish.status_code == 200

    hike = db_session.execute(select(Hike).where(Hike.project_id == project.id)).scalar_one()
    assert hike.speed_factor == 32.0
    assert len(hike.sources) == 1
    assert (uploads_dir / str(project.id) / "hike" / "hike1.mp4").read_bytes() == data


def test_parts_upload_begin_requires_ownership(auth_client, db_session, uploads_dir):
    resp = auth_client.post(
        "/api/projects/9999/parts/upload/begin",
        json={"filename": "flight.mp4", "total_size": 10},
    )
    assert resp.status_code == 404
