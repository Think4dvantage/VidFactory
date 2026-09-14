from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import vidfactory.api.routers.projects as projects_router
from vidfactory.config import ShortsSection
from vidfactory.core.jobs import Job, registry
from vidfactory.database.models import Base, Hike, Highlight, Project, User


class FakeRunner:
    def __init__(self, durations: dict[str, float]):
        self.durations = durations

    def get_video_info(self, path):
        return (1920, 1080, self.durations[path])


class FakeGpu:
    def encoding_args(self, bitrate):
        return ["-c:v", "libx264"]


class FakeConfig:
    def __init__(self, tmp_path):
        self.shorts = ShortsSection()
        self.ffmpeg = SimpleNamespace(ffmpeg_path="ffmpeg")
        self.data_dir = str(tmp_path)
        self.cta_image = str(tmp_path / "cta.jpg")
        self._shorts_dir = tmp_path / "shorts"

    def mount_roots(self):
        return {"output_shorts": self._shorts_dir}


@pytest.fixture()
def make_shorts_setup(tmp_path, monkeypatch):
    """Factory fixture: build a project (with hike, launch/landing/hook highlights) fresh for
    each test, parametrized on `flight_type` and `launch_start` so no test needs a second DB
    session to mutate state the first session already holds open."""

    def _make(flight_type: str = "hike_and_fly", launch_start: float = 125.0):
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, future=True)
        db = Session()

        user = User(username="pilot", password_hash="x")
        db.add(user)
        db.flush()

        project = Project(
            owner_id=user.id,
            date=datetime.date(2026, 7, 12),
            flight_type=flight_type,
            full_flight_file="/full.mp4",
        )
        db.add(project)
        db.flush()
        db.add(Hike(project_id=project.id, sources=["/hike1.mp4", "/hike2.mp4"], speed_factor=1.0))
        db.add(Highlight(project_id=project.id, name="Ridge soaring", start=200.0, end=210.0,
                          role="normal", make_short=True))
        db.add(Highlight(project_id=project.id, name="launch", start=launch_start,
                          end=launch_start + 5.0, role="launch"))
        db.add(Highlight(project_id=project.id, name="landing", start=395.0, end=400.0, role="landing"))
        db.commit()
        project_id = project.id

        runner = FakeRunner({"/full.mp4": 400.0, "/hike1.mp4": 60.0, "/hike2.mp4": 60.0})
        captured: list[list[tuple[float, float]]] = []

        def fake_build_short(full, clips, out, runner_, gpu, **kwargs):
            captured.append(clips)
            return {"output": out, "duration": sum(e - s for s, e in clips)}

        monkeypatch.setattr(projects_router, "get_engine", lambda: engine)
        monkeypatch.setattr(projects_router, "get_config", lambda: FakeConfig(tmp_path))
        monkeypatch.setattr(projects_router, "get_runner", lambda: runner)
        monkeypatch.setattr(projects_router.gpu_detector, "detect", lambda *_: FakeGpu())
        monkeypatch.setattr(projects_router.shorts_engine, "build_short", fake_build_short)

        job = Job(id="t1", kind="shorts", owner_id=user.id, project_id=project_id)
        return project_id, user.id, job, captured

    return _make


def test_hike_and_fly_short_composition_order(make_shorts_setup):
    project_id, owner_id, job, captured = make_shorts_setup()

    target = projects_router._short_target(
        project_id, owner_id, "highlight", "", 0.35, 1.0, (200.0, 206.0), "Ridge soaring", None, {},
    )
    target(job)

    assert len(captured) == 1
    clips = captured[0]
    assert len(clips) == 1 + 2 + 1 + 3 + 1  # hook, 2 hike, launch, 3 flying, landing

    hook, hike1, hike2, launch, fly1, fly2, fly3, landing = clips

    assert hook == (200.0, 206.0)
    assert launch == (125.0, 129.0)
    assert landing == (395.0, 399.0)

    # hike clips come from the prepended hike segment [0, 120), spread across its two halves
    for s, e in (hike1, hike2):
        assert 0.0 <= s < 120.0 and e <= 120.0
    assert hike1[0] < 60.0 <= hike2[0]

    # flying clips come from [120, 400) only — never overlapping the hike segment — and are
    # spread across the three roughly-equal thirds of the flying time
    for s, e in (fly1, fly2, fly3):
        assert s >= 120.0 and e <= 400.0
    assert fly1[0] < 213.34
    assert 213.33 <= fly2[0] < 306.67
    assert fly3[0] >= 306.66


def test_normal_flight_short_has_no_hike_clips(make_shorts_setup):
    project_id, owner_id, job, captured = make_shorts_setup(flight_type="normal_flight")

    target = projects_router._short_target(
        project_id, owner_id, "highlight", "", 0.35, 1.0, (200.0, 206.0), "Ridge soaring", None, {},
    )
    target(job)

    clips = captured[0]
    assert len(clips) == 1 + 1 + 3 + 1  # hook, launch, 3 flying, landing — no hike parts
    hook, launch, fly1, fly2, fly3, landing = clips
    assert hook == (200.0, 206.0)
    assert launch == (125.0, 129.0)
    assert landing == (395.0, 399.0)
    # with no hike to exclude, the flying pool covers the whole full-flight duration
    for s, e in (fly1, fly2, fly3):
        assert 0.0 <= s and e <= 400.0


def test_short_built_from_the_launch_highlight_drops_the_duplicate_launch_clip(make_shorts_setup):
    # Building a short *from* the launch highlight (hid = that highlight's own id) puts it in as
    # the hook already -- separately re-adding the "launch" role clip would play the same footage
    # twice in a row (nothing else sits between the hook and where the role clip would land).
    project_id, owner_id, job, captured = make_shorts_setup(flight_type="normal_flight")

    Session_ = sessionmaker(bind=projects_router.get_engine(), autoflush=False, future=True)
    with Session_() as db:
        launch_id = db.query(Highlight).filter_by(project_id=project_id, role="launch").one().id

    target = projects_router._short_target(
        project_id, owner_id, "highlight", "", 0.35, 1.0, (125.0, 130.0), "launch", launch_id, {},
    )
    target(job)

    clips = captured[0]
    assert len(clips) == 1 + 3 + 1  # hook(=launch), 3 flying, landing -- no separate launch clip
    hook, fly1, fly2, fly3, landing = clips
    assert hook == (125.0, 130.0)
    assert landing == (395.0, 399.0)


def test_short_built_from_the_landing_highlight_keeps_both_landing_clips(make_shorts_setup):
    # Unlike launch, a short built from the landing highlight is expected to keep both plays --
    # the hook copy at the very start and the role clip at the very end, with flying in between,
    # so it doesn't read as an accidental back-to-back repeat.
    project_id, owner_id, job, captured = make_shorts_setup(flight_type="normal_flight")

    Session_ = sessionmaker(bind=projects_router.get_engine(), autoflush=False, future=True)
    with Session_() as db:
        landing_id = db.query(Highlight).filter_by(project_id=project_id, role="landing").one().id

    target = projects_router._short_target(
        project_id, owner_id, "highlight", "", 0.35, 1.0, (395.0, 400.0), "landing", landing_id, {},
    )
    target(job)

    clips = captured[0]
    assert len(clips) == 1 + 1 + 3 + 1  # hook(=landing), launch, 3 flying, landing (again)
    hook, launch, fly1, fly2, fly3, landing_clip = clips
    assert hook == (395.0, 400.0)
    assert launch == (125.0, 129.0)
    assert landing_clip == (395.0, 399.0)


def test_launch_inside_hike_segment_logs_a_warning(make_shorts_setup, caplog):
    # launch marked at 50s, well before the 120s hike/flying boundary — a mis-marked highlight,
    # not something we silently clamp, but it should be loud so a future report is diagnosable.
    project_id, owner_id, job, captured = make_shorts_setup(launch_start=50.0)

    target = projects_router._short_target(
        project_id, owner_id, "highlight", "", 0.35, 1.0, (200.0, 206.0), "Ridge soaring", None, {},
    )
    with caplog.at_level("WARNING"):
        target(job)

    assert any("launch highlight starts" in r.message for r in caplog.records)


# --- api/routers/projects.py:build_shorts_ep -- one queued job per short, not one job total ---


def test_build_shorts_highlight_mode_queues_one_job_per_flagged_highlight(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="/full.mp4")
    db_session.add(project)
    db_session.commit()
    db_session.add(Highlight(project_id=project.id, name="Takeoff", start=10.0, end=20.0, make_short=True))
    db_session.add(Highlight(project_id=project.id, name="Thermal", start=100.0, end=110.0, make_short=True))
    db_session.add(Highlight(project_id=project.id, name="Scenery", start=200.0, end=210.0, make_short=False))
    db_session.commit()

    resp = auth_client.post(f"/api/projects/{project.id}/shorts/build", data={"mode": "highlight"})
    assert resp.status_code == 200
    job_ids = resp.json()["job_ids"]
    assert len(job_ids) == 2  # one per make_short highlight, not one job for the whole batch

    jobs = [registry.get(jid) for jid in job_ids]
    assert all(j is not None and j.kind == "shorts" and j.project_id == project.id for j in jobs)
    assert {j.title for j in jobs} == {"Takeoff", "Thermal"}  # queue can show which short is which


def test_build_shorts_random_mode_queues_count_jobs(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="/full.mp4")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.post(
        f"/api/projects/{project.id}/shorts/build", data={"mode": "random", "count": "4"},
    )
    assert resp.status_code == 200
    job_ids = resp.json()["job_ids"]
    assert len(job_ids) == 4
    assert len({registry.get(jid).id for jid in job_ids}) == 4  # 4 distinct jobs, not one reused


def test_build_shorts_highlight_mode_400s_when_none_flagged(auth_client, db_session, user):
    project = Project(owner_id=user.id, flight_type="normal_flight", full_flight_file="/full.mp4")
    db_session.add(project)
    db_session.commit()

    resp = auth_client.post(f"/api/projects/{project.id}/shorts/build", data={"mode": "highlight"})
    assert resp.status_code == 400
