from __future__ import annotations


def test_jobs_page_requires_login(client):
    resp = client.get("/jobs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_jobs_page_renders(auth_client):
    resp = auth_client.get("/jobs")

    assert resp.status_code == 200
    assert 'id="jobs-body"' in resp.text
    assert "/static/jobs.js" in resp.text


def test_nav_links_to_jobs_page(auth_client):
    resp = auth_client.get("/")

    assert resp.status_code == 200
    assert 'href="/jobs"' in resp.text
