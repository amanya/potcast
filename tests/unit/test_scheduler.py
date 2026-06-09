from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from potcast.scheduler import PeriodicScheduler


class ManualClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def test_scheduler_can_run_one_tick_without_sleeping() -> None:
    calls: list[str] = []
    scheduler = PeriodicScheduler(
        interval=timedelta(minutes=30),
        job=lambda: calls.append("tick"),
        clock=ManualClock(),
    )

    scheduler.run_once()

    assert calls == ["tick"]
    assert scheduler.status().last_run_at == datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
    assert scheduler.status().next_run_at == datetime(2026, 6, 9, 12, 30, tzinfo=timezone.utc)


def test_scheduler_run_due_only_runs_after_next_scheduled_time() -> None:
    calls: list[str] = []
    clock = ManualClock()
    scheduler = PeriodicScheduler(
        interval=timedelta(minutes=30),
        job=lambda: calls.append("tick"),
        clock=clock,
    )

    first = scheduler.run_due()
    early = scheduler.run_due()
    clock.advance(timedelta(minutes=30))
    due = scheduler.run_due()

    assert first is True
    assert early is False
    assert due is True
    assert calls == ["tick", "tick"]


def test_background_tick_logs_job_failure_and_schedules_next_tick(caplog) -> None:  # type: ignore[no-untyped-def]
    clock = ManualClock()

    def fail() -> None:
        raise RuntimeError("feed refresh exploded")

    scheduler = PeriodicScheduler(
        interval=timedelta(minutes=30),
        job=fail,
        clock=clock,
        name="potcast-feed-refresh-scheduler",
    )

    with caplog.at_level(logging.ERROR):
        scheduler._run_once_safely()

    status = scheduler.status()
    assert status.last_run_at == datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
    assert status.next_run_at == datetime(2026, 6, 9, 12, 30, tzinfo=timezone.utc)
    record = next(record for record in caplog.records if record.message == "Scheduled job failed")
    assert record.scheduler_name == "potcast-feed-refresh-scheduler"
    assert record.exc_info is not None
