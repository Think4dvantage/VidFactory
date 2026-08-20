from __future__ import annotations

from vidfactory.database.models import Project


def test_project_page_renders_with_full_flight(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "resumeActiveJobs" in resp.text
    assert f"project_id !== {project.id}" in resp.text
