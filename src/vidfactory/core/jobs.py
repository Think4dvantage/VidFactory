"""In-process background-job registry with progress state.

Jobs run through a single global FIFO queue -- one worker thread, so exactly one FFmpeg job
executes at a time across the whole app (all users, all projects). This host has one GPU encoder
and is already CPU-contended by other unrelated services, so running builds concurrently just
makes all of them slower; queueing lets someone start several builds at once and walk away.
Submitting a build no longer means it starts immediately -- it means it joins the line. Progress
is polled by the SSE endpoint so the browser (even after a refresh, or on another device) can
follow a running *or queued* job.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    owner_id: int
    project_id: int = 0
    status: str = "pending"  # pending | running | done | error | cancelled
    stage: str = ""
    percent: float = 0.0
    speed: str = ""
    message: str = ""
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def set_progress(self, fraction: float, speed: str = "") -> None:
        self.percent = round(min(max(fraction, 0.0), 1.0) * 100, 1)
        self.speed = speed

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        logger.info("Job [%s] %s — stage: %s", self.kind, self.id, stage)

    def public(self) -> dict:
        elapsed = None
        if self.started_at is not None:
            end = self.finished_at if self.finished_at is not None else time.time()
            elapsed = round(end - self.started_at, 1)
        return {
            "id": self.id,
            "kind": self.kind,
            "project_id": self.project_id,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "speed": self.speed,
            "message": self.message,
            "result": self.result,
            "elapsed_seconds": elapsed,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in ("done", "error", "cancelled")


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[tuple[Job, Callable[[Job], Optional[str]]]]" = queue.Queue()
        threading.Thread(target=self._worker_loop, name="job-queue-worker", daemon=True).start()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_owned(self, job_id: str, owner_id: int) -> Optional[Job]:
        job = self.get(job_id)
        return job if job is not None and job.owner_id == owner_id else None

    def list(self, owner_id: int) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.owner_id == owner_id]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "running")

    def queued_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "pending")

    def position_in_queue(self, job_id: str) -> Optional[int]:
        """How many other non-terminal jobs (queued or currently running) were created before
        this one -- 0 means it's next up (or already running), None if it's already finished.
        The queue is a single global FIFO, not per-user or per-project, so this counts every
        user's jobs: one user queueing several builds delays everyone else's behind all of them.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_terminal:
                return None
            return sum(
                1 for j in self._jobs.values()
                if not j.is_terminal and j.created_at < job.created_at
            )

    def find_active(self, kind: str, project_id: int) -> Optional[Job]:
        """A non-terminal (queued or running) job of `kind` already targeting `project_id`'s
        output, if any.

        Two builds of the same kind for the same project write to the identical output path
        (e.g. summary_output_path() is deterministic per project) — running them concurrently
        interleaves both processes' writes into one corrupted file with no error from either
        side. Callers should check this before starting a new build of that kind. The global
        queue (one job runs at a time) already prevents this for jobs of *different* kinds, but
        two of the *same* kind queued back-to-back would still just redo identical work.
        """
        with self._lock:
            for j in self._jobs.values():
                if j.kind == kind and j.project_id == project_id and not j.is_terminal:
                    return j
        return None

    def run(
        self, kind: str, owner_id: int, target: Callable[[Job], Optional[str]], project_id: int = 0
    ) -> Job:
        """Create a job and enqueue it. Returns immediately -- the job may sit as `pending`
        behind others already queued/running before the single worker thread reaches it."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, owner_id=owner_id, project_id=project_id)
        with self._lock:
            self._jobs[job.id] = job
        logger.info("Job [%s] %s queued (position %s)", kind, job.id, self.position_in_queue(job.id))
        self._queue.put((job, target))
        return job

    def _worker_loop(self) -> None:
        while True:
            job, target = self._queue.get()
            if job.cancel_event.is_set():
                job.status = "cancelled"
                continue
            job.status = "running"
            job.started_at = time.time()
            logger.info("Job [%s] %s starting", job.kind, job.id)
            try:
                result = target(job)
                job.finished_at = time.time()
                if job.cancel_event.is_set():
                    job.status = "cancelled"
                else:
                    # Set result/percent BEFORE status so any concurrent SSE read that sees
                    # "done" also sees 100% + the result (no stuck-at-99.9% race).
                    job.result = result
                    job.percent = 100.0
                    job.status = "done"
                logger.info(
                    "Job [%s] %s %s in %.1fs", job.kind, job.id, job.status,
                    job.finished_at - job.started_at,
                )
            except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                job.finished_at = time.time()
                job.status = "error"
                job.message = str(exc)
                logger.error("Job [%s] %s failed: %s", job.kind, job.id, exc, exc_info=True)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and not job.is_terminal:
            job.cancel_event.set()
            return True
        return False


registry = JobRegistry()
