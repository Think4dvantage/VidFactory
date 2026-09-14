from __future__ import annotations

import pytest

from vidfactory.api.routers import projects as projects_router
from vidfactory.config import Config
from vidfactory.database.models import Project


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


def test_picture_upload_then_attach_to_highlight(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)

    upload = auth_client.post(
        f"/api/projects/{project.id}/highlights/picture-upload",
        files={"file": ("summit.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert upload.status_code == 200
    image_path = upload.json()["image_path"]
    assert (uploads_dir / str(project.id) / "pictures" / "summit.jpg").read_bytes() == b"fake-jpeg-bytes"

    create = auth_client.post(
        f"/api/projects/{project.id}/highlights",
        json={
            "name": "Summit view", "start": 120.0, "end": 125.0,
            "type": "picture", "image_path": image_path, "duration": 5.0,
        },
    )
    assert create.status_code == 200
    hid = create.json()["id"]

    listed = auth_client.get(f"/api/projects/{project.id}/highlights").json()["data"]
    assert listed[0]["type"] == "picture"
    assert listed[0]["image_path"] == image_path

    served = auth_client.get(f"/api/projects/{project.id}/highlights/{hid}/picture")
    assert served.status_code == 200
    assert served.content == b"fake-jpeg-bytes"


def test_picture_upload_rejects_unsupported_extension(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)

    resp = auth_client.post(
        f"/api/projects/{project.id}/highlights/picture-upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_picture_upload_requires_ownership(auth_client, uploads_dir):
    resp = auth_client.post(
        "/api/projects/9999/highlights/picture-upload",
        files={"file": ("summit.jpg", b"data", "image/jpeg")},
    )
    assert resp.status_code == 404


def test_highlight_picture_404_when_highlight_has_no_image(auth_client, db_session, user, uploads_dir):
    project = _make_project(db_session, user)
    create = auth_client.post(
        f"/api/projects/{project.id}/highlights",
        json={"name": "Thermal", "start": 10.0, "end": 20.0, "type": "video"},
    )
    hid = create.json()["id"]

    resp = auth_client.get(f"/api/projects/{project.id}/highlights/{hid}/picture")
    assert resp.status_code == 404
