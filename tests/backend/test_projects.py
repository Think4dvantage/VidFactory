from __future__ import annotations

from vidfactory.database.models import Project, User


def test_create_project_without_pilot_name_defaults_to_none(auth_client, db_session, user):
    resp = auth_client.post("/projects/new", data={"date": "2026-08-30"}, follow_redirects=False)
    assert resp.status_code == 303

    project = db_session.query(Project).filter_by(owner_id=user.id).one()
    assert project.pilot_name is None


def test_create_project_with_pilot_name(auth_client, db_session, user):
    resp = auth_client.post(
        "/projects/new", data={"date": "2026-08-30", "pilot_name": "Hans"}, follow_redirects=False
    )
    assert resp.status_code == 303

    project = db_session.query(Project).filter_by(owner_id=user.id).one()
    assert project.pilot_name == "Hans"


def test_set_pilot_name_round_trip(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.post(f"/api/projects/{project.id}/pilot-name", data={"pilot_name": "Hans"})
    assert resp.status_code == 204
    db_session.refresh(project)
    assert project.pilot_name == "Hans"

    # Clearing back to blank clears it to None, not an empty string.
    resp = auth_client.post(f"/api/projects/{project.id}/pilot-name", data={"pilot_name": ""})
    assert resp.status_code == 204
    db_session.refresh(project)
    assert project.pilot_name is None


def test_set_pilot_name_404s_for_another_users_project(auth_client, db_session, user):
    other = User(username="other", password_hash="x")
    db_session.add(other)
    db_session.commit()
    other_project = Project(owner_id=other.id, flight_type="normal_flight")
    db_session.add(other_project)
    db_session.commit()

    resp = auth_client.post(
        f"/api/projects/{other_project.id}/pilot-name", data={"pilot_name": "Hans"}
    )
    assert resp.status_code == 404
