from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from potcast.models import (
    ChannelConfig,
    DownloadMetadata,
    Episode,
    OutputError,
    PodcastConfig,
    RuntimeState,
    StationConfig,
)
from potcast.outputs.base import FakeOutputBackend
from potcast.service import OutputRetryPolicy, StationService


class MemoryStateStore:
    def __init__(
        self,
        *,
        state: RuntimeState | None = None,
        downloads: dict[str, DownloadMetadata] | None = None,
    ) -> None:
        self.state = state or RuntimeState(volume=70)
        self.downloads = downloads or {}
        self.saved_states: list[RuntimeState] = []

    def load_runtime_state(self) -> RuntimeState:
        return self.state

    def save_runtime_state(self, state: RuntimeState) -> None:
        self.state = state
        self.saved_states.append(state)

    def load_download_metadata(self) -> dict[str, DownloadMetadata]:
        return self.downloads


def podcast(podcast_id: str) -> PodcastConfig:
    return PodcastConfig(
        id=podcast_id,
        name=podcast_id.replace("-", " ").title(),
        feed_url=f"https://example.com/{podcast_id}.xml",
    )


def channel(channel_id: str, podcast_ids: tuple[str, ...]) -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name=channel_id.title(),
        podcasts=tuple(podcast(podcast_id) for podcast_id in podcast_ids),
    )


def download(podcast_id: str) -> DownloadMetadata:
    return DownloadMetadata(
        podcast_id=podcast_id,
        episode_identity=f"{podcast_id}-episode",
        media_url=f"https://example.com/{podcast_id}.mp3",
        media_type="audio/mpeg",
        local_file=Path(f"/data/episodes/{podcast_id}/{podcast_id}.mp3"),
        downloaded_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        title=f"{podcast_id} episode",
    )


def downloads(*podcast_ids: str) -> dict[str, DownloadMetadata]:
    return {podcast_id: download(podcast_id) for podcast_id in podcast_ids}


def make_service(
    store: MemoryStateStore,
    output: FakeOutputBackend | None = None,
    *,
    retry_policy: OutputRetryPolicy | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[StationService, FakeOutputBackend]:
    backend = output or FakeOutputBackend(volume=70)
    service = StationService(
        (
            channel("sleep", ("history-extra", "science-hour", "short-fiction")),
            channel("stories", ("myths", "interviews")),
        ),
        StationConfig(shuffle_podcasts=False, volume=70),
        store,
        backend,
        retry_policy=retry_policy,
        clock=clock,
    )
    return service, backend


def test_play_is_idempotent() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    first = service.play()
    second = service.play()

    assert first.ok is True
    assert second.ok is True
    assert store.state.station_status == "playing"
    assert output.calls == [("play_episode", "history-extra-episode")]


def test_pause_is_idempotent() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    service.pause()
    service.pause()

    assert store.state.station_status == "paused"
    assert output.calls == [("pause", None)]


def test_stop_is_idempotent() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    service.stop()
    service.stop()

    assert store.state.station_status == "stopped"
    assert output.calls == [("stop", None)]


def test_stop_clears_persisted_supervisor_error() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="stopped",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
            playback_supervisor_error=output_error(),
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    result = service.stop()

    assert result.ok is True
    assert store.state.station_status == "stopped"
    assert store.state.playback_supervisor_error is None
    assert result.status.playback_supervisor.state == "idle"
    assert output.calls == []


def test_next_changes_podcast_and_calls_output_backend() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(store)

    result = service.next()

    assert result.ok is True
    assert store.state.current_podcast_id == "science-hour"
    assert output.calls == [("play_episode", "science-hour-episode")]


def test_advance_if_finished_moves_to_next_playable_podcast() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.finish_current_episode()

    result = service.advance_if_finished()

    assert result.ok is True
    assert store.state.station_status == "playing"
    assert store.state.current_podcast_id == "science-hour"
    assert output.calls == [("play_episode", "science-hour-episode")]


def test_startup_failure_surfaces_output_error_and_does_not_mark_station_playing() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra"),
    )
    output = FakeOutputBackend(volume=70)
    service, output = make_service(store, output)

    def fail_playback(episode: Episode) -> None:
        output.calls.append(("play_episode", episode.identity))
        output.fail("backend_start_failed", "ffmpeg missing")

    output.play_episode = fail_playback  # type: ignore[method-assign]

    result = service.play()

    assert result.ok is True
    assert store.state.station_status == "idle"
    assert store.state.playback_supervisor_error is not None
    assert store.state.playback_supervisor_error.code == "backend_start_failed"
    assert result.status.playback_supervisor.state == "retry_scheduled"
    assert result.status.playback_supervisor.last_error is not None
    assert result.status.playback_supervisor.last_error.code == "backend_start_failed"
    assert result.status.output.state == "error"
    assert result.status.output.error is not None
    assert result.status.output.error.code == "backend_start_failed"
    assert output.calls == [("play_episode", "history-extra-episode")]


def test_unexpected_process_exit_surfaces_output_error_without_advancing() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")

    result = service.advance_if_finished()

    assert result.ok is True
    assert store.state.station_status == "idle"
    assert store.state.current_podcast_id == "history-extra"
    assert store.state.playback_supervisor_error is not None
    assert store.state.playback_supervisor_error.code == "backend_process_failed"
    assert result.status.playback_supervisor.state == "retry_scheduled"
    assert result.status.output.state == "error"
    assert result.status.output.error is not None
    assert result.status.output.error.code == "backend_process_failed"
    assert output.calls == []


def test_output_failure_schedules_bounded_retry_without_immediate_relaunch() -> None:
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(
        store,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")

    failed = service.advance_if_finished()
    not_due = service.advance_if_finished()

    assert failed.status.playback_supervisor.state == "retry_scheduled"
    assert failed.status.playback_supervisor.next_retry_at == datetime(
        2026, 6, 9, 12, 0, 5, tzinfo=timezone.utc
    )
    assert failed.status.playback_supervisor.retry_attempts == 0
    assert failed.status.playback_supervisor.max_retry_attempts == 1
    assert not_due.status.playback_supervisor.next_retry_at == datetime(
        2026, 6, 9, 12, 0, 5, tzinfo=timezone.utc
    )
    assert output.calls == []


def test_output_failure_logs_block_and_scheduled_retry(caplog) -> None:  # type: ignore[no-untyped-def]
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(
        store,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")

    with caplog.at_level(logging.INFO, logger="potcast.service"):
        service.advance_if_finished()

    scheduled = _log_record(caplog.records, "Scheduled output playback retry")
    blocked = _log_record(caplog.records, "Output playback failed; station blocked")
    assert scheduled.error_code == "backend_process_failed"
    assert scheduled.next_retry_at == "2026-06-09T12:00:05+00:00"
    assert scheduled.retry_attempt == 1
    assert blocked.error_code == "backend_process_failed"
    assert blocked.station_state == "idle"
    assert blocked.podcast_id == "history-extra"
    assert blocked.episode_identity == "history-extra-episode"


def test_output_retry_runs_once_when_due() -> None:
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(
        store,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    service.advance_if_finished()
    clock.advance(timedelta(seconds=5))

    result = service.advance_if_finished()

    assert result.ok is True
    assert store.state.station_status == "playing"
    assert store.state.playback_supervisor_error is None
    assert result.status.playback_supervisor.state == "watching"
    assert result.status.playback_supervisor.next_retry_at is None
    assert output.calls == [
        ("stop", None),
        ("play_episode", "history-extra-episode"),
    ]


def test_output_retry_logs_attempt_and_success(caplog) -> None:  # type: ignore[no-untyped-def]
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(
        store,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    service.advance_if_finished()
    clock.advance(timedelta(seconds=5))
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="potcast.service"):
        service.advance_if_finished()

    attempt = _log_record(caplog.records, "Retrying output playback")
    success = _log_record(caplog.records, "Output playback retry succeeded")
    assert attempt.retry_attempt == 1
    assert attempt.error_code == "backend_process_failed"
    assert success.retry_attempt == 1
    assert success.station_state == "playing"


def test_output_retry_is_not_rescheduled_after_policy_attempts_are_exhausted() -> None:
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    output = FakeOutputBackend(volume=70)
    service, output = make_service(
        store,
        output,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    service.advance_if_finished()

    def fail_playback(episode: Episode) -> None:
        output.calls.append(("play_episode", episode.identity))
        output.fail("backend_start_failed", "ffmpeg missing")

    output.play_episode = fail_playback  # type: ignore[method-assign]
    clock.advance(timedelta(seconds=5))

    first_retry = service.advance_if_finished()
    second_tick = service.advance_if_finished()

    assert first_retry.status.playback_supervisor.state == "exhausted"
    assert first_retry.status.playback_supervisor.next_retry_at is None
    assert first_retry.status.playback_supervisor.retry_attempts == 1
    assert second_tick.status.playback_supervisor.retry_attempts == 1
    assert output.calls == [
        ("stop", None),
        ("play_episode", "history-extra-episode"),
    ]


def test_output_retry_exhaustion_is_logged(caplog) -> None:  # type: ignore[no-untyped-def]
    clock = MutableClock(datetime(2026, 6, 9, 12, tzinfo=timezone.utc))
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    output = FakeOutputBackend(volume=70)
    service, output = make_service(
        store,
        output,
        retry_policy=OutputRetryPolicy(max_attempts=1, initial_delay=timedelta(seconds=5)),
        clock=clock,
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    service.advance_if_finished()

    def fail_playback(episode: Episode) -> None:
        output.calls.append(("play_episode", episode.identity))
        output.fail("backend_start_failed", "ffmpeg missing")

    output.play_episode = fail_playback  # type: ignore[method-assign]
    clock.advance(timedelta(seconds=5))
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="potcast.service"):
        service.advance_if_finished()

    exhausted = _log_record(caplog.records, "Output playback retry policy exhausted")
    assert exhausted.error_code == "backend_start_failed"
    assert exhausted.retry_attempts == 1
    assert exhausted.max_retry_attempts == 1


def test_persisted_supervisor_error_without_retry_window_reports_blocked() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="idle",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
            playback_supervisor_error=output_error(),
        ),
        downloads=downloads("history-extra"),
    )
    service, _output = make_service(store)

    status = service.status()

    assert status.playback_supervisor.state == "blocked"
    assert status.playback_supervisor.last_error is not None
    assert status.playback_supervisor.last_error.code == "backend_process_failed"


def test_supervisor_reports_exhausted_when_retry_policy_has_no_attempts() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(
        store,
        retry_policy=OutputRetryPolicy(max_attempts=0),
    )
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")

    result = service.advance_if_finished()

    assert result.status.playback_supervisor.state == "exhausted"
    assert result.status.playback_supervisor.next_retry_at is None
    assert result.status.playback_supervisor.retry_attempts == 0
    assert result.status.playback_supervisor.max_retry_attempts == 0


def test_failing_episode_is_not_relaunched_by_repeated_supervisor_ticks() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")

    first = service.advance_if_finished()
    second = service.advance_if_finished()

    assert first.ok is True
    assert second.ok is True
    assert store.state.station_status == "idle"
    assert store.state.current_podcast_id == "history-extra"
    assert output.calls == []


def test_recover_output_retries_selected_episode_after_output_error() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="idle",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    service, output = make_service(store)
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    output.calls.clear()

    result = service.recover_output()

    assert result.ok is True
    assert store.state.station_status == "playing"
    assert store.state.current_podcast_id == "history-extra"
    assert store.state.playback_supervisor_error is None
    assert result.status.playback_supervisor.state == "watching"
    assert result.status.output.state == "playing"
    assert result.status.output.error is None
    assert output.calls == [
        ("stop", None),
        ("play_episode", "history-extra-episode"),
    ]


def test_recover_output_logs_manual_recovery(caplog) -> None:  # type: ignore[no-untyped-def]
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="idle",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)
    output.fail("backend_process_failed", "Output process exited unexpectedly with code 1.")
    output.calls.clear()

    with caplog.at_level(logging.INFO, logger="potcast.service"):
        service.recover_output()

    recovering = _log_record(caplog.records, "Manually recovering output playback")
    recovered = _log_record(caplog.records, "Manual output recovery succeeded")
    assert recovering.podcast_id == "history-extra"
    assert recovered.station_state == "playing"
    assert recovered.episode_identity == "history-extra-episode"


def test_recover_output_returns_structured_error_when_retry_fails() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="idle",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
            playback_supervisor_error=output_error(),
        ),
        downloads=downloads("history-extra"),
    )
    output = FakeOutputBackend(volume=70)
    service, output = make_service(store, output)

    def fail_playback(episode: Episode) -> None:
        output.calls.append(("play_episode", episode.identity))
        output.fail("backend_start_failed", "ffmpeg missing")

    output.play_episode = fail_playback  # type: ignore[method-assign]

    result = service.recover_output()

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "output_recovery_failed"
    assert result.error.message == "Output recovery failed: ffmpeg missing"
    assert store.state.playback_supervisor_error is not None
    assert store.state.playback_supervisor_error.code == "backend_start_failed"
    assert result.status.playback_supervisor.state == "retry_scheduled"
    assert output.calls == [
        ("stop", None),
        ("play_episode", "history-extra-episode"),
    ]


def test_recover_output_retries_persisted_supervisor_error_after_backend_restart() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="idle",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
            playback_supervisor_error=output_error(),
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    result = service.recover_output()

    assert result.ok is True
    assert store.state.station_status == "playing"
    assert store.state.playback_supervisor_error is None
    assert result.status.playback_supervisor.state == "watching"
    assert output.calls == [
        ("stop", None),
        ("play_episode", "history-extra-episode"),
    ]


def test_recover_output_is_idempotent_without_output_error() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()

    result = service.recover_output()

    assert result.ok is True
    assert store.state.station_status == "playing"
    assert output.calls == []


def test_advance_if_finished_idles_when_no_playable_podcast_is_available() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads={},
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))
    output.calls.clear()
    output.finish_current_episode()

    result = service.advance_if_finished()

    assert result.ok is True
    assert store.state.station_status == "idle"
    assert store.state.current_podcast_id is None
    assert output.calls == [("stop", None)]


def test_advance_if_finished_does_not_advance_when_paused_or_stopped() -> None:
    paused_store = MemoryStateStore(
        state=RuntimeState(
            station_status="paused",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    paused_service, paused_output = make_service(paused_store)
    paused_output.play_episode(_episode_identity_only("history-extra-episode"))
    paused_output.calls.clear()
    paused_output.finish_current_episode()

    stopped_store = MemoryStateStore(
        state=RuntimeState(
            station_status="stopped",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra", "science-hour"),
    )
    stopped_service, stopped_output = make_service(stopped_store)
    stopped_output.play_episode(_episode_identity_only("history-extra-episode"))
    stopped_output.calls.clear()
    stopped_output.finish_current_episode()

    paused_service.advance_if_finished()
    stopped_service.advance_if_finished()

    assert paused_store.state.current_podcast_id == "history-extra"
    assert stopped_store.state.current_podcast_id == "history-extra"
    assert paused_output.calls == []
    assert stopped_output.calls == []


def test_previous_uses_selector_behavior_and_calls_output_backend_when_playable() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep",
            current_podcast_id="short-fiction",
            previous_podcast_ids=("history-extra",),
            volume=70,
        ),
        downloads=downloads("history-extra", "short-fiction"),
    )
    service, output = make_service(store)

    result = service.previous()

    assert result.ok is True
    assert store.state.current_podcast_id == "history-extra"
    assert output.calls == [("play_episode", "history-extra-episode")]


def test_channel_changes_update_state_and_call_output_backend() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra", "myths"),
    )
    service, output = make_service(store)

    result = service.next_channel()

    assert result.ok is True
    assert store.state.current_channel_id == "stories"
    assert store.state.current_podcast_id == "myths"
    assert output.calls == [("play_episode", "myths-episode")]


def test_select_channel_handles_known_and_unknown_channels_with_structured_errors() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra", "myths"),
    )
    service, output = make_service(store)

    known = service.select_channel("stories")
    unknown = service.select_channel("bedtime")

    assert known.ok is True
    assert store.state.current_channel_id == "stories"
    assert store.state.current_podcast_id == "myths"
    assert output.calls == [("play_episode", "myths-episode")]
    assert unknown.ok is False
    assert unknown.error is not None
    assert unknown.error.code == "unknown_channel"
    assert unknown.error.message == "Channel not found: bedtime"


def test_select_podcast_handles_known_and_unavailable_podcasts_with_structured_errors() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra", "short-fiction"),
    )
    service, output = make_service(store)

    known = service.select_podcast("short-fiction")
    unavailable = service.select_podcast("science-hour")

    assert known.ok is True
    assert store.state.current_podcast_id == "short-fiction"
    assert output.calls == [("play_episode", "short-fiction-episode")]
    assert unavailable.ok is False
    assert unavailable.error is not None
    assert unavailable.error.code == "podcast_unavailable"


def test_set_volume_updates_runtime_state_and_output_backend() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            current_channel_id="sleep", current_podcast_id="history-extra", volume=70
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)

    result = service.set_volume(42)

    assert result.ok is True
    assert store.state.volume == 42
    assert output.status().volume == 42
    assert output.calls == [("set_volume", 42)]


def test_status_includes_active_station_and_output_fields() -> None:
    store = MemoryStateStore(
        state=RuntimeState(
            station_status="playing",
            current_channel_id="sleep",
            current_podcast_id="history-extra",
            volume=70,
        ),
        downloads=downloads("history-extra"),
    )
    service, output = make_service(store)
    output.play_episode(_episode_identity_only("history-extra-episode"))

    status = service.status()

    assert status.station_state == "playing"
    assert status.active_channel is not None
    assert status.active_channel.id == "sleep"
    assert status.active_podcast is not None
    assert status.active_podcast.id == "history-extra"
    assert status.active_episode is not None
    assert status.active_episode.identity == "history-extra-episode"
    assert status.volume == 70
    assert status.output.current_episode_identity == "history-extra-episode"
    assert status.playback_supervisor.state == "watching"
    assert status.playback_supervisor.last_error is None


def _episode_identity_only(identity: str) -> Episode:
    return Episode(
        title=identity,
        identity=identity,
        guid=None,
        published_at=None,
        media_url=f"https://example.com/{identity}.mp3",
        media_type="audio/mpeg",
        local_file=Path(f"/data/{identity}.mp3"),
    )


def output_error() -> OutputError:
    return OutputError(
        code="backend_process_failed",
        message="Output process exited unexpectedly with code 1.",
    )


def _log_record(records: list[logging.LogRecord], message: str) -> logging.LogRecord:
    for record in records:
        if record.getMessage() == message:
            return record
    raise AssertionError(f"Log record not found: {message}")


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta
