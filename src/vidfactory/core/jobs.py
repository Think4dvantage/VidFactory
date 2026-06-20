"""In-process background-job registry with progress state.

Long FFmpeg work runs here in a worker thread; progress is polled by the SSE endpoint so the
browser (even after a refresh, or on another device) can follow a running job.
"""

from __future__ import annotations

import logging
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
    status: str = "pending"  # pending | running | done | error | cancelled
    stage: str = ""
    percent: float = 0.0
    speed: str = ""
    message: str = ""
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def set_progress(self, fraction: float, speed: str = "") -> None:
        self.percent = round(min(max(fraction, 0.0), 1.0) * 100, 1)
        self.speed = speed

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        logger.info("Job [%s] %s — stage: %s", self.kind, self.id, stage)

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "speed": self.speed,
            "message": self.message,
            "result": self.result,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in ("done", "error", "cancelled")


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "running")

    def run(self, kind: str, target: Callable[[Job], Optional[str]]) -> Job:
        """Create a job and run `target(job)` in a worker thread. `target` returns a result string."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def _worker() -> None:
            job.status = "running"
            logger.info("Job [%s] %s starting", kind, job.id)
            t0 = time.time()
            try:
                result = target(job)
                if job.cancel_event.is_set():
                    job.status = "cancelled"
                else:
                    # Set result/percent BEFORE status so any concurrent SSE read that sees
                    # "done" also sees 100% + the result (no stuck-at-99.9% race).
                    job.result = result
                    job.percent = 100.0
                    job.status = "done"
                logger.info("Job [%s] %s %s in %.1fs", kind, job.id, job.status, time.time() - t0)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                job.status = "error"
                job.message = str(exc)
                logger.error("Job [%s] %s failed: %s", kind, job.id, exc, exc_info=True)

        threading.Thread(target=_worker, name=f"job-{job.id}", daemon=True).start()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and not job.is_terminal:
            job.cancel_event.set()
            return True
        return False


registry = JobRegistry()
