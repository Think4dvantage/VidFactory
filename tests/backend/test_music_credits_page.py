from __future__ import annotations

from vidfactory.core import projects
from vidfactory.database.models import Project, Short


def test_project_page_shows_short_credits_from_current_shape(auth_client, db_session, user, tmp_path):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(
        project_id=project.id, output_file=str(video), short_type="highlight",
        segments_used={"music": [{"file": "a.mp3", "title": "Lights Up", "artist": "Harris Heller"}]},
    ))
    db_session.commit()

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "Lights Up" in resp.text
    assert "Harris Heller" in resp.text
    assert "paste into the video description" in resp.text


def test_project_page_shows_short_credits_from_legacy_string_shape(auth_client, db_session, user, tmp_path):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(
        project_id=project.id, output_file=str(video), short_type="highlight",
        segments_used={"music": "/library/EDM/old_track.mp3"},
    ))
    db_session.commit()

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "old_track" in resp.text


def test_project_page_no_credits_box_when_short_has_no_music(auth_client, db_session, user, tmp_path):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(
        project_id=project.id, output_file=str(video), short_type="highlight", segments_used={},
    ))
    db_session.commit()

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "paste into the video description" not in resp.text


def test_project_page_shows_summary_credits_from_sibling_file(auth_client, db_session, user, tmp_path):
    from pathlib import Path

    summary_file = tmp_path / "Summary.mp4"
    summary_file.write_bytes(b"x")
    Path(projects.credits_path_for(str(summary_file))).write_text(
        'Music:\n- "Ridge Line" by Someone\n\nThis music library is licensed...', encoding="utf-8"
    )

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4",
                       summary_file=str(summary_file))
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "Ridge Line" in resp.text
