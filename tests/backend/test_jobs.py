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
