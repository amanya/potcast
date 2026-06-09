from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from potcast.models import (
    ChannelConfig,
    DownloadMetadata,
    Episode,
    PodcastConfig,
    RuntimeState,
    StationConfig,
)
from potcast.outputs.base import FakeOutputBackend
from potcast.service import StationService


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
