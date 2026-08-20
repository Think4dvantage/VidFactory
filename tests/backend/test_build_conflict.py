from __future__ import annotations

import threading

from vidfactory.core.jobs import registry
from vidfactory.database.models import Project


def test_second_summary_build_409s_while_first_is_running(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    release = threading.Event()

    def blocking_target(job):
        release.wait(timeout=2)
        return "ff.mp4"

    registry.run("summary", owner_id=user.id, target=blocking_target, project_id=project.id)

    resp = auth_client.post(f"/api/projects/{project.id}/summary/build", data={})

    release.set()

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"
