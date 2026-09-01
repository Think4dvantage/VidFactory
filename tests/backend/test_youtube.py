from __future__ import annotations

from pathlib import Path

from vidfactory.core import projects as projects_core
from vidfactory.database.models import Highlight, Project, Short


def test_youtube_metadata_shape(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4", summary_file="sum.mp4")
    db_session.add(project)
    db_session.commit()

    launch = Highlight(
        project_id=project.id, name="Launch", start=0.0, end=10.0, role="launch",
        comment="nice forward launch",
    )
    thermal = Highlight(
        project_id=project.id, name="Big thermal", start=100.0, end=160.0, make_short=True,
    )
    db_session.add_all([launch, thermal])
    db_session.commit()

    highlight_short = Short(
        project_id=project.id, output_file="short1.mp4", short_type="highlight",
        duration=25.0, source_highlight_id=thermal.id, segments_used={"hook": [100.0, 106.0]},
    )
    random_short = Short(
        project_id=project.id, output_file="short2.mp4", short_type="random",
        duration=20.0, source_highlight_id=None, segments_used={},
    )
    db_session.add_all([highlight_short, random_short])
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/youtube-metadata")
    assert resp.status_code == 200
    body = resp.json()

    assert body["project_id"] == project.id
    assert body["pilot_name"] is None
    assert {h["name"] for h in body["highlights"]} == {"Launch", "Big thermal"}

    assert len(body["segment_order"]) == 2
    assert body["segment_order"][0]["offset_in_segments_seconds"] == 0.0
    assert body["segment_order"][1]["offset_in_segments_seconds"] == 10.0

    shorts_by_file = {s["output_file"]: s for s in body["shorts"]}
    assert shorts_by_file["short1.mp4"]["source_highlight_name"] == "Big thermal"
    assert shorts_by_file["short2.mp4"]["source_highlight_id"] is None
    assert shorts_by_file["short2.mp4"]["source_highlight_name"] is None

    # No Flightlog key/base_url configured in tests -> enrichment is skipped, not an error.
    assert body["flight"] is None
    assert body["flight_segments"] is None


def test_youtube_metadata_includes_pasteable_short_credits(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    with_music = Short(
        project_id=project.id, output_file="short1.mp4", short_type="highlight",
        segments_used={"music": [{"file": "a.mp3", "title": "Lights Up", "artist": "Harris Heller"}]},
    )
    legacy_shape = Short(
        project_id=project.id, output_file="short2.mp4", short_type="highlight",
        segments_used={"music": "/library/old_track.mp3"},
    )
    no_music = Short(
        project_id=project.id, output_file="short3.mp4", short_type="highlight", segments_used={},
    )
    db_session.add_all([with_music, legacy_shape, no_music])
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/youtube-metadata")
    assert resp.status_code == 200
    shorts_by_file = {s["output_file"]: s for s in resp.json()["shorts"]}

    assert '"Lights Up" by Harris Heller' in shorts_by_file["short1.mp4"]["credits"]
    assert "old_track" in shorts_by_file["short2.mp4"]["credits"]
    assert shorts_by_file["short3.mp4"]["credits"] is None


def test_youtube_metadata_includes_summary_and_fullmusic_credits(auth_client, db_session, user, tmp_path):
    summary_file = tmp_path / "Summary.mp4"
    summary_file.write_bytes(b"x")
    Path(projects_core.credits_path_for(str(summary_file))).write_text(
        'Music:\n- "Ridge Line" by Someone', encoding="utf-8"
    )

    project = Project(
        owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4",
        summary_file=str(summary_file),
    )
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/youtube-metadata")
    assert resp.status_code == 200
    body = resp.json()

    assert "Ridge Line" in body["summary_credits"]
    assert body["fullflight_music_credits"] is None  # no fullflight_music_file set


def test_youtube_metadata_includes_pilot_name(auth_client, db_session, user):
    project = Project(
        owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4",
        pilot_name="Hans",
    )
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/api/projects/{project.id}/youtube-metadata")
    assert resp.status_code == 200
    assert resp.json()["pilot_name"] == "Hans"


def test_youtube_metadata_404(auth_client):
    resp = auth_client.get("/api/projects/9999/youtube-metadata")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ENTITY_NOT_FOUND"


def test_youtube_metadata_requires_login(client, db_session):
    resp = client.get("/api/projects/1/youtube-metadata", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
