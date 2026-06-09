"""Periodic background scheduler for feed refresh work."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SchedulerStatus:
    running: bool
    interval_seconds: float
    last_run_at: datetime | None
    next_run_at: datetime | None


class PeriodicScheduler:
    """Run a job periodically while keeping one-tick behavior testable."""

    def __init__(
        self,
        *,
        interval: timedelta,
        job: Callable[[], object],
        clock: Clock | None = None,
    ) -> None:
        if interval.total_seconds() <= 0:
            raise ValueError("Scheduler interval must be greater than zero.")
        self.interval = interval
        self.job = job
        self._clock = clock or _utc_now
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_run_at: datetime | None = None
        self._next_run_at: datetime | None = self._clock()

    def run_due(self) -> bool:
        """Run one tick if the scheduled time has arrived."""

        with self._lock:
            next_run_at = self._next_run_at
            now = self._clock()
            if next_run_at is not None and now < next_run_at:
                return False

        self.run_once()
        return True

    def run_once(self) -> None:
        """Run one job immediately and schedule the next tick."""

        self.job()
        with self._lock:
            now = self._clock()
            self._last_run_at = now
            self._next_run_at = now + self.interval

    def start(self) -> None:
        """Start the background scheduler thread."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="potcast-periodic-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        """Request shutdown and wait for the scheduler thread to exit."""

        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)

    def status(self) -> SchedulerStatus:
        thread = self._thread
        with self._lock:
            return SchedulerStatus(
                running=thread is not None and thread.is_alive(),
                interval_seconds=self.interval.total_seconds(),
                last_run_at=self._last_run_at,
                next_run_at=self._next_run_at,
            )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval.total_seconds())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
