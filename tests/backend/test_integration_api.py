from __future__ import annotations

from vidfactory.core.auth import generate_api_key, hash_password
from vidfactory.database.models import Project, Short, User


def test_no_key_401s(client):
    resp = client.get("/api/integration/v1/projects")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_bogus_key_401s(client):
    resp = client.get("/api/integration/v1/projects", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_valid_key_lists_own_projects(client, db_session, user):
    key = generate_api_key(db_session, user)
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    resp = client.get("/api/integration/v1/projects", headers={"Authorization": f"Bearer {key}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["project_id"] == project.id
    assert body["data"][0]["has_full_flight"] is True
    assert body["data"][0]["has_summary"] is False


def test_valid_key_gets_youtube_metadata(client, db_session, user):
    key = generate_api_key(db_session, user)
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    resp = client.get(
        f"/api/integration/v1/projects/{project.id}/youtube-metadata",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert resp.status_code == 200
    assert resp.json()["project_id"] == project.id


def test_valid_key_gets_pasteable_music_credits(client, db_session, user):
    """The exact path an external YouTube-management tool uses to pull description text — no
    browser session, API key only. See M18/M19: credits are exposed here, not just on the UI."""
    key = generate_api_key(db_session, user)
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(
        project_id=project.id, output_file="short1.mp4", short_type="highlight",
        segments_used={"music": [{"file": "a.mp3", "title": "Lights Up", "artist": "Harris Heller"}]},
    ))
    db_session.commit()

    resp = client.get(
        f"/api/integration/v1/projects/{project.id}/youtube-metadata",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert resp.status_code == 200
    credits = resp.json()["shorts"][0]["credits"]
    assert '"Lights Up" by Harris Heller' in credits


def test_key_cannot_see_another_users_project(client, db_session, user):
    key = generate_api_key(db_session, user)
    other = User(username="other", password_hash=hash_password("x"))
    db_session.add(other)
    db_session.commit()
    other_project = Project(owner_id=other.id, flight_type="normal_flight")
    db_session.add(other_project)
    db_session.commit()

    resp = client.get(
        f"/api/integration/v1/projects/{other_project.id}/youtube-metadata",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ENTITY_NOT_FOUND"

    listing = client.get("/api/integration/v1/projects", headers={"Authorization": f"Bearer {key}"})
    assert listing.json()["total"] == 0


def test_generate_api_key_redirects_and_persists(auth_client, db_session, user):
    resp = auth_client.post("/api/account/api-key")
    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == "/account"

    db_session.refresh(user)
    assert user.api_key is not None

    page = auth_client.get("/account")
    assert "configured" in page.text
    assert user.api_key in page.text


def test_key_minted_via_account_endpoint_authenticates_against_the_api(auth_client, client, db_session, user):
    auth_client.post("/api/account/api-key")
    db_session.refresh(user)

    resp = client.get(
        "/api/integration/v1/projects", headers={"Authorization": f"Bearer {user.api_key}"}
    )

    assert resp.status_code == 200


def test_refreshing_the_account_page_does_not_rotate_the_key(auth_client, db_session, user):
    auth_client.post("/api/account/api-key")
    db_session.refresh(user)
    first_key = user.api_key

    auth_client.get("/account")
    auth_client.get("/account")
    db_session.refresh(user)

    assert user.api_key == first_key
