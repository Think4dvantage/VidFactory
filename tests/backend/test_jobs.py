from __future__ import annotations

import threading
import time

from vidfactory.core.jobs import JobRegistry


def test_find_active_matches_same_kind_and_project():
    reg = JobRegistry()
    started = threading.Event()
    release = threading.Event()

    def target(job):
        started.set()
        release.wait(timeout=2)
        return "done"

    reg.run("summary", owner_id=1, target=target, project_id=42)
    started.wait(timeout=2)

    assert reg.find_active("summary", 42) is not None
    release.set()


def test_find_active_ignores_other_kind_or_project():
    reg = JobRegistry()
    started = threading.Event()
    release = threading.Event()

    def target(job):
        started.set()
        release.wait(timeout=2)
        return "done"

    reg.run("summary", owner_id=1, target=target, project_id=42)
    started.wait(timeout=2)

    assert reg.find_active("fullmusic", 42) is None
    assert reg.find_active("summary", 99) is None
    release.set()


def test_find_active_none_once_job_completes():
    reg = JobRegistry()

    def target(job):
        return "done"

    reg.run("summary", owner_id=1, target=target, project_id=42)
    for _ in range(50):
        if reg.find_active("summary", 42) is None:
            break
        time.sleep(0.02)

    assert reg.find_active("summary", 42) is None


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_jobs_run_one_at_a_time_not_concurrently():
    reg = JobRegistry()
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first(job):
        first_started.set()
        release_first.wait(timeout=2)
        return "a"

    def second(job):
        second_started.set()
        return "b"

    job1 = reg.run("summary", owner_id=1, target=first, project_id=1)
    job2 = reg.run("shorts", owner_id=1, target=second, project_id=2)

    assert first_started.wait(timeout=2)
    assert not second_started.is_set()  # queued behind job1, not running concurrently
    assert job2.status == "pending"

    release_first.set()
    assert second_started.wait(timeout=2)
    assert _wait_for(lambda: job1.status == "done" and job2.status == "done")


def test_position_in_queue_reflects_fifo_order():
    reg = JobRegistry()
    release = threading.Event()

    def blocker(job):
        release.wait(timeout=2)
        return "done"

    def noop(job):
        return "done"

    job1 = reg.run("summary", owner_id=1, target=blocker, project_id=1)
    job2 = reg.run("shorts", owner_id=1, target=noop, project_id=2)
    job3 = reg.run("fullmusic", owner_id=1, target=noop, project_id=3)

    assert _wait_for(lambda: job1.status == "running")
    assert reg.position_in_queue(job1.id) == 0
    assert reg.position_in_queue(job2.id) == 1
    assert reg.position_in_queue(job3.id) == 2

    release.set()
    assert _wait_for(lambda: job3.status == "done")


def test_cancel_while_queued_skips_execution_entirely():
    reg = JobRegistry()
    release = threading.Event()
    ran = []

    def blocker(job):
        release.wait(timeout=2)
        return "done"

    def marks_if_run(job):
        ran.append(True)
        return "done"

    job1 = reg.run("summary", owner_id=1, target=blocker, project_id=1)
    job2 = reg.run("shorts", owner_id=1, target=marks_if_run, project_id=2)

    assert _wait_for(lambda: job1.status == "running")
    assert reg.cancel(job2.id) is True

    release.set()
    assert _wait_for(lambda: job1.status == "done")
    assert _wait_for(lambda: job2.status == "cancelled")
    assert ran == []  # never actually invoked


def test_elapsed_seconds_tracks_duration_and_is_none_while_queued():
    reg = JobRegistry()
    release = threading.Event()

    def blocker(job):
        release.wait(timeout=2)
        return "done"

    def quick(job):
        time.sleep(0.05)
        return "done"

    job1 = reg.run("summary", owner_id=1, target=blocker, project_id=1)
    job2 = reg.run("shorts", owner_id=1, target=quick, project_id=2)

    assert _wait_for(lambda: job1.status == "running")
    assert job2.public()["elapsed_seconds"] is None  # still queued, hasn't started

    release.set()
    assert _wait_for(lambda: job2.status == "done")
    assert job2.public()["elapsed_seconds"] >= 0.05
