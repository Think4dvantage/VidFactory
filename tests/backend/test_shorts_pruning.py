from __future__ import annotations

from vidfactory.core import projects
from vidfactory.database.models import Project, Short


def test_prune_missing_shorts_removes_missing_and_keeps_existing(db_session, user, tmp_path):
    existing_file = tmp_path / "keep.mp4"
    existing_file.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()

    keep = Short(project_id=project.id, output_file=str(existing_file), short_type="highlight")
    gone = Short(project_id=project.id, output_file=str(tmp_path / "deleted.mp4"), short_type="highlight")
    db_session.add_all([keep, gone])
    db_session.commit()
    gone_id = gone.id

    reloaded = projects.get_owned_project(db_session, project.id, user.id)
    kept = projects.prune_missing_shorts(db_session, reloaded)

    assert [s.id for s in kept] == [keep.id]
    assert db_session.get(Short, gone_id) is None
    assert db_session.get(Short, keep.id) is not None


def test_prune_missing_shorts_no_op_when_all_files_exist(db_session, user, tmp_path):
    existing_file = tmp_path / "keep.mp4"
    existing_file.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(project_id=project.id, output_file=str(existing_file), short_type="highlight"))
    db_session.commit()

    reloaded = projects.get_owned_project(db_session, project.id, user.id)
    kept = projects.prune_missing_shorts(db_session, reloaded)

    assert len(kept) == 1


def test_project_page_omits_and_deletes_shorts_with_missing_files(auth_client, db_session, user, tmp_path):
    existing_file = tmp_path / "keep.mp4"
    existing_file.write_bytes(b"x")

    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="ff.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Short(project_id=project.id, output_file=str(existing_file),
                          short_type="highlight", title="Kept short"))
    gone = Short(project_id=project.id, output_file=str(tmp_path / "gone.mp4"),
                 short_type="highlight", title="Gone short")
    db_session.add(gone)
    db_session.commit()
    gone_id = gone.id

    resp = auth_client.get(f"/projects/{project.id}")

    assert resp.status_code == 200
    assert "keep.mp4" in resp.text
    assert "gone.mp4" not in resp.text
    assert db_session.get(Short, gone_id) is None
