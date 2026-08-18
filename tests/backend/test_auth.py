from __future__ import annotations

from vidfactory.core.auth import hash_password
from vidfactory.database.models import Project, User


def test_login_logout_flow(client, db_session):
    db_session.add(User(username="pilot", password_hash=hash_password("s3cret")))
    db_session.commit()

    resp = client.post("/login", data={"username": "pilot", "password": "s3cret"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "vf_session" in resp.cookies

    # Cookie now grants access to a page behind require_user.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200

    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_wrong_password(client, db_session):
    db_session.add(User(username="pilot", password_hash=hash_password("s3cret")))
    db_session.commit()
    resp = client.post("/login", data={"username": "pilot", "password": "nope"})
    assert resp.status_code == 401


def test_unauthenticated_api_call_gets_401_envelope(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_cross_user_project_404s(client, db_session):
    alice = User(username="alice", password_hash=hash_password("a"))
    bob = User(username="bob", password_hash=hash_password("b"))
    db_session.add_all([alice, bob])
    db_session.commit()
    project = Project(owner_id=alice.id, flight_type="normal_flight")
    db_session.add(project)
    db_session.commit()

    login = client.post("/login", data={"username": "bob", "password": "b"}, follow_redirects=False)
    assert login.status_code == 303

    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 404
