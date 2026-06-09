from __future__ import annotations

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
