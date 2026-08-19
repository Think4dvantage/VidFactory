from __future__ import annotations

from vidfactory.core import flightlog_hints
from vidfactory.database.models import Highlight, Project
from vidfactory.models.flightlog import SegmentOut


class _FakeFlightlogSection:
    base_url = "http://fl-test.example"


class _FakeConfig:
    flightlog = _FakeFlightlogSection()


def test_no_launch_marked(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/flightlog-hints")
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_launch_marked", "hints": []}


def test_unavailable_without_key_or_flight_id(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Highlight(project_id=project.id, name="Launch", start=10.0, end=15.0, role="launch"))
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/flightlog-hints")
    assert resp.status_code == 200
    assert resp.json() == {"status": "unavailable", "hints": []}


def test_hints_offset_from_launch(auth_client, db_session, user, monkeypatch):
    user.flightlog_api_key = "flg_test"
    project = Project(
        owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4",
        external_flight_id="abc123",
    )
    db_session.add(project)
    db_session.commit()
    db_session.add(Highlight(project_id=project.id, name="Launch", start=10.0, end=15.0, role="launch"))
    db_session.commit()

    monkeypatch.setattr(flightlog_hints, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        flightlog_hints.flightlog_client,
        "get_flight_segments",
        lambda base_url, api_key, flight_id: [
            SegmentOut(kind="thermal", start_offset_s=100.0, duration_s=60.0, alt_change_m=200.0, vertical_velocity_ms=2.5),
            SegmentOut(kind="takeoff", start_offset_s=0.0),
        ],
    )

    resp = auth_client.get(f"/api/projects/{project.id}/flightlog-hints")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["hints"]) == 2
    # launch.start (10.0) + start_offset_s
    assert body["hints"][0]["video_offset_s"] == 110.0
    assert body["hints"][0]["kind"] == "thermal"
    assert body["hints"][1]["video_offset_s"] == 10.0


def test_flightlog_hints_404_for_other_users_project(auth_client, db_session):
    from vidfactory.core.auth import hash_password
    from vidfactory.database.models import User

    other = User(username="other", password_hash=hash_password("x"))
    db_session.add(other)
    db_session.commit()
    project = Project(owner_id=other.id, flight_type="normal_flight")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/flightlog-hints")
    assert resp.status_code == 404
